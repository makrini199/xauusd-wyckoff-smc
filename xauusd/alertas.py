"""Alertas en vivo: vigila XAUUSD y avisa por Telegram de cada paso del sistema.

En cada vela nueva de 1 minuto se recalcula el motor sobre los últimos días
de datos y se comparan sus eventos con los ya enviados (guardados en un
archivo de estado, para no repetir tras un reinicio). Eventos:

  setup    BOS/CHoCH con POI, stop, objetivo y RR ≥ 1:3: esperando retorno al POI
  zona     el precio ha entrado en el POI: esperando el mini BOS de 1-5 min
  entrada  mini BOS confirmado y puntaje ≥ umbral: operación válida (con gráfico)
  tp1      TP1 alcanzado: cerrar parte y stop a break-even
  cierre   operación cerrada (stop, trailing stop o take profit)

Las alertas no ejecutan órdenes. El riesgo y los límites diarios/semanales
se calculan sobre las operaciones del propio sistema en la ventana de datos,
no sobre tu cuenta real.
"""
import json
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import Config
from .engine import Engine, Result, Trade
from .scoring import LABELS, max_score, score, threshold

# Criterios que solo se conocen en el momento de la entrada
AT_ENTRY = ("sos_sow", "poi_return", "killzone")


def potential(s, cfg: Config) -> float:
    """Puntaje máximo que aún puede alcanzar un setup (lo ya conocido + lo que falta por saber)."""
    return score(s.base_checks, cfg) + sum(cfg.weights[k] for k in AT_ENTRY)

OZ_PER_LOT = 100  # 1 lote estándar de oro = 100 onzas


@dataclass
class Alert:
    key: str
    kind: str
    time: pd.Timestamp
    text: str
    trade: Trade | None = None


def _dir(d: int) -> str:
    return "LARGO ▲" if d > 0 else "CORTO ▼"


def _px(v: float) -> str:
    return f"{v:.2f}"


def _checks(checks: dict) -> str:
    return "\n".join(f"{'✅' if checks.get(k) else '▫️'} {v}" for k, v in LABELS.items() if k in checks)


def _size(cfg: Config, risk: float, entry: float, sl: float) -> str:
    oz = cfg.initial_capital * risk / abs(entry - sl)
    return f"{oz:,.1f} oz ≈ {oz / OZ_PER_LOT:.2f} lotes (capital {cfg.initial_capital:,.0f} USD)"


def collect(res: Result) -> list[Alert]:
    """Todos los eventos alertables del resultado, con clave única y hora."""
    cfg, x = res.cfg, res.exec_df
    bar = pd.Timedelta(cfg.tf_exec)
    out: list[Alert] = []
    for s in res.setups:
        if potential(s, cfg) < threshold(s.system, cfg):
            continue  # ni con todo a favor llegaría al umbral: no se avisa
        t0 = x.index[s.t] + bar
        key = f"{s.system}|{s.direction}|{x.index[s.t].isoformat()}"
        known = {k: v for k, v in s.base_checks.items() if not k.startswith("_")}
        out.append(Alert(f"setup|{key}", "setup", t0, (
            f"<b>🔎 Setup {_dir(s.direction)} · Sistema {s.system}</b>\n"
            f"{s.event.kind} en {cfg.tf_exec} · fase Wyckoff {s.base_checks.get('_phase', '?')}\n"
            f"POI ({s.poi_kind}): <b>{_px(s.entry)}</b> · Fib {s.fib:.2f}\n"
            f"SL {_px(s.sl)} · TP1 {_px(s.tp1)} · RR 1:{s.rr:.1f}\n"
            f"Puntaje posible {potential(s, cfg):g}/{max_score(cfg):g} (umbral {threshold(s.system, cfg):g})\n"
            f"Esperando retorno al POI y mini BOS en {cfg.tf_ltf}.\n\n{_checks(known)}")))
        if s.zone_time is not None:
            lvl = f" que rompa {_px(s.ltf_level)}" if s.ltf_level is not None else ""
            out.append(Alert(f"zona|{key}", "zona", s.zone_time, (
                f"<b>📍 Precio en el POI · {_dir(s.direction)} · Sistema {s.system}</b>\n"
                f"POI {_px(s.entry)} · SL {_px(s.sl)} · TP1 {_px(s.tp1)}\n"
                f"Esperando mini BOS en {cfg.tf_ltf}{lvl}.")))
    for tr in [*res.trades, *res.open_trades]:
        s = tr.setup
        key = f"{tr.system}|{tr.direction}|{x.index[s.t].isoformat()}"
        rr = abs(tr.tp1 - tr.entry) / abs(tr.entry - tr.sl)
        when = tr.entry_time if tr.entry_time is not None else x.index[tr.t_entry] + bar
        wy = sum(1 for k, v in s.wyckoff_tests.items() if not k.startswith("_") and v)
        out.append(Alert(f"entrada|{key}", "entrada", when, (
            f"<b>🚨 ENTRADA {_dir(tr.direction)} · Sistema {tr.system}</b>\n"
            f"XAUUSD · {when:%d-%m %H:%M} UTC · mini BOS {cfg.tf_ltf}\n"
            f"Entrada <b>{_px(tr.entry)}</b>\nSL <b>{_px(tr.sl)}</b>\nTP1 <b>{_px(tr.tp1)}</b> (cerrar "
            f"{cfg.tp1_close_fraction:.0%} y stop a break-even)\n"
            f"RR 1:{rr:.1f} · Puntaje <b>{tr.points:g}/{max_score(cfg):g}</b> · pruebas Wyckoff {wy}/9\n"
            f"Riesgo {tr.risk_frac:.1%}: {_size(cfg, tr.risk_frac, tr.entry, tr.sl)}\n\n"
            f"{_checks(tr.checks)}"), tr))
        if tr.t_tp1 is not None:
            out.append(Alert(f"tp1|{key}", "tp1", x.index[tr.t_tp1] + bar, (
                f"<b>🎯 TP1 alcanzado · {_dir(tr.direction)} · Sistema {tr.system}</b>\n"
                f"TP1 {_px(tr.tp1)}: cerrar {cfg.tp1_close_fraction:.0%} y mover el stop a "
                f"{_px(tr.entry)} (break-even). El resto corre con stop bajo cada nuevo swing.")))
        if tr.t_exit is not None and tr.exit_reason != "fin de datos":
            r_unit = abs(tr.entry - tr.sl) * tr.units
            res_r = tr.pnl / r_unit if r_unit else 0.0
            out.append(Alert(f"cierre|{key}", "cierre", x.index[tr.t_exit] + bar, (
                f"<b>{'✅' if res_r > 0 else '❌'} Cierre {_dir(tr.direction)} · Sistema {tr.system}</b>\n"
                f"{tr.exit_reason} en {_px(tr.exit_price)} · resultado {res_r:+.2f}R")))
    return sorted(out, key=lambda a: a.time)


