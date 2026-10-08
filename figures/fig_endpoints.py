"""fig-endpoints (Fig. 6): measurement endpoints t0-t8 of a spoken turn (definitions only, no values).

Definitions follow analysis/analyze_e3.py per_utterance() and the E3 rows[] keys:
  t0  console send (play.jsonl t_send on the operator laptop, shifted by meta.clock_offset_s)
  t1  /event/speech_intent_recognized   speech-intent event (after remote ASR, via the gateway)
  t2  /brain/chat_candidate             LLM candidate from the dialogue graph
  t3  /brain/proposal                   plan from the dialogue manager (arbitrated)
  t4  /brain/skill_result ACCEPTED      executive verdict = dispatch
  t5  /tts                              speech request
  t6  TTS log "Cached"                  end of synthesis (approximates first audio)
  t7  TTS log "played"                  end of playback
  t8  /brain/skill_result STEP_STARTED motion   driver request (/webrtc_req), motion skills only
t1-t8 are arrival times at the on-board topic logger; the policy gate uses the messages'
own created_at (t3_src - t2_src, same host clock; summary key policy_gate_src_ms).
Explanatory figure in the line style of fig_arbitration.py; positions are schematic, not to scale.
"""
from _style import *

W, H = 7.16, 3.35
fig, ax = new_fig(W, H, y0=0.37)
LANES = ["Operator console", "Gateway and remote ASR", "Dialogue graph (LLM)", "Dialogue manager",
         "Executive", "Speech synthesis", "Playback (USB speaker)", "Go2 driver"]
LH = 0.255
TOP = H - 0.55
X0 = 1.55
for i, name in enumerate(LANES):
    y0 = TOP - LH * (i + 1)
    ax.add_patch(Rectangle((0.05, y0), W - 0.1, LH, facecolor=PANEL if i % 2 == 0 else "white",
                           edgecolor="#9A9A9A", lw=0.5, zorder=0))
    ax.text(0.1, y0 + LH / 2, name, fontsize=FS_S, ha="left", va="center")
ax.add_line(Line2D([X0 - 0.07] * 2, [TOP - LH * len(LANES), TOP], color="#9A9A9A", lw=0.5))


def ly(i):
    return TOP - LH * i - LH / 2


EV = {"t0": (0, 1.7, "send"), "t1": (1, 2.3, "speech-intent\nevent"), "t2": (2, 2.9, "LLM\ncandidate"),
      "t3": (3, 3.45, "plan"), "t4": (4, 3.95, "dispatch"), "t5": (5, 4.45, "synthesis start"),
      "t6": (5, 5.6, "synthesis end\n(≈ first audio)"), "t7": (6, 6.25, "end of\nplayback"),
      "t8": (7, 4.6, "driver request\n(motion skills)")}
P = {}
for k, (lane, x, txt) in EV.items():
    y = ly(lane)
    P[k] = (x, y)
    ax.add_patch(matplotlib.patches.Circle((x, y), 0.032, facecolor=INK, edgecolor=INK, zorder=5))
    ax.text(x + 0.07, y + 0.01, f"{k} {txt}" if "\n" not in txt else f"{k} " + txt.replace("\n", "\n    "),
            fontsize=FS_S, ha="left", va="center", zorder=6, linespacing=1.0)

seq = ["t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7"]
for a, b in zip(seq, seq[1:]):
    (xa, ya), (xb, yb) = P[a], P[b]
    if ya == yb:
        path(ax, [(xa + 0.04, ya - 0.085), (xb - 0.04, yb - 0.085), (xb - 0.04, yb - 0.04)], lw=0.6)
    else:
        path(ax, [(xa, ya - 0.05), (xa, yb), (xb - 0.06, yb)], lw=0.6)
path(ax, [(P["t4"][0], P["t4"][1] - 0.05), (P["t4"][0], P["t8"][1]), (P["t8"][0] - 0.06, P["t8"][1])],
     lw=0.6, ls=(0, (3, 2)))

# RQ2 endpoints, all from t0 (above the lanes)
ys = [TOP + 0.08, TOP + 0.19, TOP + 0.30, TOP + 0.41]
for (k, name), y in zip([("t1", "t1 − t0"), ("t3", "t3 − t0"), ("t6", "t6 − t0"), ("t7", "t7 − t0")], ys):
    x0, x1 = P["t0"][0], P[k][0]
    ax.add_line(Line2D([x0, x1], [y, y], color=ACCENT, lw=0.8))
    for x in (x0, x1):
        ax.add_line(Line2D([x, x], [y - 0.035, y + 0.035], color=ACCENT, lw=0.8))
    ax.text(x1 + 0.05, y, name, fontsize=FS_S, color=ACCENT, ha="left", va="center")
ax.text(0.1, TOP + 0.25, "RQ2 endpoints\n(origin t0)", fontsize=FS_S, color=ACCENT, ha="left", va="center",
        fontweight="bold", linespacing=1.05)

# RQ4 gates (below the lanes)
yb = TOP - LH * len(LANES) - 0.13
for (a, b, name), y in [(("t2", "t3", "policy gate t3 − t2: created_at, same host clock"), yb),
                        (("t3", "t4", "safety gate t4 − t3: message arrival times"), yb - 0.15)]:
    x0, x1 = P[a][0], P[b][0]
    ax.add_line(Line2D([x0, x1], [y, y], color=INK, lw=0.8))
    for x in (x0, x1):
        ax.add_line(Line2D([x, x], [y - 0.035, y + 0.035], color=INK, lw=0.8))
    ax.text(x1 + 0.07, y, name, fontsize=FS_S, ha="left", va="center")
ax.text(0.1, yb - 0.075, "RQ4 gates", fontsize=FS_S, ha="left", va="center", fontweight="bold")
ax.text(W - 0.08, yb, "schematic, not to scale", fontsize=FS_S, ha="right", va="center",
        color=GRAY, style="italic")
save(fig, "fig-endpoints")
