"""fig-e1-nodes (Fig. 9): per-node CPU (percent of one core) in the three E1 configurations.

Input: data/E1-A-r1|r2/summary.json segments.{full,console} and
E1-B-r1|r2/summary.json segments.lidar, key segments.<seg>.nodes.<node>.cpu_pct_mean
(inner keys confirmed on first run: cpu_pct_mean, n, rss_mb_mean). Mean of the two runs
(r1-r2; E1-A-r3 excluded). Node names come from harness/pawexp.toml [nodes].
The figure lists 13 nodes; the data also hold asr (full and LiDAR only; stopped in the
evaluated configuration), lidar and reactive (LiDAR only). They are drawn because the text
cites them; a missing bar means the node does not run in that configuration.
"""
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, ACCENT, GRAY, INK, FS, FS_S, save, clean_axes  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

EXP = ROOT / "data"
CFG = [("Full", [("E1-A-r1", "full"), ("E1-A-r2", "full")]),
       ("Evaluated", [("E1-A-r1", "console"), ("E1-A-r2", "console")]),
       ("LiDAR", [("E1-B-r1", "lidar"), ("E1-B-r2", "lidar")])]
COLORS = [INK, GRAY, ACCENT]
LABEL = {"face": "Face identity (YuNet + SFace)", "vision": "Gesture and pose (MediaPipe)",
         "object": "Object detection (YOLO26n)", "go2 driver": "Go2 driver (WebRTC)",
         "camera": "RealSense camera driver", "gateway": "Operator gateway",
         "lidar": "LiDAR scanner driver", "asr": "On-board ASR (Whisper tiny)",
         "depth_safety": "Depth-safety node", "llm": "Dialogue graph (LLM client)",
         "brain": "Dialogue manager (brain)", "reactive": "Reactive-stop node",
         "executive": "Executive", "fox": "Foxglove bridge", "tts": "Speech synthesis (TTS)",
         "ollama": "Ollama server (idle)"}
PERCEPTION = ("face", "vision", "object")

vals = []
for _, runs in CFG:
    segs = [json.load(open(EXP / r / "summary.json"))["segments"][s]["nodes"] for r, s in runs]
    names = set().union(*segs)
    v = {}
    for n in names:
        xs = [x[n]["cpu_pct_mean"] for x in segs if n in x]
        assert len(xs) == len(segs), (runs, n)   # a node present in one run only would bias the mean
        v[n] = st.mean(xs)
    vals.append(v)
nodes = sorted(set().union(*vals), key=lambda n: -max(v.get(n, 0) for v in vals))
assert set(nodes) <= set(LABEL), set(nodes) - set(LABEL)
perc = [sum(v[n] for n in PERCEPTION) for v in vals]
print("   perception sum:", [f"{p:.1f}" for p in perc], " driver:", [f"{v['go2 driver']:.1f}" for v in vals])

H = 0.27
fig, ax = plt.subplots(figsize=(3.5, 4.3))


def num(x):
    return "–" if x is None else (f"{x:.0f}" if x >= 10 else f"{x:.1f}")


for i, n in enumerate(nodes):
    for j, (v, c) in enumerate(zip(vals, COLORS)):
        if n in v:
            ax.barh(i + (j - 1) * H, v[n], height=H, color=c, zorder=2)
    top = max(v.get(n, 0) for v in vals)
    txt = " / ".join(num(v.get(n)) for v in vals)
    if n == "face":   # too long to sit right of the longest bar; given in the note instead
        continue
    ax.text(top + 3, i, txt, va="center", ha="left", fontsize=FS_S)
ax.set_yticks(range(len(nodes)))
ax.set_yticklabels([LABEL[n] for n in nodes], fontsize=FS_S)
ax.set_ylim(len(nodes) - 0.5, -0.75)
ax.set_xlim(0, 240)
ax.set_xlabel("CPU (% of one core)", fontsize=FS)
clean_axes(ax, grid_axis="x")
ax.tick_params(axis="y", length=0)
face = " / ".join(num(v["face"]) for v in vals)
ax.text(238, 5.55, "Values: Full / Eval. / LiDAR\n"
        f"Face identity: {face}\n"
        "Three perception nodes\n(face, gesture/pose, object):\n" + " / ".join(f"{p:.0f}" for p in perc),
        ha="right", va="top", fontsize=FS_S, linespacing=1.25)
ax.legend(handles=[Patch(color=c, label=n) for (n, _), c in zip(CFG, COLORS)], loc="lower right",
          fontsize=FS_S, frameon=False, title="Mean of 2 runs\n– = not running",
          title_fontsize=FS_S, bbox_to_anchor=(1.02, 0.0))
fig.subplots_adjust(left=0.42, right=0.98, top=0.995, bottom=0.085)
save(fig, "fig-e1-nodes")
