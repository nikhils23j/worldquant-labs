"""Make the README figures from the saved results in results/.

    python scripts/make_figures.py          # needs matplotlib (pip install matplotlib)

Reads only files in results/ (plus RESULTS.md, which scripts/summarize.py
generates from them) and writes PNGs to docs/img/. It never reruns an
experiment, and no plotted number is typed in by hand.
"""
import csv
import json
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
OUT = ROOT / "docs" / "img"

INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
# Categorical slots in fixed order (validated palette); never cycled.
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
NEUTRAL = "#8a8984"

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "font.size": 11, "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "legend.frameon": False, "lines.linewidth": 2,
})
DOT = dict(marker="o", ms=6, markeredgecolor="white", markeredgewidth=1.2)


def load(name):
    return json.loads((RES / name).read_text())


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, dpi=170, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


def series(summary, key):
    xs = [r["latency_us"] for r in summary]
    m = [r[key]["mean"] for r in summary]
    hw = [r[key]["half_width"] for r in summary]
    return xs, m, hw


def symlog_latency_axis(ax, ticks):
    ax.set_xscale("symlog", linthresh=10, linscale=0.6)
    ax.set_xticks(ticks, [f"{t:g}" for t in ticks])
    ax.minorticks_off()
    ax.set_xlim(-1.5, ticks[-1] * 1.25)
    ax.set_xlabel("probe one-way latency (µs, log scale past 10)")


# ---------------------------------------------------------------- ExchangeLab
def fig_latency_cliff():
    ls = load("exchange-latency-study.json")
    cfg = ls["base_config"]
    # The control run's competitor latency is not stored in the JSON; it is in
    # the RESULTS.md heading that summarize.py writes for that table.
    m = re.search(r"Control: same taker race when the competitor is slow \((\d+) us\)",
                  (ROOT / "RESULTS.md").read_text())
    if not m:
        raise SystemExit("could not find the control-run competitor latency in RESULTS.md")
    slow_comp = int(m.group(1))
    key = "pnl_ticks_1000us"

    fig, ax = plt.subplots(figsize=(10, 5))
    xs, mean, hw = series(ls["taker"]["summary"], key)
    lo = [a - b for a, b in zip(mean, hw)]
    hi = [a + b for a, b in zip(mean, hw)]
    ax.fill_between(xs, lo, hi, color=C[0], alpha=0.18, lw=0)
    ax.plot(xs, mean, color=C[0], **DOT,
            label=f"competitor at {cfg['competitor_latency_us']} µs "
                  f"({len(ls['taker']['seeds'])} seeds, band = 95% CI)")
    cx, cm, chw = series(ls["competitor_latency_check"]["summary"], key)
    ax.errorbar(cx, cm, yerr=chw, color=C[1], capsize=4, elinewidth=1.5, **DOT,
                label=f"competitor at {slow_comp} µs "
                      f"({len(ls['competitor_latency_check']['seeds'])} seeds, bars = 95% CI)")
    for x, txt in [(cfg["competitor_latency_us"], "competitor arb\nlatency"),
                   (cfg["mm_latency_us"], "market maker\nlatency")]:
        ax.axvline(x, color=NEUTRAL, ls="--", lw=1)
        ax.text(x * 1.06, max(hi) * 0.42, f"{txt}\n{x} µs", fontsize=9.5, color=INK2, va="top")
    symlog_latency_axis(ax, [0, 10, 20, 50, 100, 200, 500, 1000, 2000])
    ax.set_ylim(0, max(max(hi), max(c + h for c, h in zip(cm, chw))) * 1.15)
    ax.set_ylabel("taker probe PnL per run (ticks, marked 1 ms after fill)")
    ax.set_title("The latency cliff: PnL collapses once you're slower than the rival")
    ax.legend(loc="lower left")
    save(fig, "exchange_latency_cliff.png")


