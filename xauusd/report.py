"""Métricas del backtest y exportación a CSV."""
from pathlib import Path

import numpy as np
import pandas as pd

from .engine import Result
from .scoring import LABELS


def trades_frame(res: Result) -> pd.DataFrame:
    idx = res.exec_df.index
    rows = []
    for tr in res.trades:
        r_unit = abs(tr.entry - tr.sl) * tr.units
        s = tr.setup
        rows.append({
            "entrada": tr.entry_time if tr.entry_time is not None else idx[tr.t_entry],
            "salida": idx[tr.t_exit],
            "sistema": tr.system,
            "direccion": "largo" if tr.direction > 0 else "corto",
            "precio_entrada": round(tr.entry, 2),
            "stop_loss": round(tr.sl, 2),
            "take_profit_1": round(tr.tp1, 2),
            "precio_salida": round(tr.exit_price, 2),
            "rr_en_entrada": round(abs(tr.tp1 - tr.entry) / abs(tr.entry - tr.sl), 2),
            "resultado_R": round(tr.pnl / r_unit, 2) if r_unit else 0.0,
            "pnl_usd": round(tr.pnl, 2),
            "riesgo_%": tr.risk_frac * 100,
            "puntaje": tr.points,
            "poi": s.poi_kind,
            "fib": round(s.fib, 3),
            "fase_wyckoff": s.base_checks.get("_phase", "?"),
            "evento": s.event.kind,
            "nivel_mini_bos": round(s.confirm_level, 2) if s.confirm_level is not None else None,
            "motivo_salida": tr.exit_reason,
            **{f"ok_{k}": bool(tr.checks.get(k)) for k in LABELS},
            "pruebas_wyckoff_ok": sum(1 for k, v in s.wyckoff_tests.items() if not k.startswith("_") and v),
            "objetivo_pf": (round(s.wyckoff_tests["_objetivo_pf"], 2)
                            if s.wyckoff_tests.get("_objetivo_pf") is not None else None),
            **{f"prueba_{k}": s.wyckoff_tests[k] for k in sorted(s.wyckoff_tests) if not k.startswith("_")},
        })
    return pd.DataFrame(rows)


def summary(res: Result) -> dict:
    df = trades_frame(res)
    eq = res.equity
    dd = (eq / eq.cummax() - 1).min() if len(eq) else 0.0
    weeks = max((res.exec_df.index[-1] - res.exec_df.index[0]).days / 7, 1e-9)
    out = {
        "operaciones": len(df),
        "operaciones_por_semana": round(len(df) / weeks, 2),
        "setups_rechazados": len(res.rejected),
        "capital_inicial": res.cfg.initial_capital,
        "capital_final": round(float(eq.iloc[-1]), 2) if len(eq) else res.cfg.initial_capital,
        "drawdown_maximo_%": round(float(dd) * 100, 2),
        "sistema_detenido_por_drawdown": res.halted,
    }
    if len(df):
        wins = df[df.pnl_usd > 0]
        losses = df[df.pnl_usd <= 0]
        gp, gl = wins.pnl_usd.sum(), -losses.pnl_usd.sum()
        out.update({
            "tasa_acierto_%": round(len(wins) / len(df) * 100, 1),
            "R_medio": round(float(df.resultado_R.mean()), 2),
            "R_total": round(float(df.resultado_R.sum()), 2),
            "profit_factor": round(float(gp / gl), 2) if gl > 0 else float("inf"),
            "por_sistema": df.groupby("sistema").resultado_R.agg(["count", "mean", "sum"]).round(2).to_dict("index"),
        })
    return out


def write(res: Result, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    trades_frame(res).to_csv(out / "operaciones.csv", index=False)
    pd.DataFrame(res.rejected).to_csv(out / "setups_rechazados.csv", index=False)
    res.equity.rename("equity").to_csv(out / "equity.csv")
    s = summary(res)
    with open(out / "resumen.txt", "w") as f:
        for k, v in s.items():
            f.write(f"{k}: {v}\n")
    return s
