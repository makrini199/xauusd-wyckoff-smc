"""Wyckoff: rangos de trading, Spring/Upthrust, SOS/SOW, fases y las nueve pruebas.

Los rangos se detectan de forma incremental. Los criterios de Spring, SOS y
las pruebas de acumulación siguen la sección "Criterios objetivos" de la
estrategia; los que no se pueden medir solo con precio y volumen del oro
(conteo punto y figura, fuerza relativa frente al dólar) se marcan como no
evaluados en vez de inventar un valor.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class Shakeout:
    """Spring (direction=+1) o Upthrust (direction=-1)."""
    direction: int
    t_break: int
    t_reclaim: int
    extreme: float       # mínimo del Spring / máximo del Upthrust
    volume: float
    valid: bool
    invalid_reason: str = ""
    tested: bool = False
    t_test: int | None = None


@dataclass
class Strength:
    """Sign of Strength (+1) o Sign of Weakness (-1)."""
    t: int
    direction: int
    closed_back_inside: bool = False


@dataclass
class TradingRange:
    start: int
    detected: int
    support: float
    resistance: float
    kind: str                       # "acumulacion", "distribucion" o "neutral"
    climax: bool                    # clímax de volumen en el extremo (PS/SC/ST o PSY/BC/ST)
    end: int | None = None
    breakout: int = 0
    shakeouts: list = field(default_factory=list)
    strengths: list = field(default_factory=list)
    _outside_dir: int = 0
    _outside_bars: int = 0
    _outside_start: int = 0

    @property
    def width(self) -> float:
        return self.resistance - self.support



class WyckoffTracker:
    def __init__(self, df: pd.DataFrame, cfg: Config):
        self.df = df
        self.cfg = cfg
        self.o = df["open"].to_numpy()
        self.h = df["high"].to_numpy()
        self.l = df["low"].to_numpy()
        self.c = df["close"].to_numpy()
        self.v = df["volume"].to_numpy()
        self.atr = df["atr"].to_numpy()
        self.rvol = df["rvol"].to_numpy()
        self.ranges: list[TradingRange] = []
        self.active: TradingRange | None = None
        self._pending: list[tuple[Shakeout, int]] = []  # Springs a vigilar (alarma)

    # ------------------------------------------------------------------
    def _wide(self, t: int) -> bool:
        return (self.h[t] - self.l[t]) >= self.cfg.displacement_atr * self.atr[t]

    def _detect(self, t: int):
        n = self.cfg.range_min_bars
        if t < 2 * n or np.isnan(self.atr[t]):
            return
        if self.ranges and self.ranges[-1].end is not None and t - self.ranges[-1].end < n // 2:
            return
        a = t - n + 1
        hi = self.h[a:t + 1].max()
        lo = self.l[a:t + 1].min()
        if hi - lo > self.cfg.range_max_width_atr * self.atr[t]:
            return
        before = self.c[max(a - n, 0)]
        kind = "acumulacion" if before > hi else "distribucion" if before < lo else "neutral"
        ext = a + int(np.argmin(self.l[a:t + 1])) if kind != "distribucion" else a + int(np.argmax(self.h[a:t + 1]))
        climax = bool(np.nanmax(self.rvol[max(ext - 1, 0):ext + 2]) >= self.cfg.vol_high)
        self.active = TradingRange(a, t, lo, hi, kind, climax)
        self.ranges.append(self.active)

    def _check_alarm(self, t: int):
        """Invalida Springs/Upthrusts si tras el rebote vuelven velas de spread
        amplio en contra (no hay "fallo en continuar cayendo")."""
        keep = []
        for sh, deadline in self._pending:
            d = sh.direction
            against = (self.c[t] < self.o[t]) if d > 0 else (self.c[t] > self.o[t])
            beyond = (self.c[t] < sh.extreme) if d > 0 else (self.c[t] > sh.extreme)
            if sh.valid and ((against and self._wide(t)) or beyond):
                sh.valid = False
                sh.invalid_reason = "alarma: vuelve a caer con spread amplio" if d > 0 else "alarma: vuelve a subir con spread amplio"
            if t < deadline and sh.valid:
                keep.append((sh, deadline))
        self._pending = keep

    def _check_tests(self, r: TradingRange, t: int):
        lb = self.cfg.spring_test_vol_lookback
        low_third = np.nanpercentile(self.v[max(t - lb, 0):t], 33.3) if t > 5 else np.inf
        max_ratio = self.cfg.spring_test_vol_ratio[1]
        for sh in r.shakeouts:
            if not sh.valid or sh.tested or t <= sh.t_reclaim + 1:
                continue
            is_spring = sh.direction > 0
            near = (self.l[t] <= r.support + 0.5 * self.atr[t] and self.l[t] >= sh.extreme) if is_spring \
                else (self.h[t] >= r.resistance - 0.5 * self.atr[t] and self.h[t] <= sh.extreme)
            if near and self.v[t] <= max_ratio * sh.volume and self.v[t] <= low_third:
                sh.tested = True
                sh.t_test = t

    def step(self, t: int):
        self._check_alarm(t)
        r = self.active
        if r is None:
            self._detect(t)
            return
        c, h, l = self.c[t], self.h[t], self.l[t]
        self._check_tests(r, t)
        for s in r.strengths:
            if r.support < c < r.resistance:
                s.closed_back_inside = True

        # SOS / SOW: ruptura con spread amplio, volumen alto y cierre en el extremo
        span = h - l
        if span > 0 and self._wide(t) and self.rvol[t] >= self.cfg.vol_high:
            if c > r.resistance and (c - l) / span >= 0.75:
                r.strengths.append(Strength(t, +1))
            elif c < r.support and (h - c) / span >= 0.75:
                r.strengths.append(Strength(t, -1))

        out_dir = +1 if c > r.resistance else -1 if c < r.support else 0
        if out_dir != 0:
            if r._outside_dir != out_dir:
                r._outside_dir, r._outside_bars, r._outside_start = out_dir, 0, t
            r._outside_bars += 1
            if r._outside_bars > self.cfg.spring_max_bars_outside:
                # Aceptación fuera del rango: ruptura real, fin del rango
                r.end, r.breakout = t, out_dir
                self.active = None
            return

        # De vuelta dentro (o nunca salió por cierre): ¿Spring / Upthrust?
        if r._outside_dir != 0:
            self._register_shakeout(r, r._outside_start, t, r._outside_dir)
            r._outside_dir, r._outside_bars = 0, 0
        elif l < r.support:
            self._register_shakeout(r, t, t, -1)
        elif h > r.resistance:
            self._register_shakeout(r, t, t, +1)

    def _register_shakeout(self, r: TradingRange, a: int, b: int, side: int):
        """side=-1: perforó el soporte (Spring); side=+1: el techo (Upthrust)."""
        seg = slice(a, b + 1)
        extreme = self.l[seg].min() if side < 0 else self.h[seg].max()
        vol = float(self.v[seg].max())
        strong = any(self._wide(i) and self.rvol[i] >= self.cfg.vol_high for i in range(a, b + 1))
        low_or_falling = self.rvol[a] < 1.0 or (a > 0 and self.v[a] < self.v[a - 1])
        valid, reason = True, ""
        if strong:
            valid, reason = False, "ruptura con spread amplio y volumen alto"
        elif not low_or_falling:
            valid, reason = False, "volumen de ruptura ni bajo ni decreciente"
        sh = Shakeout(-side, a, b, extreme, vol, valid, reason)
        r.shakeouts.append(sh)
        if valid:
            self._pending.append((sh, b + 4))

    # ------------------------------------------------------------------
    def context(self, t: int) -> TradingRange | None:
        """Rango activo o que terminó hace poco (sigue siendo el marco vigente)."""
        if self.active is not None:
            return self.active
        if self.ranges and self.ranges[-1].end is not None and t - self.ranges[-1].end <= 2 * self.cfg.range_min_bars:
            return self.ranges[-1]
        return None


# ----------------------------------------------------------------------
def shakeout_ok(r: TradingRange | None, direction: int, t: int, require_test: bool = False) -> bool:
    if r is None:
        return False
    for sh in r.shakeouts:
        if sh.direction == direction:
            if sh.valid and sh.t_reclaim <= t and (sh.tested or not require_test):
                return True
    return False


def strength_ok(r: TradingRange | None, direction: int, t: int) -> bool:
    if r is None:
        return False
    return any(s.direction == direction and s.t <= t and not s.closed_back_inside for s in r.strengths)


def contradicts(r: TradingRange | None, direction: int, t: int) -> bool:
    """Jerarquía: Smart Money nunca contradice a Wyckoff. Si el rango marca
    el lado contrario con un evento válido y nada a favor, se bloquea."""
    if r is None:
        return False
    against = shakeout_ok(r, -direction, t) or strength_ok(r, -direction, t)
    favour = shakeout_ok(r, direction, t) or strength_ok(r, direction, t)
    return against and not favour


def phase(r: TradingRange | None, direction: int, t: int, trend: int, bos_count: int) -> tuple[str, bool]:
    """Fase Wyckoff vigente y si está claramente identificada a favor de `direction`."""
    if r is not None:
        sp, st = shakeout_ok(r, direction, t), strength_ok(r, direction, t)
        want = "acumulacion" if direction > 0 else "distribucion"
        if st:
            return ("D", True)
        if sp:
            return ("C", r.kind in (want, "neutral"))
        if r.end is None:
            return ("B", False)
    if trend == direction and bos_count >= 2:
        return ("E", True)
    return ("?", False)


def nine_tests(r: TradingRange | None, direction: int, t: int, st, df: pd.DataFrame, cfg: Config,
               ranges=None, ratio=None) -> dict:
    """Las nueve pruebas (acumulación; en espejo para distribución).

    Devuelve {nombre: True/False/None}; None = no evaluable con estos datos
    (prueba 1 sin rango previo que contar, prueba 8 sin datos del dólar).
    """
    from .pf import check_objective, check_relative_strength

    res: dict[str, bool | None] = {}
    if r is not None:
        res["1_objetivo_previo_cumplido"], res["_objetivo_pf"] = check_objective(
            r, direction, t, ranges or [], df, cfg)
    else:
        res["1_objetivo_previo_cumplido"], res["_objetivo_pf"] = None, None
    res["8_fuerza_relativa"] = check_relative_strength(r, direction, t, ratio)
    if r is None:
        for k in ("2_eventos_PS_SC_ST", "3_actividad_rango", "4_estructura_previa_rota",
                  "5_soportes_crecientes", "6_spring_o_test", "7_maximos_minimos_crecientes",
                  "9_base_amplia"):
            res[k] = False
        return res
    want = "acumulacion" if direction > 0 else "distribucion"
    res["2_eventos_PS_SC_ST"] = r.kind == want and r.climax
    a, b = r.start, min(t, r.end if r.end is not None else t)
    seg = df.iloc[a:b + 1]
    up = seg.loc[seg["close"] > seg["open"], "volume"].sum()
    dn = seg.loc[seg["close"] < seg["open"], "volume"].sum()
    res["3_actividad_rango"] = bool(up > dn) if direction > 0 else bool(dn > up)
    res["4_estructura_previa_rota"] = any(
        e.kind == "CHoCH" and e.direction == direction and a <= e.t <= t for e in st.events)
    lows = [s for s in (st.lows if direction > 0 else st.highs) if a <= s.idx <= t and s.confirm <= t][-2:]
    res["5_soportes_crecientes"] = len(lows) == 2 and (
        lows[1].price > lows[0].price if direction > 0 else lows[1].price < lows[0].price)
    res["6_spring_o_test"] = shakeout_ok(r, direction, t)
    hs = st.recent_swings(+1, 2, t)
    ls = st.recent_swings(-1, 2, t)
    if len(hs) == 2 and len(ls) == 2:
        if direction > 0:
            res["7_maximos_minimos_crecientes"] = hs[1].price > hs[0].price and ls[1].price > ls[0].price
        else:
            res["7_maximos_minimos_crecientes"] = hs[1].price < hs[0].price and ls[1].price < ls[0].price
    else:
        res["7_maximos_minimos_crecientes"] = False
    res["9_base_amplia"] = (b - a) >= 2 * cfg.range_min_bars
    return res
