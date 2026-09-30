"""Puntos de interés: order blocks y fair value gaps."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class OrderBlock:
    t_created: int
    idx: int            # vela de origen
    direction: int      # +1 demanda (alcista), -1 oferta (bajista)
    low: float
    high: float
    touches: int = 0
    mitigated: bool = False
    _inside: bool = False

    @property
    def proximal(self) -> float:
        """Borde por el que llega el precio en el retroceso."""
        return self.high if self.direction > 0 else self.low

    @property
    def distal(self) -> float:
        return self.low if self.direction > 0 else self.high

    def update(self, h: float, l: float, c: float):
        """Cuenta toques (entradas en la zona) y detecta mitigación: una vela
        completa que cierra atravesándola por el lado contrario."""
        if self.mitigated:
            return
        touching = l <= self.high and h >= self.low
        if touching and not self._inside:
            self.touches += 1
        self._inside = touching
        if (self.direction > 0 and c < self.low) or (self.direction < 0 and c > self.high):
            self.mitigated = True


@dataclass
class FVG:
    t_created: int
    direction: int
    low: float
    high: float
    filled: bool = False

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def update(self, c: float):
        if (self.direction > 0 and c < self.low) or (self.direction < 0 and c > self.high):
            self.filled = True


def find_order_block(df: pd.DataFrame, origin: int, t: int, direction: int) -> OrderBlock | None:
    """Última vela de color contrario antes del impulso que produjo la ruptura.

    Se busca desde el extremo del tramo (origin) hacia atrás unas pocas velas,
    y hacia delante hasta la primera vela a favor.
    """
    o = df["open"].to_numpy()
    c = df["close"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    opposite = (lambda i: c[i] < o[i]) if direction > 0 else (lambda i: c[i] > o[i])
    # Hacia delante desde el origen: última vela contraria antes de que arranque el impulso
    best = None
    for i in range(max(origin - 3, 0), t):
        if opposite(i):
            best = i
        elif best is not None and i > origin:
            break
    if best is None:
        best = origin
    return OrderBlock(t, best, direction, l[best], h[best])


def find_fvgs(df: pd.DataFrame, a: int, b: int, direction: int, cfg: Config) -> list[FVG]:
    """Huecos de tres velas dentro del tramo [a, b] a favor de `direction`."""
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    atr = df["atr"].to_numpy()
    out = []
    for i in range(max(a + 2, 2), b + 1):
        if direction > 0 and l[i] > h[i - 2]:
            lo, hi = h[i - 2], l[i]
        elif direction < 0 and h[i] < l[i - 2]:
            lo, hi = h[i], l[i - 2]
        else:
            continue
        if hi - lo >= cfg.fvg_min_atr * (atr[i] if not np.isnan(atr[i]) else 0):
            out.append(FVG(i, direction, lo, hi))
    return out
