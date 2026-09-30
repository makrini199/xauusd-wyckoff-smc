"""Carga y remuestreo de velas XAUUSD.

Formato esperado: el CSV de `dukascopy-node` (timestamp, open, high, low,
close, volume). El timestamp puede venir en milisegundos Unix o como fecha
ISO; siempre se trabaja en UTC (= GMT).
"""
from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["open", "high", "low", "close", "volume"]


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts_col = next(c for c in df.columns if c in ("timestamp", "time", "date", "datetime"))
    ts = df[ts_col]
    if np.issubdtype(ts.dtype, np.number):
        # Unix en s, ms o µs según la magnitud (dukascopy-node usa ms)
        mag = float(np.nanmax(np.abs(ts.to_numpy()))) if len(ts) else 0
        unit = "us" if mag > 1e14 else "ms" if mag > 1e11 else "s"
        idx = pd.to_datetime(ts, unit=unit, utc=True)
    else:
        idx = pd.to_datetime(ts, utc=True)
    df = df.set_index(idx)[COLS].astype(float)
    df.index.name = "time"
    df = df[~df.index.duplicated()].sort_index()
    return df


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Agrega velas a una temporalidad mayor. La vela queda etiquetada por su
    apertura, así que solo se conoce completa en `index + tf`."""
    out = df.resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])


def synthetic(n_bars: int = 20_000, seed: int = 7, start: str = "2024-01-01") -> pd.DataFrame:
    """Serie sintética de 1 minuto con tendencias, rangos y picos de volumen.

    Solo sirve para probar el pipeline de punta a punta; no dice nada sobre
    la rentabilidad real del sistema.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n_bars, freq="1min", tz="UTC")
    # Regímenes: alterna tendencia y rango cada 1500-4000 minutos
    drift = np.zeros(n_bars)
    i = 0
    while i < n_bars:
        length = int(rng.integers(1500, 4000))
        drift[i:i + length] = rng.choice([-0.04, 0.0, 0.0, 0.04])
        i += length
    hour = idx.hour.to_numpy()
    active = ((hour >= 13) & (hour < 17)) | ((hour >= 7) & (hour < 10))
    vol_scale = np.where(active, 0.45, 0.22)
    rets = drift + rng.standard_t(4, n_bars) * vol_scale
    close = 2000 + np.cumsum(rets)
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, vol_scale, n_bars))
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol_scale, n_bars))
    volume = rng.gamma(2.0, 50, n_bars) * np.where(active, 2.0, 1.0) * (1 + np.abs(rets) / vol_scale)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    ).rename_axis("time")
