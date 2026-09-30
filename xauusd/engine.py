"""Motor vela a vela: detecta setups, puntúa, abre, gestiona y registra operaciones.

Flujo por cada vela de ejecución t:
  1. actualizar estructura, Wyckoff y puntos de interés con la vela t;
  2. gestionar posiciones abiertas (stop antes que objetivo, por prudencia);
  3. intentar llenar órdenes límite pendientes;
  4. si la vela t produce un BOS/CHoCH, preparar un setup nuevo (orden límite
     en el POI), que solo puede llenarse a partir de la vela t+1.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import wyckoff as wk
from .config import Config
from .data import resample
from .indicators import enrich, in_killzone
from .poi import FVG, OrderBlock, find_fvgs, find_order_block
from .risk import RiskManager
from .scoring import max_score, risk_fraction, score, threshold
from .structure import StructureTracker, trend_series


@dataclass
class Setup:
    t: int
    system: str
    direction: int
    event: object
    entry: float
    sl: float
    tp1: float
    rr: float
    poi_kind: str
    ob: OrderBlock
    fvg: FVG | None
    leg_origin: int
    leg_extreme: float
    fib: float
    base_checks: dict
    wyckoff_tests: dict
    range_: object


@dataclass
class Trade:
    setup: Setup
    system: str
    direction: int
    t_entry: int
    entry: float
    sl: float
    tp1: float
    units: float
    risk_frac: float
    points: float
    checks: dict
    stop: float = 0.0
    tp1_hit: bool = False
    t_exit: int | None = None
    exit_price: float | None = None
    pnl: float = 0.0
    realised: float = 0.0
    remaining: float = 1.0
    exit_reason: str = ""
    max_r: float = 0.0

    @property
    def open_risk(self) -> float:
        return 0.0 if self.tp1_hit else self.risk_frac


@dataclass
class Result:
    exec_df: pd.DataFrame
    trades: list
    rejected: list
    equity: pd.Series
    structure: StructureTracker
    wyckoff: wk.WyckoffTracker
    cfg: Config
    halted: bool = False


def _htf_to_exec(trend: pd.Series, tf: str, exec_close: pd.DatetimeIndex) -> np.ndarray:
    """Tendencia de temporalidad alta conocida al cierre de cada vela de ejecución."""
    s = trend.copy()
    s.index = s.index + pd.Timedelta(tf)
    return s.reindex(exec_close, method="ffill").fillna(0).astype(int).to_numpy()


class Engine:
    def __init__(self, df: pd.DataFrame, cfg: Config | None = None):
        self.cfg = cfg = cfg or Config()
        self.x = enrich(resample(df, cfg.tf_exec), cfg)
        bias = enrich(resample(df, cfg.tf_bias), cfg)
        ctx = enrich(resample(df, cfg.tf_context), cfg)
        close_t = self.x.index + pd.Timedelta(cfg.tf_exec)
        self.bias = _htf_to_exec(trend_series(bias, cfg), cfg.tf_bias, close_t)
        self.ctx = _htf_to_exec(trend_series(ctx, cfg), cfg.tf_context, close_t)
        self.st = StructureTracker(self.x, cfg)
        self.wy = wk.WyckoffTracker(self.x, cfg)
        self.rm = RiskManager(cfg)
        self.o = self.x["open"].to_numpy()
        self.h = self.x["high"].to_numpy()
        self.l = self.x["low"].to_numpy()
        self.c = self.x["close"].to_numpy()
        self.atr = self.x["atr"].to_numpy()
        self.rsi = self.x["rsi"].to_numpy()
        self.rvol = self.x["rvol"].to_numpy()
        self.pending: dict[str, Setup] = {}
        self.open: list[Trade] = []
        self.closed: list[Trade] = []
        self.rejected: list[dict] = []

    # ------------------------------------------------------------------
    def run(self) -> Result:
        eq = np.empty(len(self.x))
        for t in range(len(self.x)):
            ts = self.x.index[t]
            self.rm.new_bar(ts)
            events = self.st.step(t)
            self.wy.step(t)
            for s in self.pending.values():
                if t > s.t:
                    s.ob.update(self.h[t], self.l[t], self.c[t])
            self._manage(t)
            self._fill(t)
            for ev in events:
                self._new_setup(ev, t)
            eq[t] = self.rm.equity + sum(self._mtm(tr, self.c[t]) for tr in self.open)
        # Cierre forzoso al final de los datos
        for tr in list(self.open):
            self._close(tr, len(self.x) - 1, self.c[-1], "fin de datos")
        return Result(self.x, self.closed, self.rejected, pd.Series(eq, index=self.x.index),
                      self.st, self.wy, self.cfg, self.rm.halted)

    def _reject(self, t: int, why: str, **kw):
        self.rejected.append({"time": self.x.index[t], "motivo": why, **kw})

    # ------------------------------------------------------------------
    def _new_setup(self, ev, t: int):
        cfg = self.cfg
        d = ev.direction
        if np.isnan(self.atr[t]):
            return
        system = "A" if self.bias[t] == d else "B"
        r = self.wy.context(t)
        if wk.contradicts(r, d, t):
            self._reject(t, "Smart Money contradice la fase Wyckoff", direction=d, system=system)
            return
        origin = ev.origin_idx
        ext = self.h[origin:t + 1].max() if d > 0 else self.l[origin:t + 1].min()
        leg0 = self.l[origin] if d > 0 else self.h[origin]
        ob = find_order_block(self.x, origin, t, d)
        fvgs = [f for f in find_fvgs(self.x, origin, t, d, cfg)]

        def retr(p):  # retroceso Fibonacci del precio p dentro del tramo (0 = extremo, 1 = origen)
            return (ext - p) / (ext - leg0) if ext != leg0 else 0.0

        lo_z, hi_z = cfg.fib_zone[0], cfg.fib_zone_deep[1]
        cands = [("FVG 50%", f.mid, f) for f in fvgs] + [("Order block", ob.proximal, None)]
        in_zone = [c for c in cands if lo_z <= retr(c[1]) < hi_z]
        poi_kind, entry, fvg = (in_zone or cands[-1:])[0]
        sl = leg0 - d * cfg.sl_buffer_atr * self.atr[t]
        tp1 = self.st.external_liquidity(t, d, ext)
        if tp1 is None:
            self._reject(t, "sin liquidez externa visible como objetivo", direction=d, system=system)
            return
        risk_pts = (entry - sl) * d
        if risk_pts <= 0:
            return
        rr = (tp1 - entry) * d / risk_pts
        if rr < cfg.min_rr:
            self._reject(t, f"RR {rr:.2f} < 1:{cfg.min_rr:g}", direction=d, system=system)
            return
        # Comprobaciones conocidas al crear el setup
        want = "acumulacion" if d > 0 else "distribucion"
        ph, ph_clear = wk.phase(r, d, t, self.st.trend, self.st.bos_count)
        base = {
            "spring_upthrust": wk.shakeout_ok(r, d, t),
            "bos_displacement": bool(ev.displacement),
            "wyckoff_phase": ph_clear,
            "liquidity_swept": bool(ev.swept_before),
            "mtf_alignment": bool(self.bias[t] == d and self.ctx[t] == d),
            "fib_confluence": lo_z <= retr(entry) < hi_z,
            "rsi_volume": self._rsi_div(origin, d, t) or self.rvol[t] >= cfg.vol_high,
            "_phase": ph,
        }
        tests = wk.nine_tests(r, d, t, self.st, self.x, cfg)
        if system == "B":
            ok = sum(1 for v in tests.values() if v)
            if ok < cfg.wyckoff_tests_min_b:
                self._reject(t, f"Sistema B: {ok} pruebas Wyckoff < {cfg.wyckoff_tests_min_b}",
                             direction=d, system=system)
                return
        self.pending[system] = Setup(t, system, d, ev, entry, sl, tp1, rr, poi_kind, ob, fvg,
                                     origin, ext, retr(entry), base, tests, r)

    def _rsi_div(self, origin: int, d: int, t: int) -> bool:
        """Divergencia regular en el extremo que originó el impulso."""
        pool = self.st.lows if d > 0 else self.st.highs
        prev = [s for s in pool if s.idx < origin - self.cfg.swing_len and s.confirm <= t]
        if not prev:
            return False
        p = prev[-1]
        if d > 0:
            return self.l[origin] < p.price and self.rsi[origin] > self.rsi[p.idx]
        return self.h[origin] > p.price and self.rsi[origin] < self.rsi[p.idx]

    # ------------------------------------------------------------------
    def _fill(self, t: int):
        cfg = self.cfg
        for system, s in list(self.pending.items()):
            if t <= s.t:
                continue
            d = s.direction
            if t - s.t > cfg.poi_max_age_bars:
                del self.pending[system]
                self._reject(t, "setup caducado sin retorno al POI", direction=d, system=system)
                continue
            hit_tp = self.h[t] >= s.tp1 if d > 0 else self.l[t] <= s.tp1
            touched = self.l[t] <= s.entry if d > 0 else self.h[t] >= s.entry
            if s.ob.mitigated or (not touched and hit_tp):
                del self.pending[system]
                self._reject(t, "POI mitigado u objetivo alcanzado sin entrada", direction=d, system=system)
                continue
            if not touched:
                continue
            price = min(s.entry, self.o[t]) if d > 0 else max(s.entry, self.o[t])
            if (price - s.sl) * d <= 0:
                del self.pending[system]
                continue
            del self.pending[system]
            checks = dict(s.base_checks)
            checks["sos_sow"] = wk.strength_ok(s.range_, d, t)
            prior_touches = s.ob.touches - (1 if s.ob._inside else 0)
            checks["poi_return"] = prior_touches < cfg.ob_max_touches and not s.ob.mitigated
            checks["killzone"] = in_killzone(self.x.index[t], cfg)
            pts = score(checks, cfg)
            if pts < threshold(system, cfg):
                self._reject(t, f"puntaje {pts:g}/{max_score(cfg):g} < umbral {threshold(system, cfg):g}",
                             direction=d, system=system, points=pts)
                continue
            risk = risk_fraction(pts, cfg)
            why = self.rm.can_open(system, d, risk, self.open, checks["mtf_alignment"])
            if why:
                self._reject(t, why, direction=d, system=system, points=pts)
                continue
            units = self.rm.equity * risk / ((price - s.sl) * d)
            tr = Trade(s, system, d, t, price, s.sl, s.tp1, units, risk, pts, checks, stop=s.sl)
            self.rm.on_open()
            self.open.append(tr)
            # La misma vela de entrada puede tocar el stop
            if (self.l[t] <= tr.stop) if d > 0 else (self.h[t] >= tr.stop):
                self._close(tr, t, tr.stop, "stop loss (vela de entrada)")

    # ------------------------------------------------------------------
    def _manage(self, t: int):
        cfg = self.cfg
        for tr in list(self.open):
            if t <= tr.t_entry:
                continue
            d = tr.direction
            r_unit = (tr.entry - tr.sl) * d
            fav = (self.h[t] - tr.entry) if d > 0 else (tr.entry - self.l[t])
            tr.max_r = max(tr.max_r, fav / r_unit)
            stop_hit = self.l[t] <= tr.stop if d > 0 else self.h[t] >= tr.stop
            if stop_hit:
                px = min(tr.stop, self.o[t]) if d > 0 else max(tr.stop, self.o[t])
                reason = "stop loss" if not tr.tp1_hit else "trailing stop"
                self._close(tr, t, px, reason)
                continue
            if not tr.tp1_hit and (self.h[t] >= tr.tp1 if d > 0 else self.l[t] <= tr.tp1):
                frac = cfg.tp1_close_fraction
                tr.realised += frac * tr.units * (tr.tp1 - tr.entry) * d
                tr.remaining = 1 - frac
                tr.tp1_hit = True
                tr.stop = tr.entry  # break-even
                if tr.remaining <= 0:
                    self._close(tr, t, tr.tp1, "take profit")
                    continue
            if tr.tp1_hit:
                # Dejar correr: subir el stop a cada nuevo swing a favor (LPS/LPSY)
                pool = self.st.lows if d > 0 else self.st.highs
                sw = [s for s in pool if s.confirm == t and s.idx > tr.t_entry]
                for s in sw:
                    new = s.price - d * cfg.sl_buffer_atr * self.atr[t]
                    if (new - tr.stop) * d > 0:
                        tr.stop = new

    def _mtm(self, tr: Trade, px: float) -> float:
        return tr.realised + tr.remaining * tr.units * (px - tr.entry) * tr.direction

    def _close(self, tr: Trade, t: int, px: float, reason: str):
        cost = tr.units * self.cfg.spread
        tr.pnl = tr.realised + tr.remaining * tr.units * (px - tr.entry) * tr.direction - cost
        tr.t_exit, tr.exit_price, tr.exit_reason = t, px, reason
        tr.remaining = 0.0
        self.open.remove(tr)
        self.closed.append(tr)
        self.rm.on_close(tr.pnl, tr.pnl < 0)


def run_backtest(df: pd.DataFrame, cfg: Config | None = None) -> Result:
    return Engine(df, cfg).run()
