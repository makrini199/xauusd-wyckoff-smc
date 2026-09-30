"""Gestión de exposición: límites por sistema, diarios, semanales y drawdown."""
from dataclasses import dataclass, field

import pandas as pd

from .config import Config


@dataclass
class RiskManager:
    cfg: Config
    equity: float = 0.0
    peak: float = 0.0
    halted: bool = False
    day: object = None
    trades_today: int = 0
    losses_today: int = 0
    week: object = None
    week_start_equity: float = 0.0
    week_stopped: bool = False
    log: list = field(default_factory=list)

    def __post_init__(self):
        self.equity = self.peak = self.week_start_equity = self.cfg.initial_capital

    def new_bar(self, ts: pd.Timestamp):
        d = ts.date()
        if d != self.day:
            self.day, self.trades_today, self.losses_today = d, 0, 0
        wk = ts.isocalendar()[:2]
        if wk != self.week:
            self.week, self.week_start_equity, self.week_stopped = wk, self.equity, False

    def can_open(self, system: str, direction: int, risk: float, open_trades: list, aligned: bool) -> str | None:
        """Devuelve None si se puede abrir, o el motivo del rechazo."""
        c = self.cfg
        if self.halted:
            return f"drawdown interno >= {c.internal_max_dd:.0%}: sistema detenido"
        if self.week_stopped:
            return f"pérdida semanal >= {c.weekly_loss_stop:.0%}"
        if self.trades_today >= c.max_trades_day:
            return "máximo de operaciones diarias alcanzado"
        if self.losses_today >= c.max_losses_day:
            return "dos pérdidas hoy: no se opera más"
        if any(tr.system == system for tr in open_trades):
            return f"ya hay una posición abierta del Sistema {system}"
        if sum(tr.open_risk for tr in open_trades) + risk > c.max_open_risk + 1e-12:
            return "riesgo agregado superaría el máximo"
        if any(tr.direction != direction for tr in open_trades) and not aligned:
            return "A y B con sesgos opuestos sin alineación multi-temporal"
        return None

    def on_open(self):
        self.trades_today += 1

    def on_close(self, pnl: float, is_loss: bool):
        self.equity += pnl
        self.peak = max(self.peak, self.equity)
        if is_loss:
            self.losses_today += 1
        if self.equity <= self.week_start_equity * (1 - self.cfg.weekly_loss_stop):
            self.week_stopped = True
        if self.equity <= self.peak * (1 - self.cfg.internal_max_dd):
            self.halted = True
