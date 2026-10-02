"""Fuente de velas de 1 minuto desde MetaTrader 5 (p. ej. IC Markets).

Necesita Windows, el terminal MT5 instalado y el paquete `MetaTrader5`
(`pip install MetaTrader5`). Si el terminal ya está abierto con tu cuenta,
no hace falta nada más; si no, se puede iniciar sesión con las variables de
entorno MT5_LOGIN, MT5_PASSWORD y MT5_SERVER (p. ej. ICMarketsSC-Demo).

Las horas de MT5 vienen en la hora del servidor del bróker (IC Markets usa
GMT+2 en invierno y GMT+3 en verano), no en UTC. El desfase se detecta
comparando el último tick con el reloj del ordenador, o se fija con
`gmt_offset` (`--mt5-gmt`). Es importante: las kill zones van en UTC.
"""
import os
import time

import pandas as pd

from .data import COLS
from .fuentes import DXY_WEIGHTS, dxy_from_pairs


def _import_mt5():
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError as e:
        raise RuntimeError("falta el paquete MetaTrader5 (solo Windows): pip install MetaTrader5") from e
    return mt5


def connect(mt5=None):
    """Conecta con el terminal MT5 abierto (o inicia sesión con MT5_LOGIN/PASSWORD/SERVER)."""
    mt5 = mt5 or _import_mt5()
    kw = {}
    if os.environ.get("MT5_LOGIN"):
        kw = {"login": int(os.environ["MT5_LOGIN"]), "password": os.environ.get("MT5_PASSWORD", ""),
              "server": os.environ.get("MT5_SERVER", "")}
    if os.environ.get("MT5_RUTA"):
        kw["path"] = os.environ["MT5_RUTA"]
    if not mt5.initialize(**kw):
        raise RuntimeError(f"no se pudo conectar con MetaTrader 5: {mt5.last_error()}. "
                           "¿Está el terminal abierto y con sesión iniciada?")
    return mt5


def detect_offset_hours(mt5, symbol: str, now: float | None = None) -> int:
    """Horas que la hora del servidor va por delante de UTC, según el último tick."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"símbolo {symbol} no disponible en MT5; actívalo en la Observación del mercado")
    now = time.time() if now is None else now
    return int(round((tick.time - now) / 3600))


def rates_to_frame(rates, offset_hours: int) -> pd.DataFrame:
    """Convierte el array de MT5 a velas en UTC (columnas estándar)."""
    df = pd.DataFrame(rates)
    if df.empty:
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    idx = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=offset_hours)
    out = pd.DataFrame({
        "open": df["open"].astype(float).to_numpy(), "high": df["high"].astype(float).to_numpy(),
        "low": df["low"].astype(float).to_numpy(), "close": df["close"].astype(float).to_numpy(),
        "volume": df["tick_volume"].astype(float).to_numpy(),
    }, index=pd.DatetimeIndex(idx, name="time"))
    return out[~out.index.duplicated(keep="last")].sort_index()


class Mt5Source:
    """Velas cerradas de 1 minuto de un símbolo de MT5, en UTC."""

    def __init__(self, symbol: str = "XAUUSD", gmt_offset: int | None = None, mt5=None, log=print):
        self.mt5 = mt5 or connect()
        self.symbol = symbol
        self.log = log
        if not self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"símbolo {symbol} no encontrado en MT5")
        self.offset = gmt_offset
        self.fixed = gmt_offset is not None

    def fetch(self, days: int) -> pd.DataFrame:
        if not self.fixed:
            off = detect_offset_hours(self.mt5, self.symbol)
            if off != self.offset:
                self.log(f"{self.symbol}: hora del servidor MT5 = UTC{off:+d}")
                self.offset = off
        n = days * 24 * 60
        # Posición 1 = última vela cerrada (la 0 es la que se está formando)
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 1, n)
        if rates is None:
            raise RuntimeError(f"MT5 no devolvió velas de {self.symbol}: {self.mt5.last_error()}")
        return rates_to_frame(rates, self.offset)


# Nombres de los pares del DXY en MT5 (IC Markets los usa sin separador)
MT5_PAIRS = {p: p.replace("_", "") for p in DXY_WEIGHTS}


class Mt5DxySource:
    """Índice dólar reconstruido con sus seis pares desde MT5."""

    def __init__(self, gmt_offset: int | None = None, mt5=None, suffix: str = "", log=print):
        mt5 = mt5 or connect()
        self.sources = {p: Mt5Source(s + suffix, gmt_offset, mt5, log=lambda *a: None)
                        for p, s in MT5_PAIRS.items()}

    def fetch(self, days: int) -> pd.DataFrame:
        return dxy_from_pairs({p: s.fetch(days)["close"] for p, s in self.sources.items()})
