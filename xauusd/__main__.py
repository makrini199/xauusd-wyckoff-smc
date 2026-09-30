"""Línea de comandos.

    python -m xauusd backtest --data data/xauusd_m1.csv --out resultados/
    python -m xauusd backtest --sintetico --out resultados/
"""
import argparse
import json

from .config import Config
from .data import load_csv, synthetic
from .engine import run_backtest
from .plot import plot_all
from .report import write


def main(argv=None):
    p = argparse.ArgumentParser(prog="xauusd", description="Wyckoff + Smart Money sobre XAUUSD")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest", help="ejecuta el backtest y genera informes y gráficos")
    src = b.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="CSV de dukascopy-node (timestamp, open, high, low, close, volume)")
    src.add_argument("--sintetico", action="store_true", help="usa datos sintéticos (solo para probar)")
    b.add_argument("--desde", help="fecha inicial, p. ej. 2024-01-01")
    b.add_argument("--hasta", help="fecha final")
    b.add_argument("--out", default="resultados")
    b.add_argument("--tf", default=None, help="temporalidad de ejecución (por defecto 15min)")
    b.add_argument("--graficos", type=int, default=50, help="máximo de operaciones a dibujar")
    a = p.parse_args(argv)

    cfg = Config()
    if a.tf:
        cfg.tf_exec = a.tf
    df = synthetic(60_000) if a.sintetico else load_csv(a.data)
    if a.desde:
        df = df[df.index >= a.desde]
    if a.hasta:
        df = df[df.index < a.hasta]
    res = run_backtest(df, cfg)
    s = write(res, a.out)
    if a.graficos:
        plot_all(res, a.out, a.graficos)
    print(json.dumps(s, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
