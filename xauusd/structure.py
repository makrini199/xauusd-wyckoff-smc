"""Estructura de mercado Smart Money: swings, BOS, CHoCH, liquidez y barridos.

Todo se calcula de forma incremental, vela a vela, y sin mirar al futuro:
un swing de longitud L solo se conoce L velas después de su vela extrema.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class Swing:
    idx: int            # vela del extremo
    confirm: int        # vela en la que se confirma (idx + L)
    price: float
    kind: int           # +1 máximo, -1 mínimo
    broken: bool = False
    swept: bool = False
    equal: bool = False  # forma equal highs / equal lows con el swing anterior


@dataclass
class StructureEvent:
    t: int              # vela de la ruptura (cierre más allá del swing)
    kind: str           # "BOS" o "CHoCH"
    direction: int      # +1 alcista, -1 bajista
    level: float        # precio del swing roto
    swing_idx: int
    origin_idx: int     # extremo desde el que arranca el impulso (mínimo del tramo si alcista)
    displacement: bool  # hubo vela de desplazamiento en el tramo
    swept_before: bool  # se barrió liquidez del lado contrario antes del impulso


@dataclass
class Sweep:
    t: int
    direction: int      # +1 barrido de máximos (sesgo bajista), -1 barrido de mínimos (sesgo alcista)
    level: float
    equal: bool


class StructureTracker:
    """Recorre las velas y mantiene tendencia, swings y eventos."""

    def __init__(self, df: pd.DataFrame, cfg: Config):
        self.df = df
        self.cfg = cfg
        self.h = df["high"].to_numpy()
        self.l = df["low"].to_numpy()
        self.c = df["close"].to_numpy()
        self.atr = df["atr"].to_numpy()
        self.disp = df["displacement"].to_numpy()
        self.swings: list[Swing] = []
        self.highs: list[Swing] = []
        self.lows: list[Swing] = []
        self.events: list[StructureEvent] = []
        self.sweeps: list[Sweep] = []
        self.trend = 0
        # Swing que protege la tendencia: romperlo es un CHoCH válido
        self.protected: Swing | None = None
        self.bos_count = 0  # BOS consecutivos a favor de la tendencia vigente

    # ------------------------------------------------------------------
    def _confirm_swings(self, t: int):
        L = self.cfg.swing_len
        i = t - L
        if i < L:
            return
        hi, lo = self.h, self.l
        if hi[i] > hi[i - L:i].max() and hi[i] >= hi[i + 1:t + 1].max():
            s = Swing(i, t, hi[i], +1)
            self._mark_equal(s, self.highs)
            self.highs.append(s)
            self.swings.append(s)
        if lo[i] < lo[i - L:i].min() and lo[i] <= lo[i + 1:t + 1].min():
            s = Swing(i, t, lo[i], -1)
            self._mark_equal(s, self.lows)
            self.lows.append(s)
            self.swings.append(s)

    def _mark_equal(self, s: Swing, same_side: list[Swing]):
        tol = self.cfg.eq_tolerance_atr * (self.atr[s.idx] if not np.isnan(self.atr[s.idx]) else 0)
        for prev in reversed(same_side[-3:]):
            if not prev.broken and abs(prev.price - s.price) <= tol:
                s.equal = prev.equal = True
                break

    def _leg_origin(self, sw: Swing, t: int, direction: int) -> int:
        seg = slice(sw.idx, t + 1)
        if direction > 0:
            return sw.idx + int(np.argmin(self.l[seg]))
        return sw.idx + int(np.argmax(self.h[seg]))

    def _swept_between(self, a: int, b: int, direction: int) -> bool:
        # direction del impulso: un impulso alcista busca un barrido de mínimos (-1) previo
        want = -direction
        return any(s.direction == want and a - 3 <= s.t <= b for s in self.sweeps)

    # ------------------------------------------------------------------
    def step(self, t: int) -> list[StructureEvent]:
        """Procesa la vela t. Devuelve los eventos de estructura de esa vela."""
        self._confirm_swings(t)
        new: list[StructureEvent] = []
        h, l, c = self.h[t], self.l[t], self.c[t]

        # Barridos: la mecha supera un nivel pero la vela cierra de vuelta
        for s in self.highs:
            if not s.broken and not s.swept and s.confirm < t and h > s.price and c < s.price:
                s.swept = True
                self.sweeps.append(Sweep(t, +1, s.price, s.equal))
        for s in self.lows:
            if not s.broken and not s.swept and s.confirm < t and l < s.price and c > s.price:
                s.swept = True
                self.sweeps.append(Sweep(t, -1, s.price, s.equal))

        # Rupturas por cierre
        for direction, pool in ((+1, self.highs), (-1, self.lows)):
            broken = [s for s in pool if not s.broken and s.confirm < t
                      and (c > s.price if direction > 0 else c < s.price)]
            if not broken:
                continue
            for s in broken:
                s.broken = True
            # El swing relevante es el más reciente de los rotos
            sw = max(broken, key=lambda s: s.idx)
            ev = self._classify(sw, t, direction)
            if ev:
                new.append(ev)
        self.events.extend(new)
        return new

    def _classify(self, sw: Swing, t: int, direction: int) -> StructureEvent | None:
        origin = self._leg_origin(sw, t, direction)
        disp = bool(self.disp[max(origin, t - 5):t + 1].any())
        swept = self._swept_between(origin, t, direction)
        if self.trend == direction:
            kind = "BOS"
            self.bos_count += 1
        else:
            # Contra la tendencia: solo es CHoCH si rompe el swing protegido;
            # romper un swing interno es ruido dentro de la tendencia.
            if self.trend != 0 and self.protected is not None:
                if direction > 0 and sw.price < self.protected.price:
                    return None
                if direction < 0 and sw.price > self.protected.price:
                    return None
            kind = "CHoCH" if self.trend != 0 else "BOS"
            self.trend = direction
            self.bos_count = 1
        # El nuevo swing protegido es el extremo opuesto que originó esta ruptura
        opp = self.lows if direction > 0 else self.highs
        cands = [s for s in opp if s.idx <= origin + self.cfg.swing_len and s.confirm <= t]
        if cands:
            self.protected = cands[-1]
        else:
            price = self.l[origin] if direction > 0 else self.h[origin]
            self.protected = Swing(origin, t, price, -direction)
        return StructureEvent(t, kind, direction, sw.price, sw.idx, origin, disp, swept)

    # ------------------------------------------------------------------
    def external_liquidity(self, t: int, direction: int, above: float) -> float | None:
        """Siguiente nivel de liquidez externa a favor de `direction` más allá de `above`."""
        if direction > 0:
            lv = [s.price for s in self.highs if not s.broken and s.confirm <= t and s.price > above]
            return min(lv) if lv else None
        lv = [s.price for s in self.lows if not s.broken and s.confirm <= t and s.price < above]
        return max(lv) if lv else None

    def recent_swings(self, kind: int, n: int, t: int) -> list[Swing]:
        pool = self.highs if kind > 0 else self.lows
        return [s for s in pool if s.confirm <= t][-n:]


def run(df: pd.DataFrame, cfg: Config) -> StructureTracker:
    tr = StructureTracker(df, cfg)
    for t in range(len(df)):
        tr.step(t)
    return tr


def trend_series(df: pd.DataFrame, cfg: Config) -> pd.Series:
    """Tendencia estructural (+1/-1/0) al cierre de cada vela."""
    tr = StructureTracker(df, cfg)
    out = np.zeros(len(df), dtype=int)
    for t in range(len(df)):
        tr.step(t)
        out[t] = tr.trend
    return pd.Series(out, index=df.index)
