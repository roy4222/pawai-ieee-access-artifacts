#!/usr/bin/env python3
"""E4 故障注入（協定 §4）。用法：python3 -I analyze_e4.py <runs_dir> <out_dir> [--baseline <E3 summary.json>]
- 讀 <runs_dir>/E4-S1 … E4-S7（有幾個算幾個）與同層的 E4-Sx-stop.jsonl（每情境 1 句「停」）。
- 每句：t0（Mac 時鐘，play.jsonl）以 meta.clock_offset_s 換成 Jetson 時鐘；t1 speech event、t2 chat_candidate、
  t3 proposal、t5 /tts、t6 `💾 Cached [provider]` 或 `💾 Cache hit`（罐頭句重複時命中快取，另計、不進合成延遲）、
  TTS completed [provider]；llm_decision 取 conversation_trace。
- 停止句：proposal selected_skill=stop_move 且 skill_result accepted，/webrtc_req api_id 1003 的延遲。
- S4／S5 的 revert 會重開 demo:tts，pane_tts.log 只剩復原後的視窗；有 <run>-tts-fault.log（revert 前另存）就改讀它。
- 不丟暖機（每情境只有 5 句，且是故障下的行為觀察）。輸入缺失 → 該情境 valid=false。
"""
import argparse
import importlib.util
import json
import re
from pathlib import Path

_s = importlib.util.spec_from_file_location("_common", Path(__file__).with_name("_common.py"))
C = importlib.util.module_from_spec(_s)
_s.loader.exec_module(C)

SCEN = {"S1": "llm_primary_bad", "S2": "llm_both_bad", "S3": "cloud_timeout", "S4": "cloud_refused",
        "S5": "tts_cloud_refused", "S6": "asr_down", "S7": "offline_mode"}


def D(r):
    return r.get("data") if isinstance(r.get("data"), dict) else {}


def first(recs, topic, lo, hi, pred=lambda d: True):
    for r in recs:
        if r["topic"] == topic and lo <= r["t_recv"] <= hi and pred(D(r)):
            return r
    return None


def tts_events(path):
    """回傳 [(t, kind, provider)]：kind ∈ cached／completed／timeout。"""
    out = []
    if not path.exists():
        return out
    for line in path.read_text(errors="ignore").splitlines():
        m = re.search(r"\[(\d+\.\d+)\].*💾 Cached \[(\w+)\]", line)
        if m:
            out.append((float(m.group(1)), "cached", m.group(2)))
            continue
        m = re.search(r"\[(\d+\.\d+)\].*💾 Cache hit \[(\w+)\]", line)
        if m:
            out.append((float(m.group(1)), "cache_hit", m.group(2)))
            continue
        m = re.search(r"\[(\d+\.\d+)\].*TTS completed \[(\w+)\]", line)
        if m:
            out.append((float(m.group(1)), "completed", m.group(2)))
            continue
        m = re.search(r"\[(\d+\.\d+)\].*\[(\w+)\] returned no audio", line)
        if m:
            out.append((float(m.group(1)), "no_audio", m.group(2)))
    return out


def per_utt(p, recs, off, tts, hi):
    t0 = p["t_send"] + off
    rep = p.get("reply") or {}
    row = {"id": p.get("id"), "text": p.get("text"), "published": bool(rep.get("published")),
           "reply_error": rep.get("error"), "reply_s": (p["t_reply"] - p["t_send"]) if p.get("t_reply") else None}
    sp = first(recs, "/event/speech_intent_recognized", t0 - 0.5, hi)
    if not sp:
        return row
    sid = D(sp).get("session_id")
    row.update(t1=sp["t_recv"], asr=D(sp).get("text"))
    # 以 session_id 配對的訊息從 t0 − 0.5 s 起找：logger 各訂閱各自回呼，快路徑（offline_mode 約 4 ms）的
    # proposal 可能比 speech event 先到。
    lo = t0 - 0.5
    cand = first(recs, "/brain/chat_candidate", lo, hi, lambda d: d.get("session_id") == sid)
    if cand:
        row.update(t2=cand["t_recv"], cand_reason=D(cand).get("proposal_reason"),
                   proposed_skill=D(cand).get("proposed_skill"))
    dec = first(recs, "/brain/conversation_trace", lo, hi,
                lambda d: d.get("session_id") == sid and d.get("stage") == "llm_decision")
    if dec:
        row.update(llm_status=D(dec).get("status"), llm_detail=D(dec).get("detail"))
    prop = first(recs, "/brain/proposal", lo, hi, lambda d: d.get("session_id") == sid)
    if prop:
        row.update(t3=prop["t_recv"], reason=D(prop).get("reason"), skill=D(prop).get("selected_skill"))
        tt = first(recs, "/tts", prop["t_recv"], hi)
        if tt:
            row["t5"] = tt["t_recv"]
            # 快取命中在 tts_node 內只要約 1 ms，log 時間可能早於 logger 收到 /tts，所以從 proposal 起找
            win = [e for e in tts if prop["t_recv"] - 0.05 <= e[0] <= hi]
            c = next((e for e in win if e[1] in ("cached", "cache_hit")), None)
            done = next((e for e in win if e[1] == "completed"), None)
            row.update(t6=c[0] if c else None, tts_cache_hit=bool(c and c[1] == "cache_hit"),
                       tts_provider=done[2] if done else None,
                       tts_no_audio=[e[2] for e in win if e[1] == "no_audio" and (not done or e[0] <= done[0])])
    return row


