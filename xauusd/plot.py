"""Gráficos: operaciones al estilo TradingView sobre esquema Wyckoff, y curva de capital."""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .engine import Result, Trade  # noqa: E402
from .wyckoff import snapshot  # noqa: E402

UP, DOWN = "#26a69a", "#ef5350"
PHASE_COLORS = {"A": "#90caf9", "B": "#b0bec5", "C": "#ffcc80", "D": "#a5d6a7", "E": "#ce93d8"}
FIB_LEVELS = (0, 0.618, 0.71, 0.79, 0.88, 1)
KIND_LABEL = {"acumulacion": "acumulación", "distribucion": "distribución"}


def _candles(ax, df, a, b):
    o, h, l, c = (df[k].to_numpy()[a:b] for k in ("open", "high", "low", "close"))
    for i in range(b - a):
        col = UP if c[i] >= o[i] else DOWN
        ax.vlines(a + i, l[i], h[i], color=col, linewidth=0.7)
        ax.add_patch(Rectangle((a + i - 0.35, min(o[i], c[i])), 0.7, max(abs(c[i] - o[i]), 1e-6),
                               color=col, linewidth=0))


def _wyckoff_events(res: Result, r, direction: int, upto: int):
    """Etiquetas clásicas aproximadas dentro del rango y límites de fase,
    usando solo precios hasta la vela `upto` (la de entrada)."""
    x = res.exec_df
    h, l = x["high"].to_numpy(), x["low"].to_numpy()
    end = min(r.end, upto) if r.end is not None else upto
    a, n = r.start, end - r.start
    acc = direction > 0
    ext_arr, opp_arr = (l, h) if acc else (h, l)
    pick = np.argmin if acc else np.argmax
    pick_opp = np.argmax if acc else np.argmin
    ev = {}
    third = max(a + n // 3, a + 1)
    sc = a + int(pick(ext_arr[a:third + 1]))
    ev["SC" if acc else "BC"] = (sc, ext_arr[sc])
    if sc > a:
        ps = a + int(pick(ext_arr[a:sc])) if sc - a > 1 else a
        if sc - ps > 3:
            ev["PS" if acc else "PSY"] = (ps, ext_arr[ps])
    ar = sc + int(pick_opp(opp_arr[sc:end + 1]))
    ev["AR"] = (ar, opp_arr[ar])
    st_i = ar + int(pick(ext_arr[ar:end + 1])) if ar < end else ar
    ev["ST"] = (st_i, ext_arr[st_i])
    phases = [("A", a, st_i)]
    sp = [s for s in r.shakeouts if s.direction == direction and s.valid]
    c_start = st_i
    if sp:
        s = sp[0]
        ev["Spring" if acc else "UTAD"] = (s.t_break, s.extreme)
        phases.append(("B", st_i, s.t_break))
        c_start = s.t_break
        c_end = s.t_test if s.t_test else s.t_reclaim + 2
        if s.t_test:
            ev["Test"] = (s.t_test, ext_arr[s.t_test])
        phases.append(("C", c_start, c_end))
        d_start = c_end
    else:
        phases.append(("B", st_i, end))
        d_start = None
    strengths = [s for s in r.strengths if s.direction == direction]
    if strengths:
        so = strengths[0].t
        ev["SOS" if acc else "SOW"] = (so, opp_arr[so])
        if d_start is None:
            d_start = so
    if d_start is not None:
        phases.append(("D", d_start, end))
        if r.end is not None:
            phases.append(("E", end, upto))
    phases = [(p, p0, min(p1, upto)) for p, p0, p1 in phases if p0 < upto]
    return ev, phases


def plot_trade(res: Result, tr: Trade, path: str | Path, pad: int = 30):
    x = res.exec_df
    s = tr.setup
    # Solo lo que se conocía al entrar: el rango y sus eventos a esa vela
    r = snapshot(s.range_, tr.t_entry)
    a = min(s.leg_origin, r.start if r is not None else s.leg_origin) - pad
    a = max(a, 0, tr.t_entry - 400)
    b = min(len(x), (tr.t_exit or tr.t_entry) + pad)
    fig, ax = plt.subplots(figsize=(15, 7.5))
    _candles(ax, x, a, b)
    atr = float(x["atr"].iloc[tr.t_entry])

    # Esquema Wyckoff: rango, eventos y bandas de fase
    if r is not None:
        re_ = r.end if r.end is not None else tr.t_entry
        ax.add_patch(Rectangle((r.start, r.support), re_ - r.start, r.width,
                               fill=False, ec="#546e7a", lw=1.2, ls="--"))
        ax.hlines([r.support, r.resistance], r.start, re_, colors="#546e7a", lw=0.8)
        ax.text(r.start, r.resistance, f" Rango de {KIND_LABEL[r.kind]}" if r.kind != "neutral" else " Rango (origen neutral)", va="bottom", fontsize=8, color="#37474f")
        ev, phases = _wyckoff_events(res, r, tr.direction, tr.t_entry)
        for name, (i, p) in ev.items():
            if a <= i < b:
                below = (name in ("SC", "PS", "ST", "Spring", "Test")) == (tr.direction > 0)
                ax.annotate(name, (i, p), xytext=(0, -16 if below else 12), textcoords="offset points",
                            ha="center", fontsize=8, fontweight="bold", color="#263238",
                            arrowprops=dict(arrowstyle="-", color="#78909c", lw=0.6))
        ymin = x["low"].iloc[a:b].min() - 1.5 * atr
        for ph, p0, p1 in phases:
            p0, p1 = max(p0, a), min(p1, b - 1)
            if p1 > p0:
                ax.add_patch(Rectangle((p0, ymin), p1 - p0, 0.8 * atr, color=PHASE_COLORS[ph], alpha=0.8, lw=0))
                ax.text((p0 + p1) / 2, ymin + 0.4 * atr, ph, ha="center", va="center", fontsize=9, fontweight="bold")

    # Swings y liquidez externa (equal highs / lows)
    for sw in res.structure.swings:
        if a <= sw.idx < b and sw.confirm <= (tr.t_exit or b):
            mk, off = ("v", 0.25) if sw.kind > 0 else ("^", -0.25)
            ax.plot(sw.idx, sw.price + off * atr, mk, ms=4, color="#455a64", alpha=0.7)
            if sw.equal:
                ax.hlines(sw.price, sw.idx, min(sw.idx + 40, b - 1), colors="#ff9800", lw=1, ls=":")
                ax.text(sw.idx, sw.price, " EQH" if sw.kind > 0 else " EQL", fontsize=7, color="#ef6c00",
                        va="bottom" if sw.kind > 0 else "top")

    # Evento de estructura (BOS / CHoCH)
    evt = s.event
    ax.hlines(evt.level, evt.swing_idx, evt.t, colors="#1565c0", lw=1)
    ax.text(evt.t, evt.level, f" {evt.kind}", color="#1565c0", fontsize=8, va="bottom")

    # Order block y FVG
    ob = s.ob
    ax.add_patch(Rectangle((ob.idx, ob.low), tr.t_entry - ob.idx + 1, ob.high - ob.low,
                           color="#1e88e5", alpha=0.18, lw=0))
    ax.text(ob.idx, ob.high if tr.direction > 0 else ob.low, " OB", fontsize=8, color="#0d47a1",
            va="bottom" if tr.direction > 0 else "top")
    if s.fvg is not None:
        f = s.fvg
        ax.add_patch(Rectangle((f.t_created - 1, f.low), tr.t_entry - f.t_created + 2, f.high - f.low,
                               color="#8e24aa", alpha=0.15, lw=0))
        ax.hlines(f.mid, f.t_created - 1, tr.t_entry, colors="#8e24aa", lw=0.8, ls="--")
        ax.text(f.t_created, f.high, " FVG (50% discontinua)", fontsize=7, color="#6a1b9a", va="bottom")

    # Fibonacci: niveles y zona de oro
    leg0 = x["low"].iloc[s.leg_origin] if tr.direction > 0 else x["high"].iloc[s.leg_origin]
    ext = s.leg_extreme
    lvl = lambda f: ext - f * (ext - leg0)
    ax.add_patch(Rectangle((s.leg_origin, min(lvl(0.618), lvl(0.79))), tr.t_entry - s.leg_origin + 1,
                           abs(lvl(0.79) - lvl(0.618)), color="#fdd835", alpha=0.18, lw=0))
    for f in FIB_LEVELS:
        ax.hlines(lvl(f), s.leg_origin, tr.t_entry, colors="#f9a825", lw=0.5, alpha=0.8)
        ax.text(s.leg_origin, lvl(f), f"{f:g} ", fontsize=6, color="#f57f17", ha="right", va="center")

    # Posición estilo TradingView: caja de beneficio (verde) y riesgo (rojo)
    t1 = tr.t_exit or b - 1
    w = max(t1 - tr.t_entry, 1)
    ax.add_patch(Rectangle((tr.t_entry, min(tr.entry, tr.tp1)), w, abs(tr.tp1 - tr.entry), color=UP, alpha=0.2, lw=0))
    ax.add_patch(Rectangle((tr.t_entry, min(tr.entry, tr.sl)), w, abs(tr.entry - tr.sl), color=DOWN, alpha=0.2, lw=0))
    ax.hlines(tr.entry, tr.t_entry, t1, colors="#424242", lw=1)
    ax.hlines(tr.sl, tr.t_entry, t1, colors=DOWN, lw=1.2)
    ax.hlines(tr.tp1, tr.t_entry, t1, colors=UP, lw=1.2)
    ax.text(t1, tr.sl, f" SL {tr.sl:.2f}", color=DOWN, fontsize=8, va="center")
    ax.text(t1, tr.tp1, f" TP1 {tr.tp1:.2f}", color=UP, fontsize=8, va="center")
    ax.text(t1, tr.entry, f" Entrada {tr.entry:.2f}", color="#424242", fontsize=8, va="center")
    ax.plot(tr.t_entry, tr.entry, "^" if tr.direction > 0 else "v", ms=9, color="#212121")
    if s.confirm_level is not None:
        z0 = tr.t_entry - 3
        ax.hlines(s.confirm_level, z0, tr.t_entry, colors="#00897b", lw=1.2, ls="-.")
        ax.annotate(f"mini BOS {res.cfg.tf_ltf}\n{s.confirm_time:%H:%M}", (tr.t_entry, s.confirm_level),
                    xytext=(-40, 18 if tr.direction > 0 else -26), textcoords="offset points",
                    fontsize=7, color="#00695c", arrowprops=dict(arrowstyle="->", color="#00897b", lw=0.7))
    if tr.t_exit is not None:
        ax.plot(tr.t_exit, tr.exit_price, "X", ms=8, color="#212121")

    # Resumen de las nueve pruebas Wyckoff (✓ cumplida, ✗ no, – no evaluable)
    tests = {k: v for k, v in s.wyckoff_tests.items() if not k.startswith("_")}
    if tests:
        mark = lambda v: "✓" if v else "–" if v is None else "✗"
        lines = [f"{mark(tests[k])} {k.replace('_', ' ')}" for k in sorted(tests, key=lambda k: int(k.split('_')[0]))]
        obj = s.wyckoff_tests.get("_objetivo_pf")
        if obj is not None:
            lines.append(f"Objetivo P&F previo: {obj:.2f}")
        ax.text(0.005, 0.99, "Pruebas Wyckoff\n" + "\n".join(lines), transform=ax.transAxes, fontsize=6.5,
                va="top", family="DejaVu Sans", bbox=dict(boxstyle="round", fc="white", ec="#cfd8dc", alpha=0.9))
        if obj is not None:
            lo, hi = x["low"].iloc[a:b].min(), x["high"].iloc[a:b].max()
            if lo - 3 * atr <= obj <= hi + 3 * atr:
                ax.axhline(obj, color="#6d4c41", lw=0.9, ls=(0, (6, 3)))
                lbl = "objetivo P&F bajista previo " if tr.direction > 0 else "objetivo P&F alcista previo "
                ax.text(b - 1, obj, lbl, color="#6d4c41", fontsize=7, ha="right", va="bottom")

    # Eje temporal con fechas
    ticks = np.linspace(a, b - 1, 8).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([x.index[i].strftime("%d-%m %H:%M") for i in ticks], fontsize=8)
    ax.set_xlim(a - 1, b + 12)
    r_unit = abs(tr.entry - tr.sl) * tr.units
    res_r = tr.pnl / r_unit if r_unit else 0
    ax.set_title(
        f"Sistema {tr.system} · {'Largo' if tr.direction > 0 else 'Corto'} · {s.event.kind} · "
        f"POI: {s.poi_kind}{' + confirmación ' + res.cfg.tf_ltf if s.confirm_level is not None else ''} · Puntaje {tr.points:g}/17 · RR en la entrada 1:{abs(tr.tp1 - tr.entry) / abs(tr.entry - tr.sl):.1f} · "
        f"Resultado {res_r:+.2f}R ({tr.exit_reason})", fontsize=10)
    ax.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_equity(res: Result, path: str | Path):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    eq = res.equity
    ax1.plot(eq.index, eq.values, color="#1565c0", lw=1.2)
    ax1.axhline(res.cfg.initial_capital, color="#9e9e9e", lw=0.8, ls="--")
    ax1.set_ylabel("Capital (USD)")
    ax1.set_title("Curva de capital")
    dd = (eq / eq.cummax() - 1) * 100
    ax2.fill_between(dd.index, dd.values, 0, color=DOWN, alpha=0.4)
    ax2.axhline(-res.cfg.internal_max_dd * 100, color=DOWN, lw=0.8, ls="--")
    ax2.set_ylabel("Drawdown %")
    for a in (ax1, ax2):
        a.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_all(res: Result, out_dir: str | Path, max_trades: int = 50):
    out = Path(out_dir)
    (out / "operaciones").mkdir(parents=True, exist_ok=True)
    plot_equity(res, out / "equity.png")
    for i, tr in enumerate(res.trades[:max_trades], 1):
        ts = res.exec_df.index[tr.t_entry].strftime("%Y%m%d_%H%M")
        plot_trade(res, tr, out / "operaciones" / f"{i:03d}_{ts}_{tr.system}.png")
