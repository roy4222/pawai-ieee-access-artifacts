"""fig-e1-resources (Fig. 8): steady-state resources of the three E1 configurations.

Input (two runs per configuration, r1-r2 only; E1-A-r3 excluded):
  Full       data/E1-A-r1|r2/summary.json  segments.full
  Evaluated  data/E1-A-r1|r2/summary.json  segments.console
  LiDAR      data/E1-B-r1|r2/summary.json  segments.lidar
Keys per segment: cpu.mean, cpu.p95, core_saturation_frac, gpu.mean, gpu.p95, ram_mb.mean, power_mw_mean.
Bars = mean of the two runs; error bars = between-run SD (statistics.stdev of the two run
values); open markers = mean of the two runs' 95th percentiles.
Paired deltas (addendum-1 (e)):
  on-board ASR node cost = Full - Evaluated, same-run pairs of THREE runs (E1-A-r1, r2, r3; r3 is
    excluded from the absolute values only, the same-run difference stays valid), summary key
    asr_cost_full_minus_console.{cpu, ram_mb} (CPU +0.1 / +1.4 / +4.2 pp, mean +1.9; RAM +323 +- 1 MB;
   ). Drawn in (a) and (d).
  LiDAR - Full: B-r1 - A-r1 and B-r2 - A-r2 (two pairs).
RAM reference line: 7,620 MB = total reported by tegrastats ("RAM used/7620MB");
the axis is drawn past the nominal 8,192 MB (tick) to leave room for the label.
"""
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, ACCENT, GRAY, INK, FS, FS_S, save, clean_axes  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

EXP = ROOT / "data"
CFG = [("Full", [("E1-A-r1", "full"), ("E1-A-r2", "full")]),
       ("Evaluated", [("E1-A-r1", "console"), ("E1-A-r2", "console")]),
       ("LiDAR", [("E1-B-r1", "lidar"), ("E1-B-r2", "lidar")])]
COLORS = [INK, GRAY, ACCENT]
RAM_TOTAL_MB = 7620
_cache = {}


def seg(run, s):
    if run not in _cache:
        _cache[run] = json.load(open(EXP / run / "summary.json"))
    return _cache[run]["segments"][s]


def stat(f):
    out = []
    for _, runs in CFG:
        v = [f(seg(r, s)) for r, s in runs]
        out.append((st.mean(v), st.stdev(v)))
    return out


cpu = stat(lambda x: x["cpu"]["mean"])
cpu95 = stat(lambda x: x["cpu"]["p95"])
sat = stat(lambda x: x["core_saturation_frac"] * 100)
gpu = stat(lambda x: x["gpu"]["mean"])
gpu95 = stat(lambda x: x["gpu"]["p95"])
ram = stat(lambda x: x["ram_mb"]["mean"])
pw = stat(lambda x: x["power_mw_mean"] / 1000)
_asr = [json.load(open(EXP / r / "summary.json"))["asr_cost_full_minus_console"] for r in ("E1-A-r1", "E1-A-r2", "E1-A-r3")]
asr_cpu = [x["cpu"] for x in _asr]
asr_ram = (st.mean(x["ram_mb"] for x in _asr), st.stdev(x["ram_mb"] for x in _asr))
d_lid = st.mean(seg(f"E1-B-r{i}", "lidar")["cpu"]["mean"] - seg(f"E1-A-r{i}", "full")["cpu"]["mean"] for i in (1, 2))
for name, v in [("cpu", cpu), ("cpu_p95", cpu95), ("sat%", sat), ("gpu", gpu), ("ram", ram), ("W", pw)]:
    print("  ", name, [f"{m:.1f}±{s:.1f}" for m, s in v])
print(f"   ASR cost cpu {asr_cpu} mean {st.mean(asr_cpu):+.2f}; ram {asr_ram[0]:.1f}±{asr_ram[1]:.1f}; lidar-full {d_lid:+.2f}")

fig = plt.figure(figsize=(7.16, 2.55))
gs = fig.add_gridspec(2, 4, wspace=0.42, hspace=0.55, height_ratios=[1.35, 1], left=0.06, right=0.995, top=0.86, bottom=0.2)
ax_c = fig.add_subplot(gs[:, 0])
ax_s = fig.add_subplot(gs[:, 1])
ax_g = fig.add_subplot(gs[:, 2])
ax_r = fig.add_subplot(gs[0, 3])
ax_p = fig.add_subplot(gs[1, 3])
X = range(len(CFG))
NAMES = [c for c, _ in CFG]
EB = dict(ecolor=INK, elinewidth=0.7, capsize=2, capthick=0.7)


