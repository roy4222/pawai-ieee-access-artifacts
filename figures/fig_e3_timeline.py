"""fig-e3-timeline (Fig. 12): timeline of a spoken turn in the evaluated configuration, t0 origin.

Input: data/E3-chat-r1/summary.json, key rows[] (rows with warmup = true are
dropped; n = 47). Per row we compute, all from the row's own timestamps:
  endpoints (origin t0 = console send):  t1 - t0 speech-intent event, t3 - t0 arbitrated plan,
                                         t6 - t0 first audio (~ end of synthesis), t7 - t0 end of playback
  segments (lower panel):                t2 - t1 dialogue LLM, t6 - t5 speech synthesis, t7 - t6 playback
Not used: first_audio_approx_t6_t1 / end_to_end_upper_t7_t1 (t1 origin).
Percentiles are linear interpolation (_style.pct_linear). The endpoints are per-row
differences; they are NOT sums of the segment medians, and the segment bars all start at 0.
Expected: 0.99/1.33, 2.62/3.57, 8.67/10.57, 16.3/20.6 s; segments 1.58, 6.07, 7.07 s.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, ACCENT, GRAY, INK, LIGHT, FS, FS_S, save, clean_axes, pct_linear, median  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402

SRC = ROOT / "data" / "E3-chat-r1" / "summary.json"
d = json.load(open(SRC))
rows = [r for r in d["rows"] if not r.get("warmup")]
assert len(rows) == d["n"] == 47, (len(rows), d["n"])


def diff(a, b):
    v = [r[b] - r[a] for r in rows if r.get(a) is not None and r.get(b) is not None]
    assert len(v) == len(rows), (a, b, len(v))
    return v


ENDPOINTS = [("Speech-intent event (t1)", diff("t0", "t1")),
             ("Arbitrated plan (t3)", diff("t0", "t3")),
             ("First audio, ≈ end of synthesis (t6)", diff("t0", "t6")),
             ("End of playback (t7)", diff("t0", "t7"))]
SEGMENTS = [("Dialogue LLM (t2 − t1)", diff("t1", "t2")),
            ("Speech synthesis (t6 − t5)", diff("t5", "t6")),
            ("Playback (t7 − t6)", diff("t6", "t7"))]
for lab, v in ENDPOINTS + SEGMENTS:
    print(f"  {lab}: median {median(v):.3f}  p95 {pct_linear(v, 0.95):.3f}  max {max(v):.2f}")

fig, (ax, bx) = plt.subplots(2, 1, figsize=(7.16, 3.1), sharex=True,
                             gridspec_kw={"height_ratios": [4, 3], "hspace": 0.32})
rng = random.Random(7)
ys = list(range(len(ENDPOINTS)))[::-1]
for y, (lab, v) in zip(ys, ENDPOINTS):
    jit = [y + rng.uniform(-0.2, 0.2) for _ in v]
    ax.scatter(v, jit, s=6, color=LIGHT, lw=0, zorder=2, label="Individual turns" if y == ys[0] else None)
    m, p = median(v), pct_linear(v, 0.95)
    ax.plot([m, p], [y, y], color=INK, lw=0.8, zorder=3)
    ax.scatter([m], [y], s=22, color=ACCENT, edgecolor=ACCENT, zorder=4, label="Median" if y == ys[0] else None)
    ax.scatter([p], [y], s=22, facecolor="white", edgecolor=INK, lw=0.9, zorder=4,
               label="95th percentile" if y == ys[0] else None)
    ax.text(m, y + 0.26, f"{m:.2f} s  (p95 {p:.2f} s)", va="bottom", ha="left", fontsize=FS_S)
ax.set_yticks(ys)
ax.set_yticklabels([lab for lab, _ in ENDPOINTS], fontsize=FS)
ax.set_ylim(-0.45, len(ENDPOINTS) - 0.2)
ax.set_title(f"Endpoints, measured from the console send (t0); n = {len(rows)}", fontsize=FS, loc="left", pad=3)
clean_axes(ax, grid_axis="x")
ax.tick_params(axis="y", length=0)
ax.legend(loc="lower right", fontsize=FS_S, frameon=False, ncol=3, bbox_to_anchor=(1.0, 1.0),
          handletextpad=0.3, columnspacing=1.0)

ys2 = list(range(len(SEGMENTS)))[::-1]
for y, (lab, v) in zip(ys2, SEGMENTS):
    m, p = median(v), pct_linear(v, 0.95)
    bx.barh(y, m, height=0.5, left=0, color=GRAY, zorder=2)
    bx.text(m + 0.35, y, f"{m:.2f} s", va="center", ha="left", fontsize=FS_S)
bx.set_yticks(ys2)
bx.set_yticklabels([lab for lab, _ in SEGMENTS], fontsize=FS)
bx.set_ylim(-0.6, len(SEGMENTS) - 0.4)
bx.set_title("Segment medians (each bar starts at 0; not stacked, not summed into the endpoints)",
             fontsize=FS, loc="left", pad=3)
clean_axes(bx, grid_axis="x")
bx.tick_params(axis="y", length=0)
bx.set_xlim(0, 27)
bx.xaxis.set_major_locator(MultipleLocator(2))
bx.set_xlabel("Time (s)", fontsize=FS)
fig.subplots_adjust(left=0.235, right=0.985, top=0.9, bottom=0.14)
save(fig, "fig-e3-timeline")
