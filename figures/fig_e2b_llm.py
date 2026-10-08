"""fig-e2b-llm (Fig. 11): dialogue-model tiers on the 40-sentence bank.

Input: data/E2b-prelim/summary.json and E2b-7b/summary.json
  summary[].{model, backend, total_med}     median total latency (s)
  per_call[].{model, utterance, total_s, correct}
  bank/bank.json items[].{text, subset}   subset in {chat (25), skill (10), status (5)};
  per_call is joined to the bank by utterance text (filter on subset, not mode).
p95 = linear interpolation over per_call[].total_s (not None);
summary[].total_p95 is nearest-rank and is NOT used. Correct counts are recomputed from per_call
and asserted equal to summary[].skill_correct.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, ACCENT, GRAY, INK, LIGHT, FS, FS_S, save, clean_axes, pct_linear  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

EXP = ROOT / "data"
bank = {i["text"]: i["subset"] for i in json.load(open(ROOT / "bank" / "bank.json"))["items"]}
summ, calls = [], []
for run in ("E2b-prelim", "E2b-7b"):
    d = json.load(open(EXP / run / "summary.json"))
    summ += d["summary"]
    calls += d["per_call"]

NAME = {"openai/gpt-5.4-mini": "GPT-5.4 mini", "google/gemini-3-flash-preview": "Gemini 3 Flash",
        "Qwen/Qwen2.5-7B-Instruct": "Qwen2.5-7B (server)"}
SUBSETS = [("chat", 25), ("skill", 10), ("status", 5)]
models = []
for s in summ:
    m = s["model"]
    pc = [c for c in calls if c["model"] == m]
    assert len(pc) == 40, (m, len(pc))
    corr = {k: sum(1 for c in pc if c["correct"] and bank[c["utterance"]] == k) for k, _ in SUBSETS}
    assert sum(corr.values()) == s["skill_correct"], (m, corr, s["skill_correct"])
    tier = "cloud" if s["backend"] == "openrouter" else ("server" if s["backend"] == "vllm" else "onboard")
    models.append(dict(model=m, name=NAME.get(m, m), tier=tier, med=s["total_med"],
                       p95=pct_linear([c["total_s"] for c in pc if c["total_s"] is not None], 0.95),
                       corr=corr, total=s["skill_correct"]))
for x in models:
    print(f"   {x['name']:<22} med {x['med']:.2f} p95 {x['p95']:.2f} correct {x['total']} {x['corr']}")
COL = {"cloud": ACCENT, "server": GRAY, "onboard": INK}
MK = {"cloud": "o", "server": "s", "onboard": "^"}

fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.16, 2.75), gridspec_kw={"width_ratios": [1.15, 1], "wspace": 0.55})
order = sorted(models, key=lambda x: (-x["total"], x["med"]))   # panel (b) order; (a) uses the same index numbers
for i, x in enumerate(order, 1):
    x["idx"] = i
# index-label offsets in points, set by hand so close points stay legible (checked on the PNG)
OFF = {1: (0, 6), 2: (-7, 0), 3: (-7, 0), 4: (-6, -6), 5: (0, 6), 6: (0, -7), 7: (0, 6), 8: (-7, -2), 9: (0, -7)}
for x in models:
    c = COL[x["tier"]]
    ax.plot([x["med"], x["p95"]], [x["total"]] * 2, color=c, lw=0.7, zorder=2)
    ax.plot([x["p95"]] * 2, [x["total"] - 0.6, x["total"] + 0.6], color=c, lw=0.7, zorder=2)
    ax.scatter([x["med"]], [x["total"]], s=22, marker=MK[x["tier"]], color=c, zorder=3)
    dx, dy = OFF[x["idx"]]
    ax.annotate(str(x["idx"]), (x["med"], x["total"]), xytext=(dx, dy), textcoords="offset points",
                ha="center", va="center", fontsize=FS_S, color=c, fontweight="bold")
ax.axvline(5, color=INK, ls=(0, (3, 2)), lw=0.6)
ax.text(5.15, 38, "5-s dialogue budget", fontsize=FS_S, ha="left", va="center")
ax.set_xlim(0, 12.5)
ax.set_ylim(-4, 41)
ax.set_xlabel("Total latency per call (s): median, line to 95th percentile", fontsize=FS)
ax.set_ylabel("Correct selections (of 40)", fontsize=FS)
ax.set_title("(a) Latency against correct selections (numbers as in (b))", fontsize=FS, loc="left")
clean_axes(ax, grid_axis="both")
ax.legend(handles=[Line2D([], [], marker=MK[t], color=COL[t], ls="none", markersize=4.5, label=l)
                   for t, l in (("cloud", "Cloud"), ("server", "Remote GPU server (vLLM)"), ("onboard", "On-board (Ollama)"))],
          loc="center right", fontsize=FS_S, frameon=False, bbox_to_anchor=(1.0, 0.5))

SHADE = [INK, GRAY, LIGHT]
ys = range(len(order))
for y, x in zip(ys, order):
    left = 0
    for (k, n), c in zip(SUBSETS, SHADE):
        v = x["corr"][k]
        bx.barh(y, v, left=left, height=0.62, color=c, edgecolor="white", lw=0.4, zorder=2)
        left += v
    bx.text(left + 0.6, y, f"{x['corr']['chat']} · {x['corr']['skill']} · {x['corr']['status']}  ({x['total']})",
            va="center", ha="left", fontsize=FS_S)
bx.set_yticks(list(ys))
bx.set_yticklabels([f"{x['name']}  {x['idx']}" for x in order], fontsize=FS_S)
for t, x in zip(bx.get_yticklabels(), order):
    t.set_color(COL[x["tier"]])
bx.set_ylim(len(order) - 0.5, -0.5)
bx.set_xlim(0, 52)
bx.set_xticks([0, 10, 20, 30, 40])
bx.set_xlabel("Correct selections", fontsize=FS)
bx.set_title("(b) Correct by subset: chat/25 · skill/10 · status/5", fontsize=FS, loc="left")
clean_axes(bx, grid_axis="x")
bx.tick_params(axis="y", length=0)
bx.legend(handles=[Patch(color=c, label=f"{k} (of {n})") for (k, n), c in zip(SUBSETS, SHADE)],
          loc="lower right", fontsize=FS_S, frameon=False)
fig.subplots_adjust(left=0.07, right=0.99, top=0.9, bottom=0.16)
save(fig, "fig-e2b-llm")