def stop_check(run_dir, recs, off):
    path = run_dir.parent / f"{run_dir.name}-stop.jsonl"
    stops = list(C.jsonl(path)) if path.exists() else []
    if not stops:
        return {"sent": False}
    s = stops[-1]
    t0 = s["t_send"] + off
    prop = first(recs, "/brain/proposal", t0 - 0.5, t0 + 10, lambda d: d.get("selected_skill") == "stop_move")
    out = {"sent": True, "proposal": bool(prop)}
    if prop:
        pid = D(prop).get("plan_id")
        acc = first(recs, "/brain/skill_result", prop["t_recv"], t0 + 10,
                    lambda d: d.get("plan_id") == pid and str(d.get("status", "")).lower() == "accepted")
        req = first(recs, "/webrtc_req", prop["t_recv"], t0 + 10, lambda d: d.get("api_id") == 1003)
        out.update(source=D(prop).get("source"), accepted=bool(acc), webrtc_1003=bool(req),
                   send_to_webrtc_s=(req["t_recv"] - t0) if req else None)
    return out


def scenario(run_dir):
    meta = C.jload(run_dir / "meta.json", {})
    off = meta.get("clock_offset_s")
    plays, pp = C.jsonl_checked(run_dir / "play.jsonl")
    topics, tp = C.jsonl_checked(run_dir / "topics.jsonl")
    problems = {"play.jsonl": pp, "topics.jsonl": tp, "clock_offset_s": None if off is not None else "missing"}
    off = off or 0.0
    recs = sorted((r for r in topics if "t_recv" in r), key=lambda r: r["t_recv"])
    # revert 會重開 demo:tts 視窗（S4／S5），故障期間的 pane 只能靠 revert 前另存的 <run>-tts-fault.log
    fault_log = run_dir.parent / f"{run_dir.name}-tts-fault.log"
    tts = tts_events(fault_log if fault_log.exists() else run_dir / "pane_tts.log")
    stop_path = run_dir.parent / f"{run_dir.name}-stop.jsonl"
    stop_t = [x["t_send"] for x in C.jsonl(stop_path)] if stop_path.exists() else []
    rows = []
    for i, p in enumerate(plays):
        # 最後一句的時間窗截在「停」送出前，避免配到停止句的 speech event
        end = min([p["t_send"] + 60] + [t for t in stop_t if t > p["t_send"]])
        hi = plays[i + 1]["t_send"] + off if i + 1 < len(plays) else end + off
        rows.append(per_utt(p, recs, off, tts, hi))
    tally = lambda key: {k: sum(1 for r in rows if str(r.get(key)) == k) for k in sorted({str(r.get(key)) for r in rows})}
    cand_lat = [r["t2"] - r["t1"] for r in rows if r.get("t2") and r.get("t1")]
    first_audio = [r["t6"] - r["t1"] for r in rows if r.get("t6") and r.get("t1")]
    tts_lat = [r["t6"] - r["t5"] for r in rows if r.get("t6") and r.get("t5") and not r.get("tts_cache_hit")]
    reply_s = [r["reply_s"] for r in rows if r.get("reply_s") is not None]
    return {"run": run_dir.name, "tts_log": fault_log.name if fault_log.exists() else "pane_tts.log",
            "valid": not any(problems.values()), "input_problems": {k: v for k, v in problems.items() if v},
            "n": len(rows), "speech_events": sum(1 for r in rows if r.get("t1")),
            "spoken": sum(1 for r in rows if r.get("tts_provider")),
            "llm_decision": tally("llm_status"), "llm_model": tally("llm_detail"),
            "candidate_reason": tally("cand_reason"), "proposal_reason": tally("reason"),
            "tts_provider": tally("tts_provider"),
            "tts_cache_hits": sum(1 for r in rows if r.get("tts_cache_hit")),
            "proposed_skill_nonnull": sum(1 for r in rows if r.get("proposed_skill")),
            "candidate_latency_s": C.describe(cand_lat), "tts_synth_s": C.describe(tts_lat),
            "first_audio_approx_s": C.describe(first_audio), "gateway_reply_s": C.describe(reply_s),
            "reply_errors": tally("reply_error"),
            "stop": stop_check(run_dir, recs, off), "rows": rows}


def main(runs_dir, out_dir, baseline=None, use=""):
    runs = Path(runs_dir)
    override = dict(x.split("=", 1) for x in use.split(",") if x)
    out = {"scenarios": {}, "run_override": override}
    for k, name in SCEN.items():
        d = runs / override.get(k, f"E4-{k}")
        if d.exists():
            out["scenarios"][k] = {"fault": name, **scenario(d)}
    if baseline and Path(baseline).exists():
        b = json.load(open(baseline))
        out["baseline"] = {"source": str(baseline), "candidate_latency_median_s": b["segments"]["t2-t1"]["median"],
                           "tts_synth_median_s": b["segments"]["t6-t5"]["median"]}
    C.write_summary(out_dir, C.rounded(out))
    return 0 if all(s["valid"] for s in out["scenarios"].values()) and out["scenarios"] else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--baseline")
    ap.add_argument("--use", default="", help="情境改用其他 run，例如 S4=E4-S4-r2,S5=E4-S5-r2")
    a = ap.parse_args()
    raise SystemExit(main(a.runs_dir, a.out_dir, a.baseline, a.use))
