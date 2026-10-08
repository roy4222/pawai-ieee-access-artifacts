"""fig-e2a-asr (Fig. 10): speech-recognition tiers on the 77 recognizer runs.

Input:
  data/E2a-local/summary.json  tiers.{sv_local, whisper_gpu}.{latency_s.median,
      latency_s.p95, cer_micro, intent_retention, n}
  data/E2a-remote-r2/summary.json  tiers.remote.{n, latency_s.median, latency_s.p95}
      (latency_s is over the successful runs only: 1.706 / 2.743 s, equal to the ok-only recompute)
  Remote CER and intent retention are NOT read from summary.json (cer_micro 0.592 and
  intent_retention 0.961 include the timed-out runs). Successful-run values were recomputed
  from the raw per-utterance ASR log (not distributed; available on request), after the c10
  reference correction: CER 1.8 %, intent retention 100 %, 35 of 77 runs successful, 42 timed out.
E2a-remote (first run) is invalid and not used.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, ACCENT, GRAY, INK, FS, FS_S, save, clean_axes  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

EXP = ROOT / "data"
LOC = json.load(open(EXP / "E2a-local" / "summary.json"))["tiers"]
REM = json.load(open(EXP / "E2a-remote-r2" / "summary.json"))["tiers"]["remote"]
REMOTE_TIMEOUT = 42                 # raw ASR log: 42/77 timed out after warm-up
REMOTE_OK_CER = 0.018               # raw ASR log: successful 35 runs only
REMOTE_OK_INTENT = 1.0              # raw ASR log: successful 35 runs only
assert REM["n"] == 77 and LOC["sv_local"]["n"] == 77 and LOC["whisper_gpu"]["n"] == 77
ok = REM["n"] - REMOTE_TIMEOUT
TIERS = [("SenseVoice int8\non-board CPU", LOC["sv_local"]["latency_s"], LOC["sv_local"]["cer_micro"],
          LOC["sv_local"]["intent_retention"], INK, None, "n = 77"),
         ("Whisper tiny\non-board GPU", LOC["whisper_gpu"]["latency_s"], LOC["whisper_gpu"]["cer_micro"],
          LOC["whisper_gpu"]["intent_retention"], GRAY, None, "n = 77"),
         ("SenseVoice remote\n(deployed)", REM["latency_s"], REMOTE_OK_CER, REMOTE_OK_INTENT, ACCENT, "////",
          f"{ok} of {REM['n']} returned;\n{REMOTE_TIMEOUT} timed out")]
for t in TIERS:
    print(f"   {t[0].splitlines()[0]}: {t[1]['median']:.3f}/{t[1]['p95']:.3f} s  CER {t[2]:.3f}  intent {t[3]:.3f}")

fig, axes = plt.subplots(3, 1, figsize=(3.5, 4.0), sharey=True, gridspec_kw={"hspace": 0.75})
ys = [2, 1, 0]


def frame(ax, title, xlab):
    ax.set_title(title, fontsize=FS, loc="left", pad=3)
    ax.set_xlabel(xlab, fontsize=FS_S, labelpad=1)
    clean_axes(ax, grid_axis="x")
    ax.tick_params(axis="y", length=0)


ax = axes[0]
for y, (lab, lat, cer, it, col, hatch, note) in zip(ys, TIERS):
    ax.barh(y, lat["median"], height=0.6, color=col if not hatch else "white", edgecolor=col, hatch=hatch, lw=0.8, zorder=2)
    ax.plot([lat["median"], lat["p95"]], [y, y], color=INK, lw=0.7, zorder=3)
    ax.scatter([lat["p95"]], [y], s=14, facecolor="white", edgecolor=INK, lw=0.8, zorder=4)
    ax.text(lat["p95"] + 0.08, y, f"{lat['median']:.2f} (p95 {lat['p95']:.2f})", va="center", ha="left", fontsize=FS_S)
ax.set_xlim(0, 4.5)
frame(ax, "(a) Latency (bar: median; marker: 95th pct.)", "Recognition latency (s)")

ax = axes[1]
for y, (lab, lat, cer, it, col, hatch, note) in zip(ys, TIERS):
    ax.barh(y, cer * 100, height=0.6, color=col if not hatch else "white", edgecolor=col, hatch=hatch, lw=0.8, zorder=2)
    ax.text(cer * 100 + 0.3, y, f"{cer * 100:.1f}%", va="center", ha="left", fontsize=FS_S)
ax.set_xlim(0, 20)
ax.set_xticks([0, 5, 10, 15, 20])
frame(ax, "(b) Character error rate (CER)", "CER, micro-averaged (%)")

ax = axes[2]
for y, (lab, lat, cer, it, col, hatch, note) in zip(ys, TIERS):
    ax.barh(y, it * 100, height=0.6, color=col if not hatch else "white", edgecolor=col, hatch=hatch, lw=0.8, zorder=2)
    ax.text(it * 100 - 1.5, y, f"{it * 100:.1f}%", va="center", ha="right", fontsize=FS_S,
            color="white" if not hatch else INK,
            bbox=None if not hatch else dict(boxstyle="square,pad=0.05", facecolor="white", edgecolor="none"))
ax.set_xlim(0, 100)
frame(ax, "(c) Intent retention", "Runs whose keyword intent is preserved (%)")
for ax in axes:
    ax.set_yticks(ys)
    ax.set_yticklabels([t[0] for t in TIERS], fontsize=FS_S)
fig.text(0.01, 0.005, f"Remote tier (hatched): scored on the {ok} runs that returned;\n{REMOTE_TIMEOUT} of {REM['n']} runs timed out.",
         fontsize=FS_S, ha="left", va="bottom")
fig.subplots_adjust(left=0.30, right=0.97, top=0.95, bottom=0.15)
save(fig, "fig-e2a-asr")
