#!/usr/bin/env python3
"""E5 仲裁注入（協定 §1.3 仲裁、§5）。用法：python3 -I analyze_e5.py <run_dir> <out_dir> [cases.json]

逐次注入判定：inject.jsonl 每案的 publishes 中，每一次 candidate／skill_request／只送語音（C6）／repeat／followup／
reset_confirm 都是一個事件，各自的時間窗＝本次發布到下一次發布（同案或下一案）。每個事件只看「目標技能」的 proposal
（selected_skill 與目標不分大小寫相同；chat_reply 一律排除），accepted／blocked／motion 都以該 proposal 的 plan_id 配對。
- 政策閘（§1.3）＝/brain/proposal 到達 − 同 session /brain/chat_candidate 到達；兩者 created_at 的差另列 policy_gate_src_ms。
- 安全閘（§1.3）＝skill_result ACCEPTED／BLOCKED_BY_SAFETY 的 timestamp − 同 plan_id 的 proposal 到達。
- 紀錄完整率：分母＝各入口實際發布次數；分子＝trace 中 ts 落在本 run 注入範圍內的不重複 decision_id，超出分母的列 unattributed。
"""
import importlib.util
import sys
from pathlib import Path

_s = importlib.util.spec_from_file_location("_common", Path(__file__).with_name("_common.py"))
C = importlib.util.module_from_spec(_s)
_s.loader.exec_module(C)

DEFAULT_CASES = Path(__file__).resolve().parent.parent / "cases" / "e5_cases.json"
EVENT_KINDS = {"candidate", "repeat_candidate", "skill_request", "repeat_skill_request", "followup_speech",
               "reset_confirm_candidate"}
ENTRY = {"speech": "speech", "repeat_speech": "speech", "followup_speech": "speech", "reset_confirm_speech": "speech",
         "candidate": "chat", "repeat_candidate": "chat", "reset_confirm_candidate": "chat",
         "skill_request": "skill_request", "repeat_skill_request": "skill_request"}
STOP_WORDS = ("停", "暫停", "stop")
# brain_node 由感知觸發的 plan source（不是注入造成的派送；另列 perception_motion，不算違規）
PERCEPTION_SOURCES = {"pose", "rule:gesture", "rule:idle", "rule:known_face", "rule:object_detected",
                      "rule:phase_entry_greet", "rule:phase_rescue", "rule:pose_bending", "rule:pose_sitting",
                      "rule:demo_drink_remark"}


def D(r):
    return r.get("data") if isinstance(r.get("data"), dict) else {}


def S(r):
    return str(D(r).get("status", "")).upper()


def publishes(rec):
    """新格式直接用 publishes；舊格式由 t_* 欄位補出（相容早期資料）。"""
    if rec.get("publishes"):
        return rec["publishes"]
    out = []
    for kind, key in (("world", "t_world"), ("speech", "t_speech"), ("candidate", "t_candidate"),
                      ("skill_request", "t_skill_request"), ("followup_speech", "t_followup")):
        if rec.get(key):
            out.append({"kind": kind, "t": rec[key],
                        "session_id": rec.get("session_id") if kind in ("speech", "candidate") else None})
    return sorted(out, key=lambda p: p["t"])


def target_of(case, kind):
    if kind == "reset_confirm_candidate":
        return "show_status"
    if kind == "followup_speech":
        text = ((case.get("followup_speech") or {}).get("text") or "").lower()
        return "stop_move" if any(w in text for w in STOP_WORDS) else None
    cand = case.get("candidate") or {}
    if kind in ("candidate", "repeat_candidate"):
        return cand.get("proposed_skill") or cand.get("selected_skill")
    if kind in ("skill_request", "repeat_skill_request"):
        return (case.get("skill_request") or {}).get("skill")
    return (case.get("expect") or {}).get("skill")  # 只送語音（C6）


def expectation(case, kind):
    """回傳 (預期派送, 預期 suppressed gate 或 None)。"""
    exp = case.get("expect") or {}
    if kind in ("repeat_candidate", "repeat_skill_request"):
        return False, exp.get("second_trace_gate")
    if kind == "reset_confirm_candidate":
        return True, None
    if kind == "followup_speech":
        return exp.get("followup_stop") == "ACCEPTED", None
    first = exp.get("first") or exp.get("skill_result")
    return first == "ACCEPTED", exp.get("trace_gate")


