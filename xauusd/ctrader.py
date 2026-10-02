"""Fuente de velas de 1 minuto desde la API abierta de cTrader (p. ej. IC Markets).

Usa la variante JSON sobre WebSocket de la cTrader Open API, así que funciona
en cualquier sistema (un VPS Linux barato) sin MetaTrader ni Windows.

Credenciales (en .env):
    CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET   de tu aplicación en openapi.ctrader.com
    CTRADER_ACCESS_TOKEN, CTRADER_REFRESH_TOKEN del Playground de esa aplicación
    CTRADER_ENTORNO   demo (por defecto) o live, según la cuenta
    CTRADER_CUENTA    número de cuenta (login) de IC Markets; opcional si solo hay una

El token de acceso caduca (unos 30 días): se renueva solo con el refresh
token y los nuevos se guardan en `ctrader_tokens.json`.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from .data import COLS
from .fuentes import DXY_WEIGHTS, dxy_from_pairs

HOSTS = {"demo": "wss://demo.ctraderapi.com:5036", "live": "wss://live.ctraderapi.com:5036"}
TOKEN_URL = "https://openapi.ctrader.com/apps/token"

# Tipos de mensaje (ProtoOAPayloadType / ProtoPayloadType)
ERROR_RES = 50
HEARTBEAT = 51
APP_AUTH_REQ, APP_AUTH_RES = 2100, 2101
ACCOUNT_AUTH_REQ, ACCOUNT_AUTH_RES = 2102, 2103
SYMBOLS_LIST_REQ, SYMBOLS_LIST_RES = 2114, 2115
TRENDBARS_REQ, TRENDBARS_RES = 2137, 2138
OA_ERROR_RES = 2142
ACCOUNTS_BY_TOKEN_REQ, ACCOUNTS_BY_TOKEN_RES = 2149, 2150

PERIOD_M1 = 1
PRICE_SCALE = 100_000          # los precios llegan en 1/100000 de unidad
CHUNK = pd.Timedelta(days=2)   # ~2880 velas M1 por petición; si aun así hay más (hasMore), se parte
REQ_PAUSE = 0.25               # máximo ~5 peticiones históricas por segundo

TOKEN_ERRORS = {"CH_ACCESS_TOKEN_INVALID", "OA_AUTH_TOKEN_EXPIRED"}
# Sesión perdida (p. ej. el servidor cerró la conexión): basta con reconectar
RECONNECT_ERRORS = {"TIMEOUT", "ACCOUNT_NOT_AUTHORIZED", "CH_CLIENT_NOT_AUTHENTICATED"}


class CTraderError(RuntimeError):
    def __init__(self, code: str, description: str = ""):
        super().__init__(f"cTrader {code}: {description}")
        self.code = code


def _ws_connect(url: str, timeout: float):
    try:
        import websocket
    except ImportError as e:
        raise RuntimeError("falta el paquete websocket-client: pip install websocket-client") from e
    return websocket.create_connection(url, timeout=timeout)


class Tokens:
    """Access/refresh token, con renovación y guardado en disco."""

    def __init__(self, client_id: str, client_secret: str, path: str | Path = "ctrader_tokens.json",
                 http=None):
        self.client_id, self.client_secret = client_id, client_secret
        self.path = Path(path)
        self.http = http or _http_get_json
        env_access = os.environ.get("CTRADER_ACCESS_TOKEN", "")
        env_refresh = os.environ.get("CTRADER_REFRESH_TOKEN", "")
        saved = json.loads(self.path.read_text()) if self.path.exists() else {}
        # Los tokens renovados guardados valen mientras no pongas otros nuevos en .env
        if saved and saved.get("env_access") == env_access:
            self.access, self.refresh_token = saved.get("access", ""), saved.get("refresh", "")
        else:
            self.access, self.refresh_token = env_access, env_refresh
        self._env_access = env_access
        if not self.access:
            raise RuntimeError("falta CTRADER_ACCESS_TOKEN")

    def refresh(self):
        if not self.refresh_token:
            raise RuntimeError("el token de cTrader ha caducado y no hay CTRADER_REFRESH_TOKEN para renovarlo")
        q = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": self.refresh_token,
                                    "client_id": self.client_id, "client_secret": self.client_secret})
        out = self.http(f"{TOKEN_URL}?{q}")
        access = out.get("accessToken") or out.get("access_token")
        if not access:
            raise RuntimeError(f"no se pudo renovar el token de cTrader: {out}")
        self.access = access
        self.refresh_token = out.get("refreshToken") or out.get("refresh_token") or self.refresh_token
        self.path.write_text(json.dumps({"access": self.access, "refresh": self.refresh_token,
                                         "env_access": self._env_access}))


def _http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


class CTraderClient:
    """Conexión autenticada a una cuenta. Se reconecta sola si se cae."""

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 entorno: str | None = None, cuenta: str | None = None, tokens: Tokens | None = None,
                 connect=_ws_connect, timeout: float = 30, log=print):
        self.client_id = client_id or os.environ.get("CTRADER_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("CTRADER_CLIENT_SECRET", "")
        if not self.client_id or not self.client_secret:
            raise RuntimeError("faltan CTRADER_CLIENT_ID y/o CTRADER_CLIENT_SECRET")
        self.entorno = (entorno or os.environ.get("CTRADER_ENTORNO", "demo")).lower()
        self.cuenta = str(cuenta or os.environ.get("CTRADER_CUENTA", "")).strip()
        self.tokens = tokens or Tokens(self.client_id, self.client_secret)
        self._connect = connect
        self.timeout = timeout
        self.log = log
        self.ws = None
        self.account_id: int | None = None
        self.symbols: dict[str, int] = {}
        self._msg = 0

    # --- transporte --------------------------------------------------------
    def _send(self, payload_type: int, payload: dict) -> dict:
        self._msg += 1
        mid = f"m{self._msg}"
        self.ws.send(json.dumps({"clientMsgId": mid, "payloadType": payload_type, "payload": payload}))
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            pt = msg.get("payloadType")
            if pt == HEARTBEAT:
                self.ws.send(json.dumps({"payloadType": HEARTBEAT, "payload": {}}))
                continue
            if msg.get("clientMsgId") != mid:
                continue  # eventos u otras respuestas
            p = msg.get("payload", {})
            if pt in (OA_ERROR_RES, ERROR_RES):
                raise CTraderError(p.get("errorCode", "?"), p.get("description", ""))
            return p
        raise CTraderError("TIMEOUT", f"sin respuesta a {payload_type}")

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None

    def _open(self):
        self.close()
        self.ws = self._connect(HOSTS[self.entorno], self.timeout)
        self._send(APP_AUTH_REQ, {"clientId": self.client_id, "clientSecret": self.client_secret})
        if self.account_id is None:
            self.account_id = self._pick_account()
        self._send(ACCOUNT_AUTH_REQ, {"ctidTraderAccountId": self.account_id,
                                      "accessToken": self.tokens.access})
        if not self.symbols:
            res = self._send(SYMBOLS_LIST_REQ, {"ctidTraderAccountId": self.account_id})
            self.symbols = {s["symbolName"].upper(): int(s["symbolId"]) for s in res.get("symbol", [])
                            if "symbolName" in s}

    def _pick_account(self) -> int:
        accs = self.accounts()
        live = self.entorno == "live"
        cands = [a for a in accs if bool(a.get("isLive")) == live]
        if self.cuenta:
            cands = [a for a in cands if str(a.get("traderLogin")) == self.cuenta]
        if not cands:
            raise RuntimeError(f"no hay ninguna cuenta {self.entorno} "
                               f"{'con número ' + self.cuenta + ' ' if self.cuenta else ''}"
                               "autorizada para este token (revisa CTRADER_ENTORNO / CTRADER_CUENTA)")
        if len(cands) > 1 and not self.cuenta:
            self.log("varias cuentas autorizadas; se usa la primera. Fija CTRADER_CUENTA para elegir: "
                     + ", ".join(str(a.get("traderLogin")) for a in cands))
        return int(cands[0]["ctidTraderAccountId"])

    def accounts(self) -> list[dict]:
        res = self._send(ACCOUNTS_BY_TOKEN_REQ, {"accessToken": self.tokens.access})
        return res.get("ctidTraderAccount", [])

    def call(self, fn):
        """Ejecuta fn() con la conexión abierta; reconecta o renueva el token si hace falta."""
        for attempt in range(3):
            try:
                if self.ws is None:
                    self._open()
                return fn()
            except CTraderError as e:
                self.close()
                if e.code in TOKEN_ERRORS and attempt == 0:
                    self.log("token de cTrader caducado: renovando…")
                    self.tokens.refresh()
                    continue
                if e.code in RECONNECT_ERRORS and attempt < 2:
                    continue
                raise
            except (OSError, ConnectionError) as e:
                self.close()
                if attempt == 2:
                    raise RuntimeError(f"sin conexión con cTrader: {e}") from e
                time.sleep(2)

    # --- datos -------------------------------------------------------------
    def symbol_id(self, name: str) -> int:
        if not self.symbols:
            self.call(lambda: None)
        sid = self.symbols.get(name.upper())
        if sid is None:
            raise RuntimeError(f"símbolo {name} no encontrado en la cuenta de cTrader")
        return sid

    def trendbars(self, name: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Velas M1 en [start, end), en trozos que respeten el límite de la API."""
        sid = self.symbol_id(name)
        frames = []
        pending = []
        a = start
        while a < end:
            pending.append((a, min(a + CHUNK, end)))
            a = pending[-1][1]
        while pending:
            a, b = pending.pop(0)
            req = {"ctidTraderAccountId": self.account_id, "symbolId": sid, "period": PERIOD_M1,
                   "fromTimestamp": int(a.timestamp() * 1000), "toTimestamp": int(b.timestamp() * 1000)}
            res = self.call(lambda req=req: self._send(TRENDBARS_REQ, req))
            if res.get("hasMore") and b - a > pd.Timedelta("1h"):
                mid = a + (b - a) / 2  # respuesta recortada: se pide en dos mitades
                pending[:0] = [(a, mid), (mid, b)]
            else:
                frames.append(parse_trendbars(res))
            if pending:
                time.sleep(REQ_PAUSE)
        frames = [f for f in frames if len(f)]
        if not frames:
            return parse_trendbars({})
        df = pd.concat(frames)
        return df[~df.index.duplicated(keep="last")].sort_index()


