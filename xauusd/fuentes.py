"""Fuentes de velas de 1 minuto para las alertas en vivo.

- OANDA (v20 REST): cuenta demo gratuita, instrumento XAU_USD.
  Variables de entorno: OANDA_TOKEN y, opcional, OANDA_ENTORNO=practice|live.
- MetaTrader 5: ver `mt5.py` (Windows, p. ej. IC Markets).
- CSV: reproduce datos históricos avanzando el reloj, para probar las alertas.

Todas devuelven solo velas cerradas, con índice UTC y columnas
open, high, low, close, volume (el volumen de OANDA es de ticks).
"""
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

from .data import COLS, load_csv

OANDA_HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}


def parse_oanda(payload: dict) -> pd.DataFrame:
    rows = [
        {"time": c["time"], "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
         "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]), "volume": float(c["volume"])}
        for c in payload.get("candles", []) if c.get("complete")
    ]
    if not rows:
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
    return df.set_index("time")[COLS]


class OandaSource:
    def __init__(self, token: str | None = None, entorno: str | None = None,
                 instrument: str = "XAU_USD", timeout: float = 30):
        self.token = token or os.environ.get("OANDA_TOKEN", "")
        if not self.token:
            raise RuntimeError("falta OANDA_TOKEN")
        self.host = OANDA_HOSTS[entorno or os.environ.get("OANDA_ENTORNO", "practice")]
        self.instrument = instrument
        self.timeout = timeout
        self.data: pd.DataFrame | None = None

    def _get(self, start: pd.Timestamp) -> pd.DataFrame:
        q = urllib.parse.urlencode({"granularity": "M1", "price": "M", "count": 5000,
                                    "from": start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")})
        req = urllib.request.Request(f"{self.host}/v3/instruments/{self.instrument}/candles?{q}",
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return parse_oanda(json.loads(r.read().decode()))

    def fetch(self, days: int) -> pd.DataFrame:
        """Últimos `days` días de velas cerradas; la primera vez descarga todo el
        histórico por páginas de 5000 y después solo lo nuevo."""
        now = pd.Timestamp.now(tz="UTC")
        start = now - pd.Timedelta(days=days)
        if self.data is not None and len(self.data):
            start = max(start, self.data.index[-1] + pd.Timedelta("1min"))
        chunks = []
        while start < now:
            part = self._get(start)
            if part.empty:
                break
            chunks.append(part)
            nxt = part.index[-1] + pd.Timedelta("1min")
            if len(part) < 5000 or nxt <= start:
                break
            start = nxt
        frames = [f for f in [self.data, *chunks] if f is not None and len(f)]
        if frames:
            df = pd.concat(frames)
            df = df[~df.index.duplicated(keep="last")].sort_index()
            self.data = df[df.index >= now - pd.Timedelta(days=days)]
        return self.data if self.data is not None else parse_oanda({})


class CsvReplaySource:
    """Reproduce un CSV histórico: `fetch` devuelve las velas anteriores a `now`."""

    def __init__(self, path: str, start: str):
        self.all = load_csv(path)
        self.now = pd.Timestamp(start, tz="UTC") if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start)

    def fetch(self, days: int) -> pd.DataFrame:
        df = self.all[self.all.index < self.now]
        return df[df.index >= self.now - pd.Timedelta(days=days)]


# Fórmula oficial del índice dólar (ICE): 50.14348112 × Π par^peso
DXY_WEIGHTS = {"EUR_USD": -0.576, "USD_JPY": 0.136, "GBP_USD": -0.119,
               "USD_CAD": 0.091, "USD_SEK": 0.042, "USD_CHF": 0.036}
DXY_CONST = 50.14348112


def dxy_from_pairs(closes: dict[str, pd.Series]) -> pd.DataFrame:
    """Reconstruye el DXY a partir del cierre de sus seis pares."""
    df = pd.concat(closes, axis=1).sort_index().ffill().dropna()
    val = DXY_CONST
    for pair, w in DXY_WEIGHTS.items():
        val = val * df[pair] ** w
    return pd.DataFrame({"open": val, "high": val, "low": val, "close": val, "volume": 0.0})


class OandaDxySource:
    """Índice dólar sintético con los seis pares de OANDA (OANDA no cotiza el DXY)."""

    def __init__(self, token: str | None = None, entorno: str | None = None):
        self.sources = {p: OandaSource(token, entorno, instrument=p) for p in DXY_WEIGHTS}

    def fetch(self, days: int) -> pd.DataFrame:
        return dxy_from_pairs({p: s.fetch(days)["close"] for p, s in self.sources.items()})