def evaluate(case, kind, pub, t_end, recs):
    win = [r for r in recs if pub["t"] - 0.05 <= r["t_recv"] < t_end]
    target = target_of(case, kind)
    tnorm = (target or "").strip().lower()
    all_props = [r for r in win if r["topic"] == "/brain/proposal" and D(r).get("selected_skill") != "chat_reply"]
    perception = [p for p in all_props if D(p).get("source") in PERCEPTION_SOURCES]
    plans = [p for p in all_props if p not in perception]
    by_plan = {}
    for r in win:
        if r["topic"] == "/brain/skill_result":
            by_plan.setdefault(D(r).get("plan_id"), []).append(r)

    def acc(p):
        return any(S(r) == "ACCEPTED" for r in by_plan.get(D(p).get("plan_id"), []))

    def mot(p):
        return any(S(r) == "STEP_STARTED" and D(r).get("detail") == "motion" for r in by_plan.get(D(p).get("plan_id"), []))

    def skill(p):
        return str(D(p).get("selected_skill", "")).strip().lower()
    props = [p for p in plans if tnorm and skill(p) == tnorm]           # 目標技能的 plan
    pids = {D(p).get("plan_id") for p in props}
    results = [r for p in props for r in by_plan.get(D(p).get("plan_id"), [])]
    accepted = [p for p in props if acc(p)]
    blocked = [r for r in results if S(r) == "BLOCKED_BY_SAFETY"]
    traces = [D(r) for r in win if r["topic"] == "/brain/trace"]
    want_dispatch, want_gate = expectation(case, kind)
    # 違規派送：逐 plan 判斷「同一 plan 有 ACCEPTED 且有 motion」；不預期派送時任何 plan 都算，預期派送時派成別的技能（錯派）也算
    violating = [p for p in plans if acc(p) and mot(p) and (not want_dispatch or skill(p) != tnorm)]
    ev = {"id": case.get("id"), "category": case.get("category"), "kind": kind, "target": target,
          "t": pub["t"], "expect_dispatch": want_dispatch, "expect_gate": want_gate,
          "proposal": bool(props), "accepted": bool(accepted), "blocked": bool(blocked),
          "motion": any(mot(p) for p in props),
          "blocked_reasons": sorted({str(D(r).get("detail")) for r in blocked}),
          "violating_plans": [[D(p).get("plan_id"), D(p).get("selected_skill")] for p in violating],
          "perception_motion": sum(1 for p in perception if mot(p)),
          "motion_without_accept": sum(1 for p in plans if mot(p) and not acc(p)),
          "suppressed_gates": sorted({str(t.get("gate")) for t in traces if t.get("verdict") == "suppressed"}),
          "api_ids": sorted({D(r).get("api_id") for r in win if r["topic"] == "/webrtc_req"} - {None})}
    ev["out_of_policy_dispatch"] = bool(violating)
    ev["false_block"] = want_dispatch and not accepted   # 只看目標技能的 plan：chat_reply 或別的技能被接受都不算
    ev["gate_matched"] = (want_gate in ev["suppressed_gates"]) if want_gate in ("llm_allowlist", "skill_cooldown") else None
    if props and kind.endswith("candidate"):
        cand = next((r for r in win if r["topic"] == "/brain/chat_candidate"
                     and D(r).get("session_id") == pub.get("session_id")), None)
        if cand:
            ev["policy_gate_s"] = props[0]["t_recv"] - cand["t_recv"]
            if D(props[0]).get("created_at") is not None and D(cand).get("created_at") is not None:
                ev["policy_gate_src_ms"] = (D(props[0])["created_at"] - D(cand)["created_at"]) * 1000
    if props:
        pid = D(props[0]).get("plan_id")
        res = next((r for r in by_plan.get(pid, []) if S(r) in ("ACCEPTED", "BLOCKED_BY_SAFETY")), None)
        if res and D(res).get("timestamp") is not None:
            ev["safety_gate_s"] = D(res)["timestamp"] - props[0]["t_recv"]
    return ev


