#!/usr/bin/env python3
"""E3 端到端分段延遲（協定 §1.3 端到端）。
用法：python3 -I analyze_e3.py <run_dir> <out_dir> [--mode formal|acceptance]
- formal（預設，協定 §1.4）：第 1 遍前 3 句為暖機，丟棄；acceptance：保留全部（H12／H15 驗收用）。
- t0 為 Mac 時鐘（play.jsonl），以 meta.clock_offset_s 換成 Jetson 時鐘；t1–t8 為 Jetson 上 topic_logger 的到達時間。
- 政策閘 t3−t2 依 §1.3 用到達時間；兩則訊息自帶 created_at 的差另列 policy_gate_src_ms（次要）。
- 輸入缺失或損壞 → valid=false、相關指標為 null、結束碼 1。
"""
import argparse
import importlib.util
import re
from pathlib import Path

_s = importlib.util.spec_from_file_location("_common", Path(__file__).with_name("_common.py"))
C = importlib.util.module_from_spec(_s)
_s.loader.exec_module(C)

SEGMENTS = ["t1-t0", "a", "t2-t1", "t3-t2", "t4-t3", "t6-t5", "t9-t0"]
WARMUP = 3


def D(r):
    return r.get("data") if isinstance(r.get("data"), dict) else {}


def first(recs, topic, after, bound, pred=lambda d: True):
    for r in recs:
        if r["topic"] == topic and after <= r["t_recv"] <= bound and pred(D(r)):
            return r
    return None


def per_utterance(p, recs, offset, cached, played, bound):
    t0 = p["t_send"] + offset
    reply = p.get("reply") or {}
    asr = reply.get("asr")
    row = {"pass": p.get("pass"), "id": p.get("id"), "t0": t0,
           "a": (reply.get("latency_ms") or 0) / 1000 if reply.get("published") else None}
    sp = first(recs, "/event/speech_intent_recognized", t0 - 0.5, bound, lambda d: d.get("text") == asr if asr else True)
    if not sp:
        return row
    sid = D(sp).get("session_id")
    row.update(t1=sp["t_recv"], session_id=sid)
    cand = first(recs, "/brain/chat_candidate", sp["t_recv"], bound, lambda d: d.get("session_id") == sid)
    if cand:
        row.update(t2=cand["t_recv"], t2_src=D(cand).get("created_at"))
    prop = first(recs, "/brain/proposal", sp["t_recv"], bound, lambda d: d.get("session_id") == sid)
    if prop:
        row.update(t3=prop["t_recv"], t3_src=D(prop).get("created_at"), plan_id=D(prop).get("plan_id"),
                   reason=D(prop).get("reason"), skill=D(prop).get("selected_skill"))
        acc = first(recs, "/brain/skill_result", prop["t_recv"], bound,
                    lambda d: d.get("plan_id") == row["plan_id"] and str(d.get("status", "")).upper() == "ACCEPTED")
        if acc:
            row["t4"] = acc["t_recv"]
        tts = first(recs, "/tts", prop["t_recv"], bound)
        if tts:
            row["t5"] = tts["t_recv"]
            row["t6"] = next((t for t, _ in cached if row["t5"] <= t <= bound), None)
            row["t7"] = next((t for t, _ in played if row["t5"] <= t <= bound), None)   # 只配本句，缺了保留 null
    # t8（技能句，§1.3）：本句時間窗內第一個 motion STEP_STARTED 的 timestamp（＝/webrtc_req 發出）。
    # 只排除真正的 chat_reply plan；主 plan 本身就是技能時（例如直接意圖 stand）也要算。
    chat_plans = {D(r).get("plan_id") for r in recs if r["topic"] == "/brain/proposal"
                  and D(r).get("selected_skill") == "chat_reply" and sp["t_recv"] <= r["t_recv"] <= bound}
    mot = first(recs, "/brain/skill_result", sp["t_recv"], bound,
                lambda d: str(d.get("status", "")).upper() == "STEP_STARTED" and d.get("detail") == "motion"
                and d.get("plan_id") not in chat_plans)
    if mot:
        row.update(t8=D(mot).get("timestamp") or mot["t_recv"], t8_plan_id=D(mot).get("plan_id"),
                   t8_skill=D(mot).get("selected_skill"))
    return row


def stat(xs):
    d = C.describe(xs)
    return {"median": d["median"], "p95": d["p95"], "n": d["n"]}


