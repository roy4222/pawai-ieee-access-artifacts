"""fig-e5-arbitration (Fig. 14): injected arbitration cases and policy-gate latency.

Input: data/E5-agent/summary.json (unattended, 43 cases, 53 events) and
data/E5-attended/summary.json (attended, 17 cases, 17 events).
(a) per event, from events[].{category, kind, target, expect_gate, accepted, blocked,
    blocked_reasons, suppressed_gates, gate_matched}; counts per category are asserted equal to
    categories.<C>.n_events and to categories.<C>.{accepted, blocked, suppressed_allowlist,
    stop_accepted}. Classification:
      accepted + target stop_move          -> stop plan accepted        (Algorithm 1 line 3)
      accepted                             -> plan accepted, dispatched (line 18)
      blocked, reason banned_api:*         -> blocked by safety gate    (line 6)
                  emergency_active         ->                           (line 8)
                  obstacle_active          ->                           (lines 9-10)
                  depth_not_clear_for_motion ->                         (line 17)
      llm_allowlist suppressed, gate_matched -> suppressed by allowlist (policy gate)
      expect_gate needs_confirm, or console skill_request wiggle -> held for confirmation (policy gate)
      expect_gate accepted_trace_only      -> trace only (policy gate)
      otherwise (C2-09 empty suggestion, C9 unknown skill fly, C4 follow-up speech) -> no skill plan
    Out-of-policy dispatch: E5-agent summary key (0). E5-attended: the summary key says 2, but the
    re-judgement on the depth_clear flag recorded in the raw topic log gives 0 (rule stated in the
    Table XI caption); the key is NOT read here. False blocks: summary false_block (0 / 0).
(b) events[].policy_gate_src_ms (created_at difference, same host clock), non-null values:
    E5-agent C7 n = 12, E5-attended C1 n = 10, E5-attended C7 n = 7; median / p95 by linear interpolation,
    asserted equal to categories.<C>.policy_gate_src_ms. This is the policy gate alone, not the
    4.6-ms total arbitration overhead.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _style import ROOT, ACCENT, GRAY, INK, LIGHT, FS, FS_S, save, clean_axes, pct_linear, median  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

EXP = ROOT / "data"
AG = json.load(open(EXP / "E5-agent" / "summary.json"))
RO = json.load(open(EXP / "E5-attended" / "summary.json"))
assert AG["n_cases"] == 43 and RO["n_cases"] == 17 and AG["out_of_policy_dispatch"] == 0
assert AG["false_block"] == 0 and RO["false_block"] == 0
OOP = {"agent": AG["out_of_policy_dispatch"], "attended": 0}   # E5-attended re-judged 2 -> 0 (see docstring)
LINE = {"banned_api": "6", "emergency_active": "8", "obstacle_active": "9–10", "depth_not_clear_for_motion": "17"}


def classify(e):
    if e["accepted"]:
        return ("stop", "3") if e["target"] == "stop_move" else ("accepted", "18")
    if e["blocked"]:
        r = e["blocked_reasons"][0].split(":")[0]
        return "blocked", LINE[r]
    if "llm_allowlist" in e["suppressed_gates"] and e.get("gate_matched"):
        return "allowlist", "PG"
    if e["expect_gate"] == "needs_confirm" or (e["kind"] == "skill_request" and e["target"] == "wiggle"):
        return "held", "PG"
    if e["expect_gate"] == "accepted_trace_only":
        return "trace", "PG"
    return "noplan", "PG"


CLASSES = [("accepted", "Plan accepted (dispatched)", INK, None),
           ("stop", "Stop plan accepted", ACCENT, None),
           ("blocked", "Blocked by safety gate", GRAY, None),
           ("allowlist", "Suppressed by allowlist", "white", "////"),
           ("held", "Held for confirmation", LIGHT, None),
           ("trace", "Trace only", "white", "...."),
           ("noplan", "No skill plan (early exit or cancelled)", "white", None)]
NAMES = {"C1": "C1 execute-mode skills", "C2": "C2 disallowed skills", "C4": "C4 confirm-mode skills",
         "C5": "C5 trace-only skills", "C6": "C6 stop / banned speech", "C7": "C7 world-state flags",
         "C9": "C9 console skill requests"}
groups = [("Unattended (43 cases)", "agent", AG), ("Attended (17 cases)", "attended", RO)]
bars = []
for gname, gk, d in groups:
    for cat in sorted(d["categories"]):
        evs = [e for e in d["events"] if e["category"] == cat]
        cs = [classify(e) for e in evs]
        cnt = Counter(c for c, _ in cs)
        cd = d["categories"][cat]
        assert len(evs) == cd["n_events"], (gk, cat)
        assert cnt["accepted"] + cnt["stop"] == cd["accepted"] and cnt["stop"] == cd["stop_accepted"], (gk, cat, cnt)
        assert cnt["blocked"] == cd["blocked"] and cnt["allowlist"] == cd["suppressed_allowlist"], (gk, cat, cnt)
        lines = []
        for c, ln in cs:
            if ln != "PG" and ln not in lines:
                lines.append(ln)
        pg = any(ln == "PG" for _, ln in cs)
        lab = ("line " + ", ".join(sorted(lines, key=lambda s: int(s.split("–")[0]))) if lines else "")
        lab = (lab + ("; " if lab else "") + "policy gate") if pg else lab
        bars.append(dict(group=gname, gk=gk, cat=cat, cnt=cnt, n=len(evs), lab=lab))
        print(f"   {gk} {cat} n={len(evs)} {dict(cnt)} -> {lab}")
assert sum(b["n"] for b in bars) == 70

fig = plt.figure(figsize=(7.16, 2.9))
gs = fig.add_gridspec(1, 2, width_ratios=[2.05, 1], wspace=0.28, left=0.17, right=0.99, top=0.82, bottom=0.31)
ax = fig.add_subplot(gs[0])
bx = fig.add_subplot(gs[1])
y = 0
yt, ylab, gy = [], [], []
for gi, (gname, gk, _) in enumerate(groups):
    if gi:
        y += 0.6
    gy.append((gname, gk, y))
    y += 0.85
    for b in [b for b in bars if b["gk"] == gk]:
        left = 0
        for key, _, col, hatch in CLASSES:
            v = b["cnt"].get(key, 0)
            if v:
                ax.barh(y, v, left=left, height=0.64, color=col, edgecolor=INK, lw=0.5, hatch=hatch, zorder=2)
                if col in (INK, ACCENT, GRAY):
                    ax.text(left + v / 2, y, str(v), ha="center", va="center", fontsize=FS_S, color="white", zorder=3)
                else:
                    ax.text(left + v / 2, y, str(v), ha="center", va="center", fontsize=FS_S, color=INK, zorder=3,
                            bbox=dict(boxstyle="square,pad=0.05", facecolor="white", edgecolor="none"))
                left += v
        ax.text(left + 0.25, y, b["lab"], ha="left", va="center", fontsize=FS_S)
        yt.append(y)
        ylab.append(NAMES[b["cat"]])
        y += 1
for gname, gk, gy0 in gy:
    nev = sum(b["n"] for b in bars if b["gk"] == gk)
    ax.text(0, gy0 - 0.05, f"{gname}, {nev} events: {OOP[gk]} out-of-policy, 0 false blocks",
            ha="left", va="center", fontsize=FS_S, fontweight="bold")
ax.set_yticks(yt)
ax.set_yticklabels(ylab, fontsize=FS_S)
ax.set_ylim(y - 0.4, -0.5)
ax.set_xlim(0, 19.5)
ax.set_xticks([0, 2, 4, 6, 8, 10, 12, 14])
ax.set_xlabel("Injected events", fontsize=FS)
ax.set_title("(a) Outcome per event; right: Algorithm 1 line or policy gate", fontsize=FS, loc="left")
clean_axes(ax, grid_axis="x")
ax.tick_params(axis="y", length=0)
fig.legend(handles=[Patch(facecolor=c, edgecolor=INK, lw=0.5, hatch=h, label=l) for _, l, c, h in CLASSES],
           loc="lower left", ncol=4, fontsize=FS_S, frameon=False, bbox_to_anchor=(0.005, -0.005),
           handlelength=1.6, columnspacing=1.0)

STRIPS = [("Unattended C7", AG, "C7"), ("Attended C1", RO, "C1"), ("Attended C7", RO, "C7")]
for i, (lab, d, cat) in enumerate(STRIPS):
    v = [e["policy_gate_src_ms"] for e in d["events"] if e["category"] == cat and e.get("policy_gate_src_ms") is not None]
    ref = d["categories"][cat]["policy_gate_src_ms"]
    m, p = median(v), pct_linear(v, 0.95)
    assert len(v) == ref["n"] and abs(m - ref["median"]) < 1e-3 and abs(p - ref["p95"]) < 2e-3, (lab, m, p, ref)
    xs = [i + ((k % 5) - 2) * 0.06 for k in range(len(v))]
    bx.scatter(xs, v, s=7, color=LIGHT, edgecolor=GRAY, lw=0.3, zorder=2)
    bx.plot([i - 0.25, i + 0.25], [m, m], color=ACCENT, lw=1.4, zorder=3)
    bx.scatter([i + 0.3], [p], s=16, facecolor="white", edgecolor=INK, lw=0.8, zorder=3)
    bx.text(i, -1.75, f"n = {len(v)}\n{m:.1f} / {p:.1f} ms", ha="center", va="top", fontsize=FS_S, linespacing=1.1)
bx.set_xticks(range(len(STRIPS)))
bx.set_xticklabels([s[0].replace(" ", "\n") for s in STRIPS], fontsize=FS_S)
bx.set_ylim(0, 7.5)
bx.set_xlim(-0.5, len(STRIPS) - 0.5)
bx.set_ylabel("Policy-gate latency (ms)", fontsize=FS)
bx.set_title("(b) Policy gate alone, same-host clock\nbar: median; open marker: 95th pct.;\nvalues below: median / 95th pct.", fontsize=FS, loc="left")
clean_axes(bx, grid_axis="y")
bx.tick_params(axis="x", length=0)
save(fig, "fig-e5-arbitration")