def fig_passive():
    ls = load("exchange-latency-study.json")
    summ = ls["passive"]["summary"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.4))
    for ax, key, colour, ylabel, title in [
        (a1, "picked_off_by_arbs_qty", C[1], "lots picked off by arbs per run", "Slow makers get picked off..."),
        (a2, "markout_ticks_per_lot_1000us", C[0], "markout per lot @1 ms (ticks)", "...so each fill is worth less"),
    ]:
        xs, mean, hw = series(summ, key)
        ax.fill_between(xs, [a - b for a, b in zip(mean, hw)], [a + b for a, b in zip(mean, hw)],
                        color=colour, alpha=0.18, lw=0)
        ax.plot(xs, mean, color=colour, **DOT)
        ax.axvline(ls["base_config"]["competitor_latency_us"], color=NEUTRAL, ls="--", lw=1)
        symlog_latency_axis(ax, [0, 10, 25, 50, 100, 200, 500, 2000])
        ax.set_ylim(0, max(a + b for a, b in zip(mean, hw)) * 1.15)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
    a1.text(ls["base_config"]["competitor_latency_us"] * 1.06, a1.get_ylim()[1] * 0.97,
            f"competitor\n{ls['base_config']['competitor_latency_us']} µs", fontsize=9.5, color=INK2, va="top")
    fig.suptitle(f"Passive probe: 1-lot quotes at the touch ({len(ls['passive']['seeds'])} seeds, "
                 "bands = 95% CI)", x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    save(fig, "exchange_passive_pickoff.png")


def fig_engine_speed():
    b = load("exchange-benchmark.json")
    names = {"v0": "original (book_v0.py)", "fast": "optimised (book.py)"}
    fig, ax = plt.subplots(figsize=(10, 3.4))
    for i, (eng, colour) in enumerate([("v0", NEUTRAL), ("fast", C[0])]):
        med = b["median_seconds"][eng]
        ax.barh(i, med, height=0.45, color=colour, alpha=0.35)
        runs = [r["seconds"] for r in b["runs"][eng]]
        ax.plot(runs, [i] * len(runs), ls="none", marker="o", ms=8, color=colour,
                markeredgecolor="white", markeredgewidth=1.2)
        ax.text(max(runs) + 0.05, i, f"median {med:.3f} s  ({b['count'] / med:,.0f} commands/s)",
                va="center", fontsize=10, color=INK)
    ax.set_yticks([0, 1], [names["v0"], names["fast"]])
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, b["median_seconds"]["v0"] * 1.6)
    ax.set_xlabel(f"seconds to process {b['count']:,} commands (dots = {b['repetitions']} interleaved runs)")
    ax.set_title(f"Engine speed: {b['speedup_median']:.2f}x median speedup, identical final book state")
    save(fig, "exchange_engine_speed.png")


# ------------------------------------------------------------------- RiskLab
QMC_LABELS = {
    "prng_plain": "PRNG plain", "prng_antithetic": "PRNG antithetic", "prng_control": "PRNG + control",
    "prng_antithetic_control": "PRNG antithetic + control", "sobol_standard": "Sobol, step-by-step",
    "sobol_bridge": "Sobol + Brownian bridge", "sobol_bridge_control": "Sobol + bridge + control",
}