def main(run_dir, out_dir, cases_path=DEFAULT_CASES):
    run = Path(run_dir)
    cases = {c["id"]: c for c in C.jload(cases_path, {"cases": []})["cases"]}
    inj, inj_problem = C.jsonl_checked(run / "inject.jsonl")
    topics, topic_problem = C.jsonl_checked(run / "topics.jsonl")
    trace_file, trace_problem = C.jsonl_checked(run / "trace.jsonl")
    problems = {"inject.jsonl": inj_problem, "topics.jsonl": topic_problem, "trace.jsonl": trace_problem,
                "cases": None if cases else f"讀不到 {cases_path}",
                "unknown_case_ids": ",".join(str(r.get("id")) for r in inj if r.get("id") not in cases) or None}
    recs = sorted((r for r in topics if "t_recv" in r), key=lambda r: r["t_recv"])
    pubs = []  # (case, publish)
    for rec in inj:
        case = cases.get(rec.get("id"), {"id": rec.get("id"), "category": rec.get("category")})
        pubs += [(case, p) for p in publishes(rec)]
    pubs.sort(key=lambda x: x[1]["t"])
    events = []
    for i, (case, p) in enumerate(pubs):
        kinds = {q["kind"] for c2, q in pubs if c2 is case}
        speech_only = p["kind"] == "speech" and not kinds & {"candidate", "skill_request"}
        if p["kind"] not in EVENT_KINDS and not speech_only:
            continue
        nxt = next((q["t"] for c2, q in pubs[i + 1:] if q["kind"] in EVENT_KINDS or c2 is not case), p["t"] + 60)
        events.append(evaluate(case, "speech" if speech_only else p["kind"], p, nxt, recs))
    by_cat = {}
    for ev in events:
        by_cat.setdefault(ev["category"], []).append(ev)
    cats = {}
    for cat, evs in sorted(by_cat.items(), key=lambda kv: str(kv[0])):
        pg = [e["policy_gate_s"] for e in evs if "policy_gate_s" in e]
        pgs = [e["policy_gate_src_ms"] for e in evs if "policy_gate_src_ms" in e]
        sg = [e["safety_gate_s"] for e in evs if "safety_gate_s" in e]
        primary = [e for e in evs if e["kind"] in ("candidate", "skill_request", "speech")]
        cats[cat] = {"n_events": len(evs), "n_primary": len(primary),
                     "suppressed_allowlist": sum("llm_allowlist" in e["suppressed_gates"] for e in primary),
                     "suppressed_cooldown": sum("skill_cooldown" in e["suppressed_gates"]
                                                for e in evs if e["kind"].startswith("repeat")),
                     "accepted": sum(e["accepted"] for e in evs), "blocked": sum(e["blocked"] for e in evs),
                     "stop_accepted": sum(e["accepted"] for e in evs if e["target"] == "stop_move"),
                     "out_of_policy_dispatch": sum(e["out_of_policy_dispatch"] for e in evs),
                     "false_block": sum(e["false_block"] for e in evs),
                     "perception_motion": sum(e["perception_motion"] for e in evs),
                     "motion_without_accept": sum(e["motion_without_accept"] for e in evs),
                     "policy_gate_s": {k: C.describe(pg)[k] for k in ("n", "median", "p95")},
                     "policy_gate_src_ms": {k: C.describe(pgs)[k] for k in ("n", "median", "p95")},
                     "safety_gate_s": {k: C.describe(sg)[k] for k in ("n", "median", "p95")}}
    injected = {"speech": 0, "chat": 0, "skill_request": 0}
    for _, p in pubs:
        if ENTRY.get(p["kind"]):
            injected[ENTRY[p["kind"]]] += 1
    lo = min((p["t"] for _, p in pubs), default=0) - 0.1
    hi = max((p["t"] for _, p in pubs), default=0) + 60
    src = trace_file or [D(r) for r in recs if r["topic"] == "/brain/trace"]
    ids = {t.get("decision_id") for t in src if t.get("decision_id") and lo <= (t.get("ts") or 0) <= hi}
    completeness = {}
    for k, n in injected.items():
        found = sum(1 for d in ids if d.startswith(k + "-"))
        completeness[k] = {"injected": n, "logged": min(found, n), "unattributed": max(0, found - n),
                           "rate": (min(found, n) / n) if n else None}
    ok = not inj_problem and not topic_problem
    summary = {"run": run.name, "n_cases": len(inj), "n_events": len(events), "categories": cats if ok else {},
               "out_of_policy_dispatch": sum(c["out_of_policy_dispatch"] for c in cats.values()) if ok else None,
               "false_block": sum(c["false_block"] for c in cats.values()) if ok else None,
               "webrtc_api_ids": sorted({a for e in events for a in e["api_ids"]}),
               "trace_completeness": completeness if ok and not trace_problem else None, "events": events}
    C.finish(out_dir, summary, problems)


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(*sys.argv[1:])
