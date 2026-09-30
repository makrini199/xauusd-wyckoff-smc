"""Prueba 1 (objetivo por punto y figura) y prueba 8 (fuerza relativa).

Prueba 1. Conteo horizontal de Wyckoff: el rango previo al movimiento
(la distribución que precedió a la caída, o la acumulación que precedió a
la subida) acumula una "causa". Su efecto se mide contando las columnas del
gráfico de punto y figura a lo ancho de ese rango:

    objetivo = línea de conteo ∓ columnas × caja × reversión

Para una acumulación, la prueba se cumple si el mínimo del rango actual
(el clímax de venta) ya ha llegado al objetivo bajista de la distribución
anterior, con una caja de tolerancia.

Prueba 8. Fuerza relativa del oro frente al dólar: la pendiente de
log(XAUUSD / DXY) desde el inicio del rango. En acumulación debe ser
positiva (el oro aguanta mejor que el dólar); en distribución, negativa.
"""
from dataclasses import dataclass

import numpy as np

from .config import Config


@dataclass
class PFColumn:
    direction: int   # +1 columna de X, -1 columna de O
    top: float
    bottom: float


def pf_columns(highs, lows, box: float, reversal: int = 3) -> list[PFColumn]:
    """Gráfico de punto y figura por máximos y mínimos, precios en múltiplos de la caja."""
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    if len(highs) == 0 or box <= 0:
        return []
    cols: list[PFColumn] = []
    level = np.floor(lows[0] / box) * box
    d, top, bottom = 0, level, level
    for h, l in zip(highs, lows):
        up = np.floor(h / box) * box
        dn = np.ceil(l / box) * box
        if d == 0:
            if up - top >= box * reversal:
                d, top, bottom = +1, up, bottom
            elif bottom - dn >= box * reversal:
                d, top, bottom = -1, top, dn
            continue
        if d > 0:
            if up > top:
                top = up
            elif top - dn >= box * reversal:
                cols.append(PFColumn(+1, top, bottom))
                d, top, bottom = -1, top - box, dn
        else:
            if dn < bottom:
                bottom = dn
            elif up - bottom >= box * reversal:
                cols.append(PFColumn(-1, top, bottom))
                d, top, bottom = +1, up, bottom + box
    if d != 0:
        cols.append(PFColumn(d, top, bottom))
    return cols


def pf_objective(prev_range, direction: int, df, cfg: Config) -> float | None:
    """Objetivo del movimiento que siguió al rango anterior.

    `direction` es la dirección del setup actual; el objetivo que interesa es
    el del movimiento previo, en sentido contrario (una acumulación alcista
    exige que se haya cumplido el objetivo bajista de la distribución anterior).
    """
    if prev_range is None or prev_range.end is None:
        return None
    a, b = prev_range.start, prev_range.end
    atr = df["atr"].to_numpy()[a:b + 1]
    atr = atr[~np.isnan(atr)]
    if len(atr) == 0:
        return None
    box = cfg.pf_box_atr * float(np.median(atr))
    cols = pf_columns(df["high"].to_numpy()[a:b + 1], df["low"].to_numpy()[a:b + 1], box, cfg.pf_reversal)
    if len(cols) < 2:
        return None
    width = len(cols) * box * cfg.pf_reversal
    if direction > 0:  # el movimiento previo fue bajista
        return prev_range.support - width
    return prev_range.resistance + width


def previous_range(ranges, r, direction: int):
    """Rango que originó el movimiento anterior a `r` (ruptura en sentido contrario a `direction`)."""
    if r is None:
        return None
    prior = [p for p in ranges if p is not r and p.end is not None and p.end <= r.detected
             and p.breakout == -direction]
    return prior[-1] if prior else None


def check_objective(r, direction: int, t: int, ranges, df, cfg: Config) -> tuple[bool | None, float | None]:
    """Prueba 1: ¿el rango actual ha alcanzado el objetivo del movimiento anterior?"""
    prev = previous_range(ranges, r, direction)
    obj = pf_objective(prev, direction, df, cfg)
    if obj is None:
        return None, None
    a, b = r.start, min(t, r.end if r.end is not None else t)
    box_tol = cfg.pf_box_atr * float(np.nanmedian(df["atr"].to_numpy()[a:b + 1]))
    if direction > 0:
        reached = df["low"].to_numpy()[prev.end:b + 1].min() <= obj + box_tol
    else:
        reached = df["high"].to_numpy()[prev.end:b + 1].max() >= obj - box_tol
    return bool(reached), obj


def check_relative_strength(r, direction: int, t: int, ratio) -> bool | None:
    """Prueba 8: pendiente de log(oro / dólar) desde el inicio del rango hasta t."""
    if ratio is None or r is None:
        return None
    seg = np.asarray(ratio[r.start:t + 1], dtype=float)
    seg = seg[~np.isnan(seg)]
    if len(seg) < 10:
        return None
    y = np.log(seg)
    slope = np.polyfit(np.arange(len(y)), y, 1)[0]
    return bool(slope > 0) if direction > 0 else bool(slope < 0)
