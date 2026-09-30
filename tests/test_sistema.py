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


# --- Confirmación en temporalidad menor ------------------------------------
from xauusd.engine import Engine, Setup  # noqa: E402


def _engine_with_ltf(highs, lows, closes):
    cfg = Config(threshold_a=0, threshold_b=0, ltf_swing_len=2, ltf_lookback=30, ltf_max_wait=60)
    eng = Engine(synthetic(3000), cfg)
    eng.lh, eng.ll, eng.lc = (np.asarray(v, dtype=float) for v in (highs, lows, closes))
    eng.ltf_bounds = np.array([0] * 50 + [len(closes)] * (len(eng.x) - 49))  # todo cae en la vela 49
    return eng


def _setup(tp1=125.0):
    ob = OrderBlock(0, 0, +1, 99.0, 100.0)
    return Setup(t=10, system="A", direction=+1, event=None, entry=100.0, sl=98.0, tp1=tp1, rr=5,
                 poi_kind="Order block", ob=ob, fvg=None, leg_origin=0, leg_extreme=110, fib=0.7,
                 base_checks={"mtf_alignment": True}, wyckoff_tests={}, range_=None)


# Retroceso con un máximo menor en la vela 3 (103.0), entrada en el POI en la 8
PULL_H = [104, 102.9, 102.5, 103.0, 102.2, 101.8, 101.5, 101.0, 100.4, 100.8, 101.9, 103.8]
PULL_L = [103, 101.9, 101.7, 102.0, 101.2, 100.8, 100.5, 100.2, 99.8, 99.9, 100.6, 101.8]
PULL_C = [103.2, 102.1, 102.0, 102.3, 101.5, 101.0, 100.8, 100.4, 100.1, 100.6, 101.7, 103.6]


def test_confirmacion_mini_bos_entra_al_cierre_de_la_ruptura():
    eng = _engine_with_ltf(PULL_H, PULL_L, PULL_C)
    s = _setup()
    eng.pending["A"] = s
    eng._confirm_ltf("A", s, 49)
    assert "A" not in eng.pending
    assert len(eng.open) == 1, eng.rejected
    tr = eng.open[0]
    assert s.zone_start == 8
    assert s.confirm_level == 103.0
    assert tr.entry == 103.6 and tr.sl == 98.0


def test_confirmacion_rechaza_si_el_rr_cae_por_debajo_de_3():
    eng = _engine_with_ltf(PULL_H, PULL_L, PULL_C)
    s = _setup(tp1=112.0)  # (112-103.6)/(103.6-98) = 1.5
    eng.pending["A"] = s
    eng._confirm_ltf("A", s, 49)
    assert not eng.open
    assert "RR tras confirmación" in eng.rejected[-1]["motivo"]


def test_confirmacion_se_cancela_si_atraviesa_el_order_block():
    c = PULL_C[:9] + [98.9, 100.0, 103.6]  # cierra bajo el OB (99) antes de romper
    l = PULL_L[:9] + [98.7, 98.9, 101.8]
    eng = _engine_with_ltf(PULL_H, l, c)
    s = _setup()
    eng.pending["A"] = s
    eng._confirm_ltf("A", s, 49)
    assert not eng.open
    assert "POI atravesado" in eng.rejected[-1]["motivo"]


def test_confirmacion_espera_si_no_hay_ruptura():
    h = PULL_H[:11] + [101.5]
    c = PULL_C[:11] + [101.2]
    l = PULL_L[:11] + [100.7]
    eng = _engine_with_ltf(h, l, c)
    s = _setup()
    eng.pending["A"] = s
    eng._confirm_ltf("A", s, 49)
    assert not eng.open and "A" in eng.pending and s.zone_start == 8


