"""fig-e1-runs (Fig. 15, Appendix A): CPU over time in the second E1 runs and the excluded third runs.

Raw input (same parsing as fig_e1_cpu.py): tegrastats.log of E1-A-r2, E1-A-r3, E1-B-r2, E1-B-r3
under $PAWAI_EXP_RUNS (default ~/pawai-exp-data/runs), via analysis/_common.py
tegrastats(); segment bounds from data/<run>/meta.json (label, t_start, t_end) and
the raw marks.json ('console' mark = on-board ASR node stopped). Lines are 30-s moving means
of the six-core CPU; dashed lines are steady-state means (first 60 s of each segment excluded),
equal to segments.<seg>.cpu.mean in each run's summary.json (asserted to 0.1 point).
E1-A-r3 is drawn only here: excluded because the driver connection was idle
(segments.*.nodes."go2 driver".cpu_pct_mean about 0.6-1.1 % vs about 20 % in r1-r2); E1-B-r3
stopped after 152 s (meta t_end - t_start).
"""
import importlib.util
import json
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, GRAY, INK, FS, FS_S, save, clean_axes  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402

RUNS = Path(os.environ.get("PAWAI_EXP_RUNS", Path.home() / "pawai-exp-data" / "runs"))
EXP = ROOT / "data"
WARMUP_S, SMOOTH_S = 60, 30
_s = importlib.util.spec_from_file_location("_common", ROOT / "analysis" / "_common.py")
C = importlib.util.module_from_spec(_s)
_s.loader.exec_module(C)


def segments(run):
    meta = json.load(open(EXP / run / "meta.json"))
    mp = RUNS / run / "marks.json"
    marks = json.load(open(mp)) if mp.exists() else []
    b = [(meta["label"], meta["t_start"])] + [(m["label"], m["t"]) for m in marks]
    return [(lab, t0, b[i + 1][1] if i + 1 < len(b) else meta["t_end"]) for i, (lab, t0) in enumerate(b)]


def rows(run):
    p = RUNS / run / "tegrastats.log"
    if not p.exists():
        raise SystemExit(f"[fig_e1_runs] 找不到原始檔 {p}（設 PAWAI_EXP_RUNS 指到 runs 目錄）")
    return [r for r in C.tegrastats(p) if r["cpu"] is not None]


def smooth(xs, ys, w):
    return [st.mean([y for x, y in zip(xs, ys) if xs[i] - w / 2 <= x <= xs[i] + w / 2]) for i in range(len(xs))]


NAMES = {"full": "Full", "console": "Evaluated", "lidar": "LiDAR"}
PANELS = [[("E1-A-r2", INK, "second run"), ("E1-A-r3", GRAY, "excluded third run (idle driver connection)")],
          [("E1-B-r2", INK, "second run"), ("E1-B-r3", GRAY, "third run, stopped at 152 s")]]
fig, axes = plt.subplots(1, 2, figsize=(7.16, 2.45), sharey=True,
                         gridspec_kw={"width_ratios": [2, 1], "wspace": 0.05})
for ax, runs in zip(axes, PANELS):
    for k, (run, col, lab) in enumerate(runs):
        data, segs = rows(run), segments(run)
        org = segs[0][1]
        sel = [r for r in data if segs[0][1] <= r["t"] < segs[-1][2]]
        xs = [(r["t"] - org) / 60 for r in sel]
        ax.plot(xs, smooth(xs, [r["cpu"] for r in sel], SMOOTH_S / 60), color=col, lw=0.9,
                label=f"{run}: {lab}")
        summ = json.load(open(EXP / run / "summary.json"))["segments"]
        for j, (seg, t0, t1) in enumerate(segs):
            steady = [r["cpu"] for r in data if t0 + WARMUP_S <= r["t"] < t1]
            if not steady:
                continue
            m = st.mean(steady)
            if seg in summ:
                assert abs(m - summ[seg]["cpu"]["mean"]) < 0.1, (run, seg, m, summ[seg]["cpu"]["mean"])
            x0, x1 = (t0 - org) / 60, (t1 - org) / 60
            ax.hlines(m, x0 + WARMUP_S / 60, x1, colors=col, linestyles="--", lw=0.7)
            if k == 0:
                ax.text(x1 - 0.1, m + 1.0, f"{m:.1f}%", ha="right", va="bottom", fontsize=FS_S, color=col)
            else:
                ax.text(x0 + WARMUP_S / 60 + 0.1, m - 1.2, f"{m:.1f}%", ha="left", va="top", fontsize=FS_S,
                        color=col)
            if k == 0:
                ax.axvspan(x0, x0 + WARMUP_S / 60, color="#E6E6E6", lw=0, zorder=0)
                ax.text((x0 + x1) / 2, 101.5, f"{NAMES[seg]} configuration", ha="center", va="bottom", fontsize=FS)
                if x0 > 0:
                    ax.axvline(x0, color=INK, lw=0.8)
                    ax.text(x0 + 0.2, 52, "on-board ASR\nnode stopped", fontsize=FS_S, ha="left", va="bottom")
    ax.set_ylim(50, 100)
    ax.xaxis.set_major_locator(MultipleLocator(2))
    ax.set_xlabel("Time from run start (min)", fontsize=FS)
    clean_axes(ax)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.6), fontsize=FS_S, frameon=False)
axes[0].set_xlim(0, 22.5)
axes[1].set_xlim(0, 11.5)
axes[0].set_ylabel("CPU utilization (%)", fontsize=FS)
axes[1].tick_params(left=False)
axes[1].spines["left"].set_visible(False)
fig.subplots_adjust(left=0.07, right=0.995, top=0.86, bottom=0.36)
save(fig, "fig-e1-runs")