def bars(ax, vals, p95=None, fmt="{:.1f}", dy=None):
    for i, ((m, s), c) in enumerate(zip(vals, COLORS)):
        ax.bar(i, m, width=0.62, color=c, zorder=2)
        ax.errorbar(i, m, yerr=s, fmt="none", zorder=3, **EB)
        top = m + s
        if p95:
            ax.scatter([i], [p95[i][0]], s=14, facecolor="white", edgecolor=INK, lw=0.8, zorder=4)
            top = max(top, p95[i][0])
        ax.text(i, ax.get_ylim()[1] * 0.03, fmt.format(m), ha="center", va="bottom", fontsize=FS_S,
                color="white", zorder=5)
    ax.set_xticks(list(X))
    ax.set_xticklabels(NAMES, fontsize=FS_S, rotation=0)
    clean_axes(ax, grid_axis="y")
    ax.tick_params(axis="x", length=0)


ax_c.set_ylim(0, 160)
bars(ax_c, cpu, cpu95)
ax_c.set_ylabel("CPU, six-core mean (%)", fontsize=FS)
ax_c.set_title("(a) CPU", fontsize=FS, loc="left")
ax_c.set_yticks([0, 20, 40, 60, 80, 100])
# paired deltas
yb = 103
ax_c.plot([0, 0, 1, 1], [yb - 2, yb, yb, yb - 2], color=INK, lw=0.5)
ax_c.text(0.5, yb + 0.8, f"ASR node {st.mean(asr_cpu):+.1f}\n(n = 3 paired;\n{min(asr_cpu):+.1f} to {max(asr_cpu):+.1f})",
          ha="center", va="bottom", fontsize=FS_S, linespacing=1.05)
ax_c.plot([0, 0, 2, 2], [yb + 37, yb + 39, yb + 39, yb + 37], color=ACCENT, lw=0.5)
ax_c.text(1.0, yb + 39.8, f"LiDAR {d_lid:+.1f} (n = 2 paired)", ha="center", va="bottom", fontsize=FS_S, color=ACCENT)

ax_s.set_ylim(0, 100)
bars(ax_s, sat, fmt="{:.0f}%")
ax_s.set_ylabel("Seconds with ≥ 1 core ≥ 90% (%)", fontsize=FS)
ax_s.set_title("(b) Core saturation", fontsize=FS, loc="left")

ax_g.set_ylim(0, 100)
bars(ax_g, gpu, gpu95)
ax_g.set_ylabel("GPU utilization (%)", fontsize=FS)
ax_g.set_title("(c) GPU", fontsize=FS, loc="left")

ax_r.set_ylim(0, 9100)
bars(ax_r, ram, fmt="{:,.0f}", dy=250)
ax_r.axhline(RAM_TOTAL_MB, color=INK, ls=(0, (3, 2)), lw=0.6)
ax_r.text(2.45, RAM_TOTAL_MB + 120, "7,620 MB (tegrastats total)", ha="right", va="bottom", fontsize=FS_S)
ax_r.plot([0, 0, 1, 1], [5000, 5250, 5250, 5000], color=INK, lw=0.5)
ax_r.text(-0.4, 5350, f"+{asr_ram[0]:.0f} ± {asr_ram[1]:.0f} MB (n = 3 paired)", ha="left", va="bottom",
          fontsize=FS_S)
ax_r.set_yticks([0, 4000, 8192])
ax_r.set_yticklabels(["0", "4,000", "8,192"])
ax_r.set_ylabel("RAM (MB)", fontsize=FS)
ax_r.set_title("(d) Memory and power", fontsize=FS, loc="left")
ax_r.set_xticklabels([])
ax_p.set_ylim(0, 14)
bars(ax_p, pw, fmt="{:.1f}", dy=0.5)
ax_p.set_ylabel("Power (W)", fontsize=FS)

from matplotlib.lines import Line2D  # noqa: E402
handles = [Line2D([], [], color=INK, marker="_", ls="none", markersize=6, label="Between-run SD (error bar)"),
           Line2D([], [], marker="o", ls="none", markerfacecolor="white", markeredgecolor=INK, markersize=4,
                  label="95th percentile"),
           Line2D([], [], color=INK, lw=0.5, label="Paired Δ: ASR node = Full − Evaluated; LiDAR − Full")]
fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=FS_S, frameon=False, bbox_to_anchor=(0.5, -0.01))
save(fig, "fig-e1-resources")
