import io
import json

import numpy as np
import pandas as pd
import pytest

from xauusd import telegram as tg
from xauusd.alertas import Watcher, collect, potential
from xauusd.config import Config
from xauusd.data import synthetic
from xauusd.engine import Engine, run_backtest
from xauusd.fuentes import DXY_CONST, DXY_WEIGHTS, dxy_from_pairs, parse_oanda
from xauusd.scoring import threshold

RELAJADO = dict(threshold_a=5, threshold_b=6, wyckoff_tests_min_b=2, min_rr=2)


class Sink:
    def __init__(self):
        self.msgs, self.photos = [], []

    def send_message(self, text):
        self.msgs.append(text)

    def send_photo(self, path, caption=""):
        self.photos.append((str(path), caption))


class GrowingSource:
    """Devuelve los datos hasta `now`, que el test va adelantando."""

    def __init__(self, df, now):
        self.df, self.now = df, pd.Timestamp(now, tz="UTC")

    def fetch(self, days):
        d = self.df[self.df.index < self.now]
        return d[d.index >= self.now - pd.Timedelta(days=days)]


def test_collect_una_entrada_por_operacion_y_claves_unicas():
    res = run_backtest(synthetic(40_000, seed=3), Config(**RELAJADO))
    alerts = collect(res)
    keys = [a.key for a in alerts]
    assert len(keys) == len(set(keys))
    entradas = [a for a in alerts if a.kind == "entrada"]
    assert len(entradas) == len(res.trades) > 0
    assert all("Entrada" in a.text and "SL" in a.text and "TP1" in a.text for a in entradas)
    # Los setups avisados pueden llegar al umbral
    cfg = res.cfg
    avisados = {a.key.split("|", 1)[1] for a in alerts if a.kind == "setup"}
    for s in res.setups:
        k = f"{s.system}|{s.direction}|{res.exec_df.index[s.t].isoformat()}"
        assert (k in avisados) == (potential(s, cfg) >= threshold(s.system, cfg))


def test_engine_en_vivo_deja_abiertas_las_operaciones():
    df = synthetic(40_000, seed=3)
    res = Engine(df, Config(**RELAJADO)).run(close_open=False)
    assert all(tr.t_exit is None for tr in res.open_trades)
    assert all(tr.exit_reason != "fin de datos" for tr in res.trades)


def test_watcher_no_envia_historico_ni_repite(tmp_path):
    df = synthetic(40_000, seed=3)
    cfg = Config(**RELAJADO)
    src = GrowingSource(df, "2024-01-12")
    sink = Sink()
    estado = tmp_path / "estado.json"
    w = Watcher(src, sink, cfg, estado, days=10, charts_dir=tmp_path / "g", max_age_min=10_000)
    assert w.step() == []  # arranque: solo marca lo histórico
    assert not sink.msgs and not sink.photos
    enviados = []
    for _ in range(2 * 24 * 3):  # 3 días en pasos de 30 min
        src.now += pd.Timedelta("30min")
        enviados += w.step()
    kinds = [a.kind for a in enviados]
    assert "setup" in kinds and "entrada" in kinds
    assert len({a.key for a in enviados}) == len(enviados)
    # Las entradas van con gráfico
    n_ent = sum(a.kind == "entrada" for a in enviados)
    assert len(sink.photos) == n_ent and all(p.endswith(".png") for p, _ in sink.photos)
    # Reinicio: con el estado guardado no se repite nada
    w2 = Watcher(src, Sink(), cfg, estado, days=10, charts_dir=tmp_path / "g", max_age_min=10_000)
    src.now += pd.Timedelta("1min")
    assert all(a.key not in {e.key for e in enviados} for a in w2.step())


def test_watcher_descarta_eventos_viejos(tmp_path):
    df = synthetic(40_000, seed=3)
    cfg = Config(**RELAJADO)
    src = GrowingSource(df, "2024-01-10")
    sink = Sink()
    w = Watcher(src, sink, cfg, tmp_path / "e.json", days=15, charts_dir=tmp_path, max_age_min=30)
    w.step()
    src.now += pd.Timedelta(days=6)  # un corte largo
    assert w.step() == []
    assert not sink.msgs and not sink.photos


def test_parse_oanda_ignora_velas_abiertas():
    payload = {"candles": [
        {"time": "2024-05-01T10:00:00.000000000Z", "volume": 12, "complete": True,
         "mid": {"o": "2300.1", "h": "2301.0", "l": "2299.5", "c": "2300.8"}},
        {"time": "2024-05-01T10:01:00.000000000Z", "volume": 3, "complete": False,
         "mid": {"o": "2300.8", "h": "2300.9", "l": "2300.7", "c": "2300.9"}},
    ]}
    df = parse_oanda(payload)
    assert len(df) == 1
    assert df.index[0] == pd.Timestamp("2024-05-01 10:00", tz="UTC")
    assert df.iloc[0]["close"] == 2300.8 and df.iloc[0]["volume"] == 12


def test_dxy_sintetico():
    idx = pd.date_range("2024-01-01", periods=3, freq="1min", tz="UTC")
    ones = {p: pd.Series(1.0, index=idx) for p in DXY_WEIGHTS}
    assert np.allclose(dxy_from_pairs(ones)["close"], DXY_CONST)
    # Si el euro sube, el dólar (DXY) baja
    up = dict(ones)
    up["EUR_USD"] = pd.Series([1.0, 1.1, 1.2], index=idx)
    assert dxy_from_pairs(up)["close"].is_monotonic_decreasing


def test_telegram_envia_mensaje_y_foto(monkeypatch, tmp_path):
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return Resp(json.dumps({"ok": True, "result": {}}).encode())

    monkeypatch.setattr(tg.urllib.request, "urlopen", fake_urlopen)
    bot = tg.Telegram("TOKEN", "123")
    bot.send_message("<b>hola</b>")
    img = tmp_path / "g.png"
    img.write_bytes(b"\x89PNG fake")
    bot.send_photo(img, "pie")
    assert calls[0].full_url.endswith("/botTOKEN/sendMessage")
    assert b"chat_id=123" in calls[0].data and b"parse_mode=HTML" in calls[0].data
    assert calls[1].full_url.endswith("/sendPhoto")
    assert b"\x89PNG fake" in calls[1].data and b'name="caption"' in calls[1].data


def test_telegram_sin_credenciales(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(tg.TelegramError):
        tg.Telegram()
