"""MT5 solo existe en Windows: se prueba con un módulo falso."""
import types

import numpy as np
import pandas as pd

from xauusd.mt5 import Mt5DxySource, Mt5Source, detect_offset_hours, rates_to_frame

DTYPE = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
         ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]
NOW = pd.Timestamp("2026-10-01 12:00", tz="UTC").timestamp()


class FakeMT5:
    TIMEFRAME_M1 = 1

    def __init__(self, server_offset_h=3, symbols=("XAUUSD",)):
        self.off = server_offset_h * 3600
        self.symbols = set(symbols)
        self.calls = []

    def symbol_select(self, s, enable):
        return s in self.symbols

    def symbol_info_tick(self, s):
        return types.SimpleNamespace(time=int(NOW + self.off - 5)) if s in self.symbols else None

    def copy_rates_from_pos(self, s, tf, pos, n):
        self.calls.append((s, tf, pos, n))
        t0 = int(NOW + self.off) - 60 * 3  # tres velas cerradas, en hora del servidor
        rows = [(t0 + 60 * i, 1.0 + i, 2.0 + i, 0.5 + i, 1.5 + i, 10 * (i + 1), 0, 0) for i in range(3)]
        return np.array(rows, dtype=DTYPE)

    def last_error(self):
        return (0, "ok")


def test_desfase_del_servidor():
    assert detect_offset_hours(FakeMT5(3), "XAUUSD", now=NOW) == 3
    assert detect_offset_hours(FakeMT5(2), "XAUUSD", now=NOW) == 2


def test_velas_pasan_a_utc_y_sin_la_vela_abierta(monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW)
    fake = FakeMT5(3)
    df = Mt5Source("XAUUSD", mt5=fake, log=lambda *a: None).fetch(days=1)
    assert fake.calls[0][2] == 1  # desde la posición 1: la 0 está abierta
    assert fake.calls[0][3] == 1440
    assert df.index[-1] == pd.Timestamp("2026-10-01 11:59", tz="UTC")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["volume"].tolist() == [10.0, 20.0, 30.0]


def test_desfase_fijo():
    df = Mt5Source("XAUUSD", gmt_offset=2, mt5=FakeMT5(3), log=lambda *a: None).fetch(1)
    # Con +2 fijo en vez de +3 real, las horas quedan una hora por delante
    assert df.index[-1] == pd.Timestamp("2026-10-01 12:59", tz="UTC")


def test_rates_vacio():
    assert rates_to_frame(np.array([], dtype=DTYPE), 3).empty


def test_dxy_desde_mt5(monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW)
    fake = FakeMT5(3, symbols=("EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF"))
    dxy = Mt5DxySource(mt5=fake).fetch(1)
    assert len(dxy) == 3 and (dxy["close"] > 0).all()
    assert {c[0] for c in fake.calls} == {"EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF"}


def test_load_env(tmp_path, monkeypatch):
    from xauusd.__main__ import load_env
    f = tmp_path / ".env"
    f.write_text("# comentario\nTELEGRAM_TOKEN=abc:123\nTELEGRAM_CHAT_ID = '42'\nYA=no\n", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("YA", "si")
    load_env(str(f))
    import os
    assert os.environ["TELEGRAM_TOKEN"] == "abc:123"
    assert os.environ["TELEGRAM_CHAT_ID"] == "42"
    assert os.environ["YA"] == "si"  # no pisa lo ya definido