def parse_trendbars(payload: dict) -> pd.DataFrame:
    rows = []
    for b in payload.get("trendbar", []):
        low = int(b.get("low", 0))
        rows.append({
            "time": pd.Timestamp(int(b["utcTimestampInMinutes"]) * 60, unit="s", tz="UTC"),
            "open": (low + int(b.get("deltaOpen", 0))) / PRICE_SCALE,
            "high": (low + int(b.get("deltaHigh", 0))) / PRICE_SCALE,
            "low": low / PRICE_SCALE,
            "close": (low + int(b.get("deltaClose", 0))) / PRICE_SCALE,
            "volume": float(b.get("volume", 0)),
        })
    if not rows:
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    return pd.DataFrame(rows).set_index("time")[COLS]


class CTraderSource:
    """Velas cerradas de 1 minuto de un símbolo; descarga todo la primera vez y luego solo lo nuevo."""

    def __init__(self, client: CTraderClient, symbol: str = "XAUUSD", now=None):
        self.client = client
        self.symbol = symbol
        self.data: pd.DataFrame | None = None
        self._now = now or (lambda: pd.Timestamp.now(tz="UTC"))

    def fetch(self, days: int) -> pd.DataFrame:
        now = self._now()
        closed_until = now.floor("1min")  # la vela de este minuto aún está abierta
        start = now - pd.Timedelta(days=days)
        if self.data is not None and len(self.data):
            start = max(start, self.data.index[-1] - pd.Timedelta("2min"))  # solapa por si se corrigió
        new = self.client.trendbars(self.symbol, start, closed_until)
        new = new[new.index < closed_until]
        frames = [f for f in (self.data, new) if f is not None and len(f)]
        if frames:
            df = pd.concat(frames)
            df = df[~df.index.duplicated(keep="last")].sort_index()
            self.data = df[df.index >= now - pd.Timedelta(days=days)]
        return self.data if self.data is not None else parse_trendbars({})


class CTraderDxySource:
    """Índice dólar reconstruido con sus seis pares desde cTrader."""

    def __init__(self, client: CTraderClient, now=None):
        self.sources = {p: CTraderSource(client, p.replace("_", ""), now) for p in DXY_WEIGHTS}

    def fetch(self, days: int) -> pd.DataFrame:
        return dxy_from_pairs({p: s.fetch(days)["close"] for p, s in self.sources.items()})
