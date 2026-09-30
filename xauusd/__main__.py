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
    b.add_argument("--dxy", help="CSV del índice dólar (mismo formato) para la prueba 8 de Wyckoff")
    b.add_argument("--desde", help="fecha inicial, p. ej. 2024-01-01")
    b.add_argument("--hasta", help="fecha final")
    b.add_argument("--out", default="resultados")
    b.add_argument("--tf", default=None, help="temporalidad de ejecución (por defecto 15min)")
    b.add_argument("--ltf", default=None, help="temporalidad de confirmación: 1min (defecto) o 5min")
    b.add_argument("--sin-confirmacion", action="store_true",
                   help="entrar con orden límite en el POI, sin esperar el mini BOS")
    b.add_argument("--graficos", type=int, default=50, help="máximo de operaciones a dibujar")
    al = sub.add_parser("alertas", help="vigila XAUUSD y envía alertas por Telegram")
    al.add_argument("--fuente", choices=["oanda", "csv"], default="oanda")
    al.add_argument("--csv", help="(fuente csv) velas de 1 min para reproducir")
    al.add_argument("--dxy-csv", help="(fuente csv) índice dólar para la prueba 8")
    al.add_argument("--desde", help="(fuente csv) fecha desde la que reproducir")
    al.add_argument("--hasta", help="(fuente csv) fecha hasta la que reproducir")
    al.add_argument("--paso", default="1min", help="(fuente csv) avance del reloj en cada paso")
    al.add_argument("--sin-dxy", action="store_true", help="(oanda) no calcular el DXY; prueba 8 no evaluable")
    al.add_argument("--simular", action="store_true", help="imprimir las alertas en pantalla en vez de Telegram")
    al.add_argument("--capital", type=float, help="capital para calcular el tamaño de posición")
    al.add_argument("--dias", type=int, default=20, help="días de historia que recalcula el motor")
    al.add_argument("--estado", default="alertas_estado.json", help="archivo con las alertas ya enviadas")
    al.add_argument("--una-vez", action="store_true", help="una sola comprobación (para cron)")
    sub.add_parser("telegram-prueba", help="envía un mensaje de prueba a Telegram")
    a = p.parse_args(argv)
    if a.cmd == "telegram-prueba":
        from .telegram import Telegram
        Telegram().send_message("✅ Alertas XAUUSD Wyckoff + Smart Money: conexión correcta.")
        print("Mensaje enviado.")
        return
    if a.cmd == "alertas":
        return _alertas(a)

    cfg = Config()
    if a.tf:
        cfg.tf_exec = a.tf
    if a.ltf:
        cfg.tf_ltf = a.ltf
    if a.sin_confirmacion:
        cfg.ltf_confirm = False
    df = synthetic(60_000) if a.sintetico else load_csv(a.data)
    if a.desde:
        df = df[df.index >= a.desde]
    if a.hasta:
        df = df[df.index < a.hasta]
    dxy = load_csv(a.dxy) if a.dxy else None
    res = run_backtest(df, cfg, dxy)
    s = write(res, a.out)
    if a.graficos:
        plot_all(res, a.out, a.graficos)
    print(json.dumps(s, indent=2, ensure_ascii=False, default=str))


def _alertas(a):
    import pandas as pd

    from .alertas import Watcher
    from .fuentes import CsvReplaySource, OandaDxySource, OandaSource
    from .telegram import ConsoleSink, Telegram

    cfg = Config()
    if a.capital:
        cfg.initial_capital = a.capital
    sink = ConsoleSink() if a.simular else Telegram()
    if a.fuente == "oanda":
        src = OandaSource()
        dxy = None if a.sin_dxy else OandaDxySource()
        w = Watcher(src, sink, cfg, a.estado, a.dias, dxy_source=dxy)
        if a.una_vez:
            w.step()
        else:
            w.loop()
        return
    # Reproducción de un CSV: el reloj avanza `paso` en cada comprobación
    if not (a.csv and a.desde):
        raise SystemExit("--fuente csv necesita --csv y --desde")
    src = CsvReplaySource(a.csv, a.desde)
    dxy = CsvReplaySource(a.dxy_csv, a.desde) if a.dxy_csv else None
    w = Watcher(src, sink, cfg, a.estado, a.dias, dxy_source=dxy)
    end = pd.Timestamp(a.hasta, tz="UTC") if a.hasta else src.all.index[-1]
    while src.now <= end:
        w.step()
        src.now += pd.Timedelta(a.paso)
        if dxy:
            dxy.now = src.now


def run():
    from .telegram import TelegramError
    try:
        main()
    except (TelegramError, RuntimeError) as e:
        raise SystemExit(f"Error: {e}. Revisa las variables de entorno (ver .env.ejemplo).")


if __name__ == "__main__":
    run()