class Watcher:
    def __init__(self, source, sink, cfg: Config | None = None, state_path: str | Path = "alertas_estado.json",
                 days: int = 20, max_age_min: int = 30, charts_dir: str | Path = "alertas_graficos",
                 dxy_source=None, log=print):
        self.source = source
        self.sink = sink
        self.cfg = cfg or Config()
        self.state_path = Path(state_path)
        self.days = days
        self.max_age = pd.Timedelta(minutes=max_age_min)
        self.charts_dir = Path(charts_dir)
        self.dxy_source = dxy_source
        self.log = log
        self.sent: set[str] = set()
        self.initialised = False
        self.last_bar = None
        if self.state_path.exists():
            self.sent = set(json.loads(self.state_path.read_text()).get("enviadas", []))
            self.initialised = True

    def _save(self):
        self.state_path.write_text(json.dumps({"enviadas": sorted(self.sent)}, indent=0))

    def step(self) -> list[Alert]:
        """Una pasada: descarga, recalcula y envía lo nuevo. Devuelve lo enviado."""
        df = self.source.fetch(self.days)
        if df is None or len(df) == 0 or df.index[-1] == self.last_bar:
            return []
        self.last_bar = df.index[-1]
        dxy = self.dxy_source.fetch(self.days) if self.dxy_source else None
        res = Engine(df, self.cfg, dxy).run(close_open=False)
        alerts = collect(res)
        now = df.index[-1] + pd.Timedelta("1min")
        if not self.initialised:
            # Primera ejecución: lo que ya pasó no se envía
            self.sent |= {a.key for a in alerts}
            self.initialised = True
            self._save()
            self.log(f"[{now:%Y-%m-%d %H:%M}] iniciado con {len(alerts)} eventos históricos (no se envían)")
            return []
        sent = []
        for a in alerts:
            if a.key in self.sent:
                continue
            self.sent.add(a.key)
            if now - a.time > self.max_age:
                continue  # evento viejo (p. ej. tras un corte): se marca pero no se envía
            self._send(a, res)
            sent.append(a)
        self._save()
        return sent

    def _send(self, a: Alert, res: Result):
        if a.kind == "entrada" and a.trade is not None:
            try:
                from .plot import plot_trade
                self.charts_dir.mkdir(parents=True, exist_ok=True)
                path = self.charts_dir / f"{a.time:%Y%m%d_%H%M}_{a.trade.system}.png"
                plot_trade(res, a.trade, path)
                self.sink.send_photo(path, a.text)
                return
            except Exception as e:  # el gráfico nunca debe impedir la alerta
                self.log(f"gráfico no enviado: {e}")
        self.sink.send_message(a.text)

    def loop(self, delay_s: int = 5):
        """Comprueba una vez por minuto, `delay_s` segundos después del cierre de cada vela."""
        self.log("Vigilando XAUUSD… (Ctrl+C para parar)")
        while True:
            try:
                for a in self.step():
                    self.log(f"alerta enviada: {a.kind} {a.key}")
            except Exception as e:
                self.log(f"error: {e}")
            now = time.time()
            time.sleep(60 - now % 60 + delay_s)
