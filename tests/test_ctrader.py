"""cTrader Open API (JSON sobre WebSocket) contra un servidor simulado."""
import json

import pandas as pd
import pytest

from xauusd import ctrader as ct

NOW = pd.Timestamp("2026-10-01 12:00:30", tz="UTC")
SYMBOLS = {"XAUUSD": 41, "EURUSD": 1, "USDJPY": 4, "GBPUSD": 2, "USDCAD": 8, "USDSEK": 30, "USDCHF": 6}
ACCOUNTS = [{"ctidTraderAccountId": 111, "isLive": False, "traderLogin": 5001},
            {"ctidTraderAccountId": 222, "isLive": True, "traderLogin": 7001}]


class FakeServer:
    def __init__(self, valid_token="TOK", max_bars=None, drop_after=None):
        self.valid_token = valid_token
        self.max_bars = max_bars
        self.drop_after = drop_after  # simula que el servidor olvida la sesión
        self.requests = []
        self.connections = 0
        self.now = NOW

    def connect(self, url, timeout):
        self.connections += 1
        self.url = url
        return FakeWS(self)

    def reply(self, msg):
        pt, p, mid = msg["payloadType"], msg.get("payload", {}), msg.get("clientMsgId")
        self.requests.append((pt, p))
        out = []
        if pt == ct.HEARTBEAT:
            return out
        # Un latido y un evento ajeno antes de la respuesta, como en la realidad
        out.append({"payloadType": ct.HEARTBEAT, "payload": {}})
        out.append({"payloadType": 2126, "payload": {"spot": 1}})
        if pt == ct.APP_AUTH_REQ:
            out.append({"clientMsgId": mid, "payloadType": ct.APP_AUTH_RES, "payload": {}})
        elif pt == ct.ACCOUNTS_BY_TOKEN_REQ:
            if p["accessToken"] != self.valid_token:
                out.append(self._err(mid, "CH_ACCESS_TOKEN_INVALID"))
            else:
                out.append({"clientMsgId": mid, "payloadType": ct.ACCOUNTS_BY_TOKEN_RES,
                            "payload": {"ctidTraderAccount": ACCOUNTS}})
        elif pt == ct.ACCOUNT_AUTH_REQ:
            if p["accessToken"] != self.valid_token:
                out.append(self._err(mid, "CH_ACCESS_TOKEN_INVALID"))
            else:
                out.append({"clientMsgId": mid, "payloadType": ct.ACCOUNT_AUTH_RES, "payload": {}})
        elif pt == ct.SYMBOLS_LIST_REQ:
            out.append({"clientMsgId": mid, "payloadType": ct.SYMBOLS_LIST_RES, "payload": {
                "symbol": [{"symbolId": v, "symbolName": k} for k, v in SYMBOLS.items()]}})
        elif pt == ct.TRENDBARS_REQ:
            a, b = p["fromTimestamp"] // 60000, p["toTimestamp"] // 60000
            # El servidor también devuelve la vela abierta del minuto actual
            last = min(b, int(self.now.timestamp()) // 60)
            if self.drop_after is not None:
                self.drop_after -= 1
                if self.drop_after < 0:
                    self.drop_after = None
                    out.append(self._err(mid, "ACCOUNT_NOT_AUTHORIZED"))
                    return out
            bars = [{"utcTimestampInMinutes": m, "low": 200000000 + m % 1000, "deltaOpen": 50,
                     "deltaClose": 120, "deltaHigh": 300, "volume": 7} for m in range(a, last + 1)]
            more = self.max_bars is not None and len(bars) > self.max_bars
            if more:
                bars = bars[-self.max_bars:]
            out.append({"clientMsgId": mid, "payloadType": ct.TRENDBARS_RES,
                        "payload": {"trendbar": bars, "hasMore": more}})
        return out

    @staticmethod
    def _err(mid, code):
        return {"clientMsgId": mid, "payloadType": ct.OA_ERROR_RES,
                "payload": {"errorCode": code, "description": "token"}}


class FakeWS:
    def __init__(self, server):
        self.server, self.queue = server, []

    def send(self, data):
        self.queue += self.server.reply(json.loads(data))

    def recv(self):
        return json.dumps(self.queue.pop(0))

    def close(self):
        pass


@pytest.fixture(autouse=True)
def entorno(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ct, "REQ_PAUSE", 0)
    for k, v in {"CTRADER_CLIENT_ID": "cid", "CTRADER_CLIENT_SECRET": "sec",
                 "CTRADER_ACCESS_TOKEN": "TOK", "CTRADER_REFRESH_TOKEN": "REF"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CTRADER_CUENTA", raising=False)
    monkeypatch.delenv("CTRADER_ENTORNO", raising=False)


def client(server, **kw):
    return ct.CTraderClient(connect=server.connect, log=lambda *a: None, **kw)


def test_precios_en_cienmilesimas():
    df = ct.parse_trendbars({"trendbar": [{"utcTimestampInMinutes": 29_000_000, "low": 265012345,
                                           "deltaOpen": 100, "deltaHigh": 5000, "deltaClose": 2000,
                                           "volume": 42}]})
    r = df.iloc[0]
    assert (r.low, r.open, r.high, r.close, r.volume) == pytest.approx(
        (2650.12345, 2650.12445, 2650.17345, 2650.14345, 42))
    assert df.index[0] == pd.Timestamp(29_000_000 * 60, unit="s", tz="UTC")


def test_fetch_cuenta_demo_velas_cerradas_y_trozos():
    srv = FakeServer()
    c = client(srv)
    df = ct.CTraderSource(c, "XAUUSD", now=lambda: NOW).fetch(days=7)
    assert srv.url == ct.HOSTS["demo"]
    assert c.account_id == 111
    assert df.index[-1] == pd.Timestamp("2026-10-01 11:59", tz="UTC")  # sin la vela abierta
    assert df.index.is_monotonic_increasing and not df.index.duplicated().any()
    reqs = [p for t, p in srv.requests if t == ct.TRENDBARS_REQ]
    assert len(reqs) == 4  # 7 días en trozos de 2
    assert all(r["toTimestamp"] - r["fromTimestamp"] <= 2 * 86_400_000 for r in reqs)
    assert all(r["symbolId"] == 41 and r["period"] == ct.PERIOD_M1 for r in reqs)
    # Responde a los latidos del servidor
    assert any(t == ct.HEARTBEAT for t, _ in srv.requests)


def test_fetch_incremental():
    srv = FakeServer()
    now = [NOW]
    src = ct.CTraderSource(client(srv), "XAUUSD", now=lambda: now[0])
    src.fetch(days=2)
    n = len(srv.requests)
    now[0] = srv.now = NOW + pd.Timedelta("3min")
    df = src.fetch(days=2)
    nuevas = [p for t, p in srv.requests[n:] if t == ct.TRENDBARS_REQ]
    assert len(nuevas) == 1
    assert nuevas[0]["toTimestamp"] - nuevas[0]["fromTimestamp"] <= 6 * 60_000
    assert df.index[-1] == pd.Timestamp("2026-10-01 12:02", tz="UTC")
    assert srv.connections == 1  # reutiliza la conexión


def test_elegir_cuenta_real_por_numero(monkeypatch):
    monkeypatch.setenv("CTRADER_ENTORNO", "live")
    monkeypatch.setenv("CTRADER_CUENTA", "7001")
    srv = FakeServer()
    c = client(srv)
    c.call(lambda: None)
    assert c.account_id == 222 and srv.url == ct.HOSTS["live"]


def test_cuenta_inexistente(monkeypatch):
    monkeypatch.setenv("CTRADER_CUENTA", "9999")
    with pytest.raises(RuntimeError, match="9999"):
        client(FakeServer()).call(lambda: None)


def test_token_caducado_se_renueva_y_se_guarda(tmp_path):
    srv = FakeServer(valid_token="NUEVO")
    pedidas = []

    def http(url):
        pedidas.append(url)
        return {"accessToken": "NUEVO", "refreshToken": "REF2", "expiresIn": 2628000}

    tokens = ct.Tokens("cid", "sec", http=http)
    c = client(srv, tokens=tokens)
    df = ct.CTraderSource(c, "XAUUSD", now=lambda: NOW).fetch(days=1)
    assert len(df) > 0
    assert "grant_type=refresh_token" in pedidas[0] and "refresh_token=REF" in pedidas[0]
    saved = json.loads((tmp_path / "ctrader_tokens.json").read_text())
    assert saved["access"] == "NUEVO" and saved["refresh"] == "REF2"
    # Al reiniciar se usan los renovados…
    assert ct.Tokens("cid", "sec").access == "NUEVO"


def test_tokens_nuevos_en_env_mandan(monkeypatch, tmp_path):
    (tmp_path / "ctrader_tokens.json").write_text(json.dumps(
        {"access": "VIEJO_RENOVADO", "refresh": "R", "env_access": "TOK"}))
    assert ct.Tokens("cid", "sec").access == "VIEJO_RENOVADO"
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "PEGADO_A_MANO")
    assert ct.Tokens("cid", "sec").access == "PEGADO_A_MANO"


def test_simbolo_inexistente():
    with pytest.raises(RuntimeError, match="XAUEUR"):
        ct.CTraderSource(client(FakeServer()), "XAUEUR", now=lambda: NOW).fetch(1)


def test_dxy_desde_ctrader():
    srv = FakeServer()
    dxy = ct.CTraderDxySource(client(srv), now=lambda: NOW).fetch(1)
    assert len(dxy) > 0 and (dxy["close"] > 0).all()
    ids = {p["symbolId"] for t, p in srv.requests if t == ct.TRENDBARS_REQ}
    assert ids == {1, 4, 2, 8, 30, 6}


def test_faltan_credenciales(monkeypatch):
    monkeypatch.delenv("CTRADER_CLIENT_ID")
    with pytest.raises(RuntimeError, match="CTRADER_CLIENT_ID"):
        ct.CTraderClient(connect=FakeServer().connect)


def test_respuesta_recortada_se_pide_por_mitades():
    srv = FakeServer(max_bars=1000)
    df = ct.CTraderSource(client(srv), "XAUUSD", now=lambda: NOW).fetch(days=2)
    esperado = pd.date_range(NOW.floor("1min") - pd.Timedelta(days=2), NOW.floor("1min"),
                             freq="1min", inclusive="left")
    # No falta ninguna vela pese al recorte del servidor
    assert df.index.equals(esperado[esperado >= NOW - pd.Timedelta(days=2)])


def test_reconecta_si_el_servidor_pierde_la_sesion():
    srv = FakeServer(drop_after=1)
    df = ct.CTraderSource(client(srv), "XAUUSD", now=lambda: NOW).fetch(days=3)
    assert len(df) > 0 and srv.connections == 2