def test_entradas_con_confirmacion_son_coherentes():
    cfg = Config(threshold_a=5, threshold_b=6, wyckoff_tests_min_b=2, min_rr=2)
    res = run_backtest(synthetic(40_000, seed=3), cfg)
    x = res.exec_df
    assert res.trades
    for tr in res.trades:
        s = tr.setup
        bar_open = x.index[tr.t_entry]
        assert bar_open <= tr.entry_time < bar_open + pd.Timedelta(cfg.tf_exec)
        assert s.confirm_time == tr.entry_time
        assert (tr.entry - s.confirm_level) * tr.direction > 0


# --- Pruebas 1 (punto y figura) y 8 (fuerza relativa) ------------------------
from xauusd.pf import check_objective, check_relative_strength, pf_columns  # noqa: E402
from xauusd.wyckoff import TradingRange  # noqa: E402


def test_punto_y_figura_columnas():
    # Sube 10 cajas, baja 5, sube 4: X, O, X (reversión de 3)
    prices = [100, 110, 105, 109]
    cols = pf_columns(prices, prices, box=1.0, reversal=3)
    assert [c.direction for c in cols] == [+1, -1, +1]
    assert cols[0].top == 110 and cols[1].bottom == 105 and cols[2].top == 109


def test_punto_y_figura_ignora_retrocesos_menores_que_la_reversion():
    prices = [100, 110, 108.5, 112]
    cols = pf_columns(prices, prices, box=1.0, reversal=3)
    assert len(cols) == 1 and cols[0].top == 112


def _df_const_atr(highs, lows, atr=1.0):
    idx = pd.date_range("2024-01-01", periods=len(highs), freq="15min", tz="UTC")
    return pd.DataFrame({"high": highs, "low": lows, "atr": atr}, index=idx)


@pytest.mark.parametrize("suelo, cumplido", [(70, False), (45, True)])
def test_prueba_1_objetivo_bajista(suelo, cumplido):
    cfg = Config(pf_box_atr=1.0, pf_reversal=3)
    # Distribución (velas 0-19) oscilando entre 100 y 106, caída y acumulación en `suelo`
    dist = [100, 106, 100, 106, 100] * 4
    fall = list(np.linspace(100, suelo, 20))
    acc = [suelo + 2, suelo + 5, suelo + 1, suelo + 4, suelo + 2] * 4
    p = np.array(dist + fall + acc, dtype=float)
    df = _df_const_atr(p, p)
    prev = TradingRange(0, 19, 100, 106, "distribucion", True, end=19, breakout=-1)
    cur = TradingRange(40, 45, suelo + 1, suelo + 5, "acumulacion", True)
    ok, obj = check_objective(cur, +1, 59, [prev, cur], df, cfg)
    ncols = len(pf_columns(p[:20], p[:20], 1.0, 3))
    assert obj == pytest.approx(100 - ncols * 3)  # soporte − columnas × caja × reversión
    assert ok is cumplido


def test_prueba_1_no_evaluable_sin_rango_previo():
    cfg = Config()
    p = np.linspace(100, 90, 60)
    df = _df_const_atr(p, p)
    cur = TradingRange(30, 59, 89, 92, "acumulacion", True)
    assert check_objective(cur, +1, 59, [cur], df, cfg) == (None, None)


def test_prueba_8_fuerza_relativa():
    r = TradingRange(0, 10, 0, 1, "acumulacion", False)
    subiendo = np.linspace(1.0, 1.1, 50)
    assert check_relative_strength(r, +1, 49, subiendo) is True
    assert check_relative_strength(r, -1, 49, subiendo) is False
    assert check_relative_strength(r, +1, 49, None) is None


def test_backtest_con_dxy_evalua_la_prueba_8():
    cfg = Config(threshold_a=5, threshold_b=6, wyckoff_tests_min_b=2, min_rr=2)
    gold = synthetic(40_000, seed=3)
    dxy = synthetic(40_000, seed=11)
    dxy[["open", "high", "low", "close"]] = dxy[["open", "high", "low", "close"]] / 20  # ~100
    res = run_backtest(gold, cfg, dxy)
    vals = [tr.setup.wyckoff_tests.get("8_fuerza_relativa") for tr in res.trades
            if tr.setup.range_ is not None]
    assert vals and all(v is not None for v in vals)
