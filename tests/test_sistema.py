import numpy as np
import pandas as pd
import pytest

from xauusd.config import Config
from xauusd.data import resample, synthetic
from xauusd.engine import run_backtest
from xauusd.indicators import enrich
from xauusd.poi import FVG, OrderBlock, find_fvgs
from xauusd.risk import RiskManager
from xauusd.scoring import max_score, risk_fraction, score
from xauusd.structure import StructureTracker
from xauusd.wyckoff import WyckoffTracker, shakeout_ok


def frame(closes, vol=None, spread=0.3, start="2024-01-02 00:00"):
    c = np.asarray(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    idx = pd.date_range(start, periods=len(c), freq="15min", tz="UTC")
    v = np.full(len(c), 100.0) if vol is None else np.asarray(vol, dtype=float)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread,
                         "low": np.minimum(o, c) - spread, "close": c, "volume": v}, index=idx)


def zigzag(legs):
    out = [legs[0]]
    for a, b in zip(legs, legs[1:]):
        out += list(np.linspace(a, b, 8)[1:])
    return out


def test_puntuacion_maxima_17():
    cfg = Config()
    assert max_score(cfg) == 17
    assert score({k: True for k in cfg.weights}, cfg) == 17
    assert risk_fraction(12, cfg) == 0.005
    assert risk_fraction(16, cfg) == 0.005
    assert risk_fraction(17, cfg) == 0.01


def test_bos_alcista_en_tendencia_de_maximos_crecientes():
    cfg = Config(swing_len=2)
    df = enrich(frame(zigzag([100, 105, 102, 108, 104, 112, 107, 116])), cfg)
    st = StructureTracker(df, cfg)
    for t in range(len(df)):
        st.step(t)
    ups = [e for e in st.events if e.direction > 0]
    assert ups and st.trend == 1
    assert all(e.kind == "BOS" for e in ups)


def test_choch_tras_romper_el_minimo_protegido():
    cfg = Config(swing_len=2)
    df = enrich(frame(zigzag([100, 105, 102, 108, 104, 112, 107, 110, 95])), cfg)
    st = StructureTracker(df, cfg)
    for t in range(len(df)):
        st.step(t)
    assert st.trend == -1
    assert any(e.kind == "CHoCH" and e.direction < 0 for e in st.events)


def test_fvg_alcista():
    cfg = Config(fvg_min_atr=0)
    df = frame([100, 100.5, 104, 104.5])
    df.loc[df.index[2], "low"] = 101.5  # hueco entre high[1] (~100.8) y low[3]
    df.loc[df.index[3], "low"] = 103.0
    df["atr"] = 1.0
    gaps = find_fvgs(df, 0, 3, +1, cfg)
    assert gaps and all(g.low < g.high for g in gaps)
    assert gaps[-1].mid == pytest.approx((gaps[-1].low + gaps[-1].high) / 2)


def test_order_block_toques_y_mitigacion():
    ob = OrderBlock(0, 0, +1, 100, 101)
    ob.update(103, 100.5, 102)   # toque 1
    ob.update(104, 102, 103)     # fuera
    ob.update(103, 100.8, 101.5) # toque 2
    assert ob.touches == 2 and not ob.mitigated
    ob.update(101, 98, 99)       # cierra por debajo del OB
    assert ob.mitigated


def _range_then(tail_close, tail_vol, n=60):
    rng = np.random.default_rng(0)
    base = list(100 + rng.uniform(-1, 1, n))
    base[5] = 99.0  # soporte
    closes = [110 - i for i in range(10)] + base + tail_close
    vols = [100] * 10 + [100] * n + tail_vol
    return frame(closes, vols, spread=0.2)


def test_spring_valido_con_volumen_bajo():
    cfg = Config(range_min_bars=30, range_max_width_atr=12)
    df = enrich(_range_then([98.2, 100.0, 100.3, 100.2], [40, 60, 60, 60]), cfg)
    wy = WyckoffTracker(df, cfg)
    for t in range(len(df)):
        wy.step(t)
    r = wy.ranges[0]
    assert shakeout_ok(r, +1, len(df) - 1)


def test_spring_invalido_con_ruptura_fuerte():
    cfg = Config(range_min_bars=30, range_max_width_atr=12)
    df = enrich(_range_then([94.0, 100.0, 100.3, 100.2], [600, 60, 60, 60]), cfg)
    wy = WyckoffTracker(df, cfg)
    for t in range(len(df)):
        wy.step(t)
    assert not shakeout_ok(wy.ranges[0], +1, len(df) - 1)


def test_limites_de_riesgo():
    cfg = Config()
    rm = RiskManager(cfg)
    rm.new_bar(pd.Timestamp("2024-01-02 10:00", tz="UTC"))
    assert rm.can_open("A", 1, 0.01, [], True) is None
    rm.on_open(); rm.on_close(-1000, True)
    rm.on_open(); rm.on_close(-1000, True)
    assert "diarias" in rm.can_open("A", 1, 0.01, [], True) or "pérdidas" in rm.can_open("A", 1, 0.01, [], True)
    rm.new_bar(pd.Timestamp("2024-01-03 10:00", tz="UTC"))
    rm.on_close(-1500, True)  # -3.5% en la semana
    assert "semanal" in rm.can_open("A", 1, 0.01, [], True)
    rm2 = RiskManager(cfg)
    rm2.on_close(-5000, True)
    assert rm2.halted


def test_sin_mirar_al_futuro():
    """Las operaciones cerradas antes del corte deben ser idénticas con y sin los datos posteriores."""
    cfg = Config(threshold_a=5, threshold_b=6, wyckoff_tests_min_b=2, min_rr=2)
    df = synthetic(40_000, seed=3)
    full = run_backtest(df, cfg)
    cut = df.index[25_000]
    part = run_backtest(df[df.index < cut], cfg)
    last = part.exec_df.index[-1] - pd.Timedelta("1D")

    def key(res):
        return [(res.exec_df.index[t.t_entry], t.system, round(t.entry, 6), round(t.pnl, 4))
                for t in res.trades if res.exec_df.index[t.t_exit] < last]

    k_full, k_part = key(full), key(part)
    assert k_part, "el test necesita al menos una operación antes del corte"
    assert k_full == k_part


def test_resample_no_mezcla_velas():
    df = synthetic(120)
    r = resample(df, "15min")
    first = df.iloc[:15]
    assert r["high"].iloc[0] == first["high"].max()
    assert r["volume"].iloc[0] == pytest.approx(first["volume"].sum())
