"""fig-e1-cpu (Fig. 7): CPU utilization over time in E1, full -> evaluated -> LiDAR configuration.

Raw input: tegrastats.log of E1-A-r1 (A-full, then the on-board ASR node is
killed at the 'console' mark) and E1-B-r1 (LiDAR configuration), under
$PAWAI_EXP_RUNS (default ~/pawai-exp-data/runs). Segment bounds come from the
repo copies data/<run>/meta.json and the raw marks.json. Parsing
reuses analysis/_common.py tegrastats() so the timestamps and the
six-core mean match summary.json. The dashed lines are the steady-state means
(first 60 s excluded), the same numbers as segments.<seg>.cpu.mean.
"""
import importlib.util
import json
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, ACCENT, GRAY, INK, FS, FS_S, save  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402

RUNS = Path(os.environ.get("PAWAI_EXP_RUNS", Path.home() / "pawai-exp-data" / "runs"))
EXP = ROOT / "data"
WARMUP_S = 60
SMOOTH_S = 30

_s = importlib.util.spec_from_file_location("_common", ROOT / "analysis" / "_common.py")
C = importlib.util.module_from_spec(_s)
_s.loader.exec_module(C)


def segments(run):
    meta = json.load(open(EXP / run / "meta.json"))
    marks_p = RUNS / run / "marks.json"
    marks = json.load(open(marks_p)) if marks_p.exists() else []
    bounds = [(meta["label"], meta["t_start"])] + [(m["label"], m["t"]) for m in marks]
    return [(lab, t0, bounds[i + 1][1] if i + 1 < len(bounds) else meta["t_end"])
            for i, (lab, t0) in enumerate(bounds)]


def rows(run):
    p = RUNS / run / "tegrastats.log"
    if not p.exists():
        raise SystemExit(f"[fig_e1_cpu] 找不到原始檔 {p}（設 PAWAI_EXP_RUNS 指到 runs 目錄）")
    return [r for r in C.tegrastats(p) if r["cpu"] is not None]


def smooth(xs, ys, w):
    out = []
    for i in range(len(xs)):
        win = [y for x, y in zip(xs, ys) if xs[i] - w / 2 <= x <= xs[i] + w / 2]
        out.append(st.mean(win))
    return out


PANELS = [("E1-A-r1", {"full": "Full configuration", "console": "Evaluated configuration"}),
          ("E1-B-r1", {"lidar": "LiDAR configuration"})]

fig, axes = plt.subplots(1, 2, figsize=(7.16, 2.35), sharey=True,
                         gridspec_kw={"width_ratios": [2, 1], "wspace": 0.05})
for ax, (run, names) in zip(axes, PANELS):
    data = rows(run)
    segs = segments(run)
    t_origin = segs[0][1]
    xs = [(r["t"] - t_origin) / 60 for r in data if segs[0][1] <= r["t"] < segs[-1][2]]
    ys = [r["cpu"] for r in data if segs[0][1] <= r["t"] < segs[-1][2]]
    ax.plot(xs, ys, color=GRAY, lw=0.4, alpha=0.45, label="1-s samples")
    ax.plot(xs, smooth(xs, ys, SMOOTH_S / 60), color=ACCENT, lw=1.1, label=f"{SMOOTH_S}-s moving mean")
    for lab, t0, t1 in segs:
        x0, x1 = (t0 - t_origin) / 60, (t1 - t_origin) / 60
        ax.axvspan(x0, x0 + WARMUP_S / 60, color="#E6E6E6", lw=0, zorder=0)
        steady = [r["cpu"] for r in data if t0 + WARMUP_S <= r["t"] < t1]
        m = st.mean(steady)
        ax.hlines(m, x0 + WARMUP_S / 60, x1, colors=INK, linestyles="--", lw=0.8)
        ax.text((x0 + x1) / 2, 101.5, f"{names[lab]}\nmean {m:.1f}%", ha="center", va="bottom", fontsize=FS)
        if x0 > 0:
            ax.axvline(x0, color=INK, lw=0.8)
            ax.text(x0 + 0.2, 52, "on-board ASR\nnode stopped", fontsize=FS_S, ha="left", va="bottom")
    ax.set_xlim(0, (segs[-1][2] - t_origin) / 60)
    ax.set_ylim(50, 100)
    ax.xaxis.set_major_locator(MultipleLocator(2))
    ax.set_xlabel("Time (min)", fontsize=FS)
    ax.tick_params(labelsize=FS_S, width=0.6, length=2.5)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_linewidth(0.6)
axes[0].set_ylabel("CPU utilization (%)", fontsize=FS)
axes[1].tick_params(left=False)
axes[1].spines["left"].set_visible(False)
h, lab = axes[0].get_legend_handles_labels()
fig.legend(h, lab, loc="lower center", ncol=2, fontsize=FS_S, frameon=False, bbox_to_anchor=(0.5, -0.01))
fig.subplots_adjust(left=0.07, right=0.995, top=0.80, bottom=0.27)
save(fig, "fig-e1-cpu")
