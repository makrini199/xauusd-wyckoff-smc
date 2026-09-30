"""Indicadores básicos: ATR, RSI, volumen relativo, desplazamiento y kill zones."""
import numpy as np
import pandas as pd

from .config import Config


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def rel_volume(vol: pd.Series, n: int = 20) -> pd.Series:
    """Volumen de la vela / media de las N anteriores (sin incluir la actual)."""
    return vol / vol.shift().rolling(n, min_periods=n // 2).mean()


def enrich(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Añade columnas derivadas usadas por el resto de módulos."""
    out = df.copy()
    out["atr"] = atr(out, cfg.atr_len)
    out["rsi"] = rsi(out["close"], cfg.rsi_len)
    out["rvol"] = rel_volume(out["volume"], cfg.vol_len)
    body = (out["close"] - out["open"]).abs()
    rng = (out["high"] - out["low"]).replace(0, np.nan)
    out["body"] = body
    # Desplazamiento: vela grande, de cuerpo dominante y con expansión de volumen
    out["displacement"] = (
        (body >= cfg.displacement_atr * out["atr"])
        & (body / rng >= cfg.displacement_body_ratio)
        & (out["rvol"] >= 1.0)
    )
    out["dir"] = np.sign(out["close"] - out["open"]).astype(int)
    return out


def in_killzone(ts: pd.Timestamp, cfg: Config) -> bool:
    h = ts.tz_convert("UTC").hour if ts.tzinfo else ts.hour
    return any(a <= h < b for a, b in cfg.killzones)
