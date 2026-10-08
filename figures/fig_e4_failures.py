"""fig-e4-failures (Fig. 13): behavior under injected tier failures, n = 5 per scenario.

Input: data/E4/summary.json
  scenarios.S1-S7.{fault, candidate_latency_s.{median,min,max}, first_audio_approx_s.{median,min,max},
                   llm_decision, llm_model, proposal_reason, tts_provider, tts_cache_hits, spoken,
                   reply_errors, stop.{sent, webrtc_1003, source}}
  (S4, S5 already point at the r3 runs, see run_override)
  baseline.candidate_latency_median_s   E3 t2 - t1 median (1.58 s)
  E3 first-audio baseline t6 - t1 = 7.66 s is NOT in E4/summary.json; it is recomputed here from
  data/E3-chat-r1/summary.json rows[] (warmup rows dropped, n = 47, linear median).
Origin of every E4 time is the speech-intent event t1 (E4 has no t0).
n = 5: median and min-max range only, no p95. Tier that served the reply:
  dialogue  llm_decision ok + llm_model gpt -> cloud first tier; ok + gemini -> cloud second tier;
            fallback -> on-board rule-based responder; proposal_reason offline_mode -> reply unused
  speech    majority of tts_provider (openrouter_gemini = cloud first tier, edge_tts = cloud second
            tier, piper = on-board); tts_cache_hits = 5 -> cache replay
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, ACCENT, GRAY, INK, FS, FS_S, save, clean_axes, median  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

EXP = ROOT / "data"
E4 = json.load(open(EXP / "E4" / "summary.json"))
SC = E4["scenarios"]
e3 = [r for r in json.load(open(EXP / "E3-chat-r1" / "summary.json"))["rows"] if not r.get("warmup")]
assert len(e3) == 47
BASE = {"cand": E4["baseline"]["candidate_latency_median_s"], "audio": median([r["t6"] - r["t1"] for r in e3])}
print(f"   baseline cand {BASE['cand']:.3f}  audio {BASE['audio']:.3f}")

# scenario names = Table X names (named after the injection mechanism);
# S3 injects openrouter_request_timeout_s:=0.05
FAULT = {"llm_primary_bad": "Invalid\nprimary LLM", "llm_both_bad": "Both LLMs\ninvalid",
         "cloud_timeout": "LLM 0.05-s\ntimeout", "cloud_refused": "Invalid LLMs +\nGemini TTS",
         "tts_cloud_refused": "S4 + invalid\nedge-tts voice", "asr_down": "Remote ASR\nstopped",
         "offline_mode": "Manual\noffline mode"}
STY = {"first": dict(marker="o", color=ACCENT, fc=ACCENT), "second": dict(marker="D", color=ACCENT, fc="white"),
       "onboard": dict(marker="s", color=INK, fc=INK), "cache": dict(marker="^", color=GRAY, fc=GRAY),
       "unused": dict(marker="o", color=ACCENT, fc="white")}


def major(d):
    return max(d, key=d.get)


def llm_tier(s):
    if s["proposal_reason"].get("offline_mode"):
        return "unused", "GPT, unused"
    dec = major(s["llm_decision"])
    if dec == "fallback":
        return "onboard", "rules"
    if dec == "ok":
        m = major(s["llm_model"])
        return ("first", "GPT") if "gpt" in m else ("second", "Gemini")
    return None, None


def tts_tier(s):
    if s["tts_cache_hits"] == s["n"]:
        return "cache", "cache"
    p = major(s["tts_provider"])
    lab = {"openrouter_gemini": "Gemini TTS", "edge_tts": "edge-tts", "piper": "Piper"}.get(p)
    tier = {"openrouter_gemini": "first", "edge_tts": "second", "piper": "onboard"}.get(p)
    if lab and len(s["tts_provider"]) > 1:
        lab += f" {s['tts_provider'][p]}/{s['n']}"
    return tier, lab


keys = sorted(SC)
fig, axes = plt.subplots(2, 1, figsize=(7.16, 3.6), sharex=True, gridspec_kw={"hspace": 0.3})
ROWS = [("cand", "candidate_latency_s", llm_tier, "Speech-intent event →\ndialogue candidate (s)", 5.0),
        ("audio", "first_audio_approx_s", tts_tier, "Speech-intent event →\nfirst audio (s)", 12.5)]
for ax, (k, key, tierf, ylab, ymax) in zip(axes, ROWS):
    ax.axhline(BASE[k], color=GRAY, ls=(0, (3, 2)), lw=0.7, zorder=1)
    ax.text(len(keys) + 0.75, BASE[k] + ymax * 0.02, f"E3 baseline\n{BASE[k]:.2f} s\n(median,\nn = 47)",
            fontsize=FS_S, ha="right", va="bottom", color=GRAY, linespacing=1.05)
    for i, sk in enumerate(keys):
        s = SC[sk]
        st = s[key]
        tier, lab = tierf(s)
        if st["median"] is None:
            ax.scatter([i], [ymax * 0.12], marker="x", s=30, color=INK, lw=1.0, zorder=3)
            ax.text(i, ymax * 0.12 + ymax * 0.07, "no reply", ha="center", va="bottom", fontsize=FS_S)
            continue
        sy = STY[tier]
        ax.plot([i, i], [st["min"], st["max"]], color=sy["color"], lw=0.8, zorder=2)
        for e in (st["min"], st["max"]):
            ax.plot([i - 0.07, i + 0.07], [e, e], color=sy["color"], lw=0.8, zorder=2)
        ax.scatter([i], [st["median"]], marker=sy["marker"], s=26, color=sy["color"], facecolor=sy["fc"],
                   lw=0.9, zorder=3)
        ax.text(i + 0.12, st["median"], f"{st['median']:.2f}", ha="left", va="center", fontsize=FS_S)
        ytxt = st["max"] + ymax * 0.04
        extra = ""
        if k == "audio" and 0 < s["tts_cache_hits"] < s["n"]:
            extra = f"\n{s['tts_cache_hits']}/{s['n']} cached"
        ax.text(i, ytxt, lab + extra, ha="center", va="bottom", fontsize=FS_S, linespacing=1.05, zorder=4,
                bbox=dict(boxstyle="square,pad=0.05", facecolor="white", edgecolor="none"))
    ax.set_ylim(-ymax * 0.04, ymax)
    ax.set_ylabel(ylab, fontsize=FS)
    clean_axes(ax, grid_axis="y")
axes[0].set_xlim(-0.5, len(keys) + 0.8)
axes[1].set_xticks(range(len(keys)))
axes[1].set_xticklabels([f"{k}\n{FAULT[SC[k]['fault']]}" for k in keys], fontsize=FS_S)
axes[1].tick_params(axis="x", length=0)
# stop path row (all seven scenarios)
stops = all(SC[k]["stop"]["sent"] and SC[k]["stop"]["webrtc_1003"] for k in keys)
assert stops
axes[0].set_title("Origin: speech-intent event (t1). Markers: median; bars: range (n = 5). "
                  "Stop word reached the driver topic in all 7 scenarios.", fontsize=FS, loc="left", pad=4)
leg = [Line2D([], [], ls="none", markersize=5, markeredgewidth=0.9, marker=STY[t]["marker"],
              color=STY[t]["color"], markerfacecolor=STY[t]["fc"], label=l)
       for t, l in (("first", "Cloud, first tier"), ("second", "Cloud, second tier"),
                    ("onboard", "On-board last tier"), ("cache", "Replayed from cache"),
                    ("unused", "Cloud reply unused"))]
leg.append(Line2D([], [], ls="none", marker="x", color=INK, markersize=5, label="No reply"))
fig.legend(handles=leg, loc="lower center", ncol=6, fontsize=FS_S, frameon=False, bbox_to_anchor=(0.55, -0.01),
           handletextpad=0.3, columnspacing=1.2)
fig.subplots_adjust(left=0.12, right=0.99, top=0.93, bottom=0.2)
save(fig, "fig-e4-failures")