def main(run_dir, out_dir, mode="formal"):
    run = Path(run_dir)
    meta = C.jload(run / "meta.json", {})
    pmeta = C.jload(run / "play_meta.json", {})
    offset = meta.get("clock_offset_s", pmeta.get("clock_offset_s"))
    plays, play_problem = C.jsonl_checked(run / "play.jsonl")
    topics, topic_problem = C.jsonl_checked(run / "topics.jsonl")
    problems = {"play.jsonl": play_problem, "topics.jsonl": topic_problem,
                "pane_tts.log": C.file_problem(run / "pane_tts.log"),
                "clock_offset_s": None if offset is not None else "missing（meta.json／play_meta.json）"}
    ok = not any(problems.values())
    recs = sorted((r for r in topics if "t_recv" in r), key=lambda r: r["t_recv"])
    cached = C.ros_log_events(run / "pane_tts.log", "💾 Cached")
    played = C.ros_log_events(run / "pane_tts.log", "Local playback completed")
    off = offset or 0.0
    rows = []
    for i, p in enumerate(plays):
        bound = plays[i + 1]["t_send"] + off if i + 1 < len(plays) else float("inf")
        rows.append(per_utterance(p, recs, off, cached, played, bound))
    # 首音：每句只在「本句送出 → 下一句送出」之間找；找不到保留缺值
    sends = [p["t_send"] for p in plays]
    onsets = C.mic_onsets(run / "mic.wav", pmeta.get("mic_t0"), sends, bounds=sends[1:] + [None])
    for row, p, on in zip(rows, plays, onsets):
        if on is not None:
            row["t9_mac"] = on
            row["t9-t0"] = on - p["t_send"]
    for k, row in enumerate(rows):
        row["warmup"] = row.get("pass") in (1, None) and k < WARMUP
    used = [r for r in rows if mode == "acceptance" or not r["warmup"]]
    # 覆蓋檢查：每句 gateway 回 published 的話，topic log 一定要有對應的 speech event（t1），否則 log 不完整
    pub_ids = {(p.get("pass"), p.get("id")) for p in plays if (p.get("reply") or {}).get("published")}
    no_t1 = [r["id"] for r in used if (r.get("pass"), r.get("id")) in pub_ids and r.get("t1") is None]
    problems["coverage"] = (f"{len(no_t1)} 句 published 但 topics.jsonl 沒有對應 speech event：{no_t1[:5]}" if no_t1 else None) \
        or (None if used else "可用句數 n=0")
    # 罐頭句率要靠每句的 /brain/proposal（reason）判定：有 speech event 卻沒有 proposal → 觀測不完整，不能當成「不是罐頭句」
    no_prop = [r["id"] for r in used if (r.get("pass"), r.get("id")) in pub_ids and r.get("t1") is not None
               and r.get("t3") is None]
    n_complete = sum(1 for r in used if r.get("t1") is not None and r.get("t3") is not None)
    problems["proposal_coverage"] = (f"{len(no_prop)} 句有 speech event 但缺 /brain/proposal：{no_prop[:5]}"
                                     if no_prop else None)
    ok = not any(problems.values())
    seg = {k: [] for k in SEGMENTS}
    src_gate = []
    pairs = {"t1-t0": ("t1", "t0"), "t2-t1": ("t2", "t1"), "t3-t2": ("t3", "t2"), "t4-t3": ("t4", "t3"), "t6-t5": ("t6", "t5")}
    for r in used:
        for k, (b, a) in pairs.items():
            if r.get(b) is not None and r.get(a) is not None:
                seg[k].append(r[b] - r[a])
        if r.get("t3_src") is not None and r.get("t2_src") is not None:
            src_gate.append((r["t3_src"] - r["t2_src"]) * 1000)
        if r.get("a") is not None:
            seg["a"].append(r["a"])
        if r.get("t9-t0") is not None:
            seg["t9-t0"].append(r["t9-t0"])
    upper = [r["t7"] - r["t1"] for r in used if r.get("t7") and r.get("t1")]
    first_audio = [r["t6"] - r["t1"] for r in used if r.get("t6") and r.get("t1")]
    motion = [r["t8"] - r["t1"] for r in used if r.get("t8") and r.get("t1")]
    canned = sum(1 for r in used if r.get("reason") == "chat_candidate_timeout")
    tts_log = (run / "pane_tts.log").read_text(errors="ignore") if (run / "pane_tts.log").exists() else ""
    lanes, served = {}, {}
    for m in re.finditer(r"\[tts\] lane=(\w+)", tts_log):
        lanes[m.group(1)] = lanes.get(m.group(1), 0) + 1
    for m in re.finditer(r"TTS completed \[(\w+)\] \((\w+)\)", tts_log):
        served[f"{m.group(1)}/{m.group(2)}"] = served.get(f"{m.group(1)}/{m.group(2)}", 0) + 1
    S = (lambda xs: stat(xs)) if ok else (lambda xs: stat([]))
    summary = {"run": meta.get("run", run.name), "mode": mode, "n": len(used), "n_total": len(rows),
               "warmup_dropped": len(rows) - len(used), "clock_offset_s": offset,
               "segments": {k: S(v) for k, v in seg.items()},
               "policy_gate_src_ms": S(src_gate), "end_to_end_upper_t7_t1": S(upper),
               "first_audio_approx_t6_t1": S(first_audio), "motion_t8_t1": S(motion),
               "canned_rate": (canned / len(used) if used else None) if ok else None,
               "observation": {"complete": ok, "n_used": len(used), "n_complete": n_complete,
                               "missing_speech_event": no_t1, "missing_proposal": no_prop},
               "tts_lanes": lanes, "tts_served_by": served,
               "complete": {k: sum(1 for r in used if r.get(k) is not None)
                            for k in ("t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8")},
               "rows": rows}
    C.finish(out_dir, summary, problems)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--mode", choices=["formal", "acceptance"], default="formal")
    a = ap.parse_args()
    main(a.run_dir, a.out_dir, a.mode)