def spread_labels(ys, min_gap):
    """Nudge label positions (in log10 space) apart so they don't overlap."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    pos = [math.log10(ys[i]) for i in order]
    for k in range(1, len(pos)):
        pos[k] = max(pos[k], pos[k - 1] + min_gap)
    out = [0.0] * len(ys)
    for k, i in enumerate(order):
        out[i] = 10 ** pos[k]
    return out


def fig_qmc():
    with open(RES / "risk-qmc-convergence.csv") as f:
        rows = [r for r in csv.DictReader(f) if r["target"] == "arithmetic_asian"]
    methods = list(dict.fromkeys(r["method"] for r in rows))
    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    ends = []
    for colour, meth in zip(C, methods):
        pts = sorted((int(r["paths"]), float(r["rmse"])) for r in rows if r["method"] == meth)
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=colour, **DOT, label=QMC_LABELS.get(meth, meth))
        ends.append((xs[-1], ys[-1], colour, meth))
    # 1/sqrt(N) reference, anchored on plain MC's first point.
    plain = sorted((int(r["paths"]), float(r["rmse"])) for r in rows if r["method"] == "prng_plain")
    n0, e0 = plain[0]
    nn = [p for p, _ in plain]
    ax.plot(nn, [e0 * math.sqrt(n0 / n) for n in nn], color=NEUTRAL, ls=":", lw=1.5,
            label="1/√N reference")
    labels_y = spread_labels([e[1] for e in ends], 0.09)
    for (x, y, colour, meth), ly in zip(ends, labels_y):
        ax.annotate(QMC_LABELS.get(meth, meth), (x, y), xytext=(x * 1.25, ly), fontsize=9.5,
                    color=INK, va="center")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks(nn, [f"{n:,}" for n in nn])
    ax.minorticks_off()
    ax.set_xlim(nn[0] / 1.3, nn[-1] * 9)
    ax.set_xlabel("paths N (log scale)")
    ax.set_ylabel("RMSE vs truth (log scale)")
    reps = load("risk-study.json")["qmc_convergence"]["reps"]
    ax.set_title(f"Arithmetic Asian: RMSE vs paths by method ({reps} replications per point)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), fontsize=9.5, ncol=4)
    save(fig, "risk_qmc_convergence.png")


def fig_barrier():
    cases = load("risk-study.json")["barrier"]
    fig, axes = plt.subplots(1, len(cases), figsize=(13, 4.4), sharey=False)
    for ax, case in zip(axes, cases):
        for corr, colour, lab in [(False, C[1], "naive (check barrier on grid dates)"),
                                  (True, C[0], "Brownian-bridge corrected")]:
            runs = sorted((r for r in case["runs"] if r["correction"] == corr), key=lambda r: r["steps"])
            xs = [r["steps"] for r in runs]
            ax.errorbar(xs, [r["error_vs_quantlib"] for r in runs], yerr=[r["half_width"] for r in runs],
                        color=colour, capsize=4, elinewidth=1.5, **DOT, label=lab)
        ax.axhline(0, color=INK2, lw=1)
        ax.set_xscale("log")
        ax.set_xticks(xs, [str(x) for x in xs])
        ax.minorticks_off()
        ax.set_xlabel("monitoring dates (log scale)")
        ax.set_title(f"{case['name'].replace('_', ' ')}\nQuantLib continuous price {case['quantlib_continuous']:.4f}",
                     fontsize=11)
    axes[0].set_ylabel("price error vs QuantLib (± 95% CI)")
    axes[0].legend(loc="upper right", fontsize=9.5)
    paths = cases[0]["runs"][0]["paths"]
    fig.suptitle(f"Barrier options: naive discrete monitoring vs bridge correction ({paths:,} paths per point)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    save(fig, "risk_barrier_error.png")


def fig_heston():
    rows = load("risk-study.json")["heston"]
    cases = list(dict.fromkeys(r["case"] for r in rows))
    strikes = sorted({r["spec"]["strike"] for r in rows})
    fig, axes = plt.subplots(1, len(cases), figsize=(12, 4.4), sharey=True)
    for ax, case in zip(axes, cases):
        sub = [r for r in rows if r["case"] == case]
        for colour, k in zip(C, strikes):
            pts = sorted((r for r in sub if r["spec"]["strike"] == k), key=lambda r: r["steps"])
            xs = [r["steps"] for r in pts]
            ax.errorbar(xs, [r["bias"] for r in pts], yerr=[1.96 * r["standard_error"] for r in pts],
                        color=colour, capsize=4, elinewidth=1.5, **DOT, label=f"strike {k:g}")
        ax.axhline(0, color=INK2, lw=1)
        ax.set_xscale("log")
        ax.set_xticks(xs, [str(x) for x in xs])
        ax.minorticks_off()
        ax.set_xlabel("time steps (log scale)")
        p = sub[0]["params"]
        neg = {r["steps"]: r["negative_variance_fraction"] for r in sub}
        feller = "holds" if sub[0]["feller"] else "violated"
        ax.set_title(f"Feller {feller}: κ={p['kappa']:g}, θ={p['theta']:g}, ξ={p['xi']:g}", fontsize=11)
        ax.text(0.98, 0.95, "steps with v<0: " + ", ".join(f"{neg[s]:.1%} @{s}" for s in sorted(neg)),
                transform=ax.transAxes, ha="right", va="top", fontsize=9.5, color=INK2)
    axes[0].set_ylabel("MC price − QuantLib analytic (± 95% CI)")
    axes[0].legend(loc="center right", fontsize=9.5)
    fig.suptitle(f"Heston, full-truncation Euler: bias vs steps ({rows[0]['paths']:,} paths per point)",
                 x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    save(fig, "risk_heston_bias.png")


if __name__ == "__main__":
    fig_latency_cliff()
    fig_passive()
    fig_engine_speed()
    fig_qmc()
    fig_barrier()
    fig_heston()
