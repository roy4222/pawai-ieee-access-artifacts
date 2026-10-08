#!/usr/bin/env python3
"""E5 仲裁注入（設計 §4.3）：每案 world 旗標 → speech intent → chat_candidate／skill_request → followup → 節奏閘。
用法：
  python3 inject_arbitration.py --cases e5_cases.json --validate-only           # Mac 也能跑，不需 ROS
  python3 inject_arbitration.py --cases e5_cases.json --only C2 --limit 2 --out inject.jsonl [--confirm-each]
結束碼：0 正常；1 案例檔不合法；4 watchdog（出現原地集合以外的 /webrtc_req 或任何 /cmd_vel）。
"""
import argparse
import json
import sys
import time
import uuid
from collections import Counter

WHITELIST = ["show_status", "wave_hello", "stand", "sit_along", "careful_remind", "wiggle", "stretch",
             "self_introduce", "greet_known_person", "fly"]
INPLACE_API = {1002, 1003, 1004, 1005, 1016, 1017, 1020}
# 前進與召喚子字串（協定 §1.5、F2）：任何送出的語音文字都不得含有
FORWARD = ["往前走", "往前移動", "往前一點", "前進", "走一點", "過來一點", "往前",
           "過來", "來這裡", "來我這", "靠近", "跟我來", "這邊", "來一下"]
WORLD_TOPICS = {"/state/reactive_stop/status", "/capability/depth_clear"}
CATEGORIES = [f"C{i}" for i in range(1, 10)]
ALLOWED_SPEECH_INTENTS = {"chat", "stop", "status"}   # 只送語音的案（C6）可用的 intent；come_here 等一律拒絕
TERMINAL = {"COMPLETED", "BLOCKED_BY_SAFETY", "STEP_FAILED"}


def validate(cases, whitelist):
    errs = []
    ids = Counter(c.get("id") for c in cases)
    errs += [f"{i}: id 重複" for i, n in ids.items() if n > 1]
    for c in cases:
        cid = c.get("id", "?")
        if c.get("category") not in CATEGORIES:
            errs.append(f"{cid}: category 不合法 {c.get('category')!r}")
        sr = c.get("skill_request")
        if sr is not None:
            sk = sr.get("skill") if isinstance(sr, dict) else None
            if sk not in whitelist:
                errs.append(f"{cid}: skill_request 的 {sk!r} 不在白名單 {whitelist}")
        for key in ("speech", "followup_speech"):
            s = c.get(key)
            if s is None:
                continue
            text = s.get("text", "") if isinstance(s, dict) else ""
            if not text:
                errs.append(f"{cid}: {key}.text 為空")
            hit = [w for w in FORWARD if w in text]
            if hit:
                errs.append(f"{cid}: {key} 含前進／召喚字 {hit}")
        sp_intent = (c.get("speech") or {}).get("intent")
        if c.get("candidate") is not None and sp_intent not in (None, "chat", "status"):
            errs.append(f"{cid}: 候選注入案的 speech.intent 必須是 chat（收到 {sp_intent!r}；技能只能來自候選）")
        if c.get("candidate") is None and c.get("speech") is not None and sp_intent not in ALLOWED_SPEECH_INTENTS:
            errs.append(f"{cid}: speech.intent {sp_intent!r} 不允許（只允許 {sorted(ALLOWED_SPEECH_INTENTS)}）")
        fu = (c.get("followup_speech") or {}).get("intent")
        if fu not in (None, "chat", "stop"):
            errs.append(f"{cid}: followup_speech.intent {fu!r} 不允許")
        cand = c.get("candidate")
        if cand is not None:
            if c.get("speech") is None:
                errs.append(f"{cid}: 有 candidate 但沒有 speech（brain_node 會丟掉）")
            if not isinstance(cand.get("proposed_skill", None), (str, type(None))):
                errs.append(f"{cid}: candidate.proposed_skill 必須是字串或 null")
        for w in (c.get("world"), c.get("world_reset")):
            if w is not None and w.get("topic") not in WORLD_TOPICS:
                errs.append(f"{cid}: world topic {w.get('topic')!r} 不允許（只允許 {sorted(WORLD_TOPICS)}）")
        if c.get("speech") is None and sr is None:
            errs.append(f"{cid}: 沒有 speech 也沒有 skill_request")
        if not isinstance(c.get("needs_operator"), bool):
            errs.append(f"{cid}: needs_operator 必須是 bool")
    return errs


def speech_payload(text, intent, session_id):
    now = time.time()
    return {"stamp": now, "event_type": "intent_recognized", "intent": intent or "chat", "text": text,
            "confidence": 0.9, "provider": "text_input", "source": "web_bridge", "session_id": session_id,
            "matched_keywords": [], "latency_ms": 0.0, "degraded": False,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))}


def candidate_payload(cand, session_id, intent):
    return {"session_id": session_id, "reply_text": cand.get("reply_text", ""), "intent": intent or "chat",
            "selected_skill": cand.get("selected_skill"), "confidence": 0.9,
            "proposed_skill": cand.get("proposed_skill"), "proposed_args": cand.get("proposed_args") or {},
            "proposal_reason": cand.get("proposal_reason", "pawexp_inject"), "engine": "langgraph",
            "source": "pawai_brain", "input_origin": None, "created_at": time.time()}


def dry_run(cases):
    """不需 ROS：印出每案依序會發的 topic 與 JSON（驗證序列化）。"""
    for c in cases:
        sid = "dry00000"
        steps = []
        if c.get("world"):
            steps.append((c["world"]["topic"], c["world"]["json"]))
        if c.get("speech"):
            steps.append(("/event/speech_intent_recognized", speech_payload(c["speech"]["text"], c["speech"].get("intent"), sid)))
        if c.get("candidate"):
            steps.append(("/brain/chat_candidate", candidate_payload(c["candidate"], sid, (c.get("speech") or {}).get("intent"))))
        if c.get("skill_request"):
            steps.append(("/brain/skill_request", {"skill": c["skill_request"]["skill"], "args": c["skill_request"].get("args") or {},
                                                   "request_id": f"pawexp-{c['id']}", "source": "studio_button"}))
        if c.get("followup_speech"):
            steps.append(("/event/speech_intent_recognized", speech_payload(c["followup_speech"]["text"], None, "dry00001")))
        if c.get("world_reset"):
            steps.append((c["world_reset"]["topic"], c["world_reset"]["json"]))
            if c.get("reset_confirm"):
                steps.append(("/event/speech_intent_recognized", speech_payload("你現在狀態還好嗎", "status", "dry00002")))
                steps.append(("/brain/chat_candidate", candidate_payload({"reply_text": "", "proposed_skill": "show_status"},
                                                                         "dry00002", "status")))
        print(f"# {c['id']} expect={json.dumps(c.get('expect'), ensure_ascii=False)}")
        for topic, obj in steps:
            print(f"  {topic} {json.dumps(obj, ensure_ascii=False)}")
    return 0


def run(cases, out_path, confirm_each, gate_timeout, discovery_timeout=30.0):
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import Bool, String
    from rosidl_runtime_py.utilities import get_message

    rel = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE)
    be = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE)
    latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)

    rclpy.init()
    node = Node("pawexp_inject")
    state = {"results": [], "tts": None, "violation": None}

    def close(code):
        node.destroy_node()
        rclpy.try_shutdown()
        return code

    def spin(sec):
        end = time.time() + sec
        while time.time() < end:
            rclpy.spin_once(node, timeout_sec=0.05)
            if state["violation"]:
                print(f"[inject] WATCHDOG {state['violation']} → 中止", flush=True)
                sys.exit(close(4))

    # ── 安全訂閱閘：/webrtc_req 有型別並訂好之前，不建立任何 publisher、不送任何訊息 ──
    node.create_subscription(String, "/brain/skill_result",
                             lambda m: state["results"].append((time.time(), _loads(m.data))), rel)
    node.create_subscription(Bool, "/state/tts_playing", lambda m: state.__setitem__("tts", (time.time(), m.data)), be)
    node.create_subscription(get_message("geometry_msgs/msg/Twist"), "/cmd_vel",
                             lambda m: state.__setitem__("violation", "/cmd_vel message"), be)

    def on_req(m):
        api = getattr(m, "api_id", None)
        if api not in INPLACE_API:
            state["violation"] = f"/webrtc_req api_id={api}"

    t0 = time.time()
    while True:  # DDS discovery 持續重試
        types = dict(node.get_topic_names_and_types())
        if "/webrtc_req" in types:
            node.create_subscription(get_message(types["/webrtc_req"][0]), "/webrtc_req", on_req, be)
            break
        if time.time() - t0 > discovery_timeout:
            print(f"[inject] FAIL {discovery_timeout:.0f} s 內找不到 /webrtc_req 型別，安全監控無法建立；未送出任何訊息", flush=True)
            return close(5)
        spin(0.5)
    spin(1.0)  # 訂閱建立後再等一輪 discovery
    print(f"[inject] 安全訂閱完成（{time.time() - t0:.1f} s）：/webrtc_req /cmd_vel /brain/skill_result /state/tts_playing", flush=True)

    pubs = {"/event/speech_intent_recognized": node.create_publisher(String, "/event/speech_intent_recognized", rel),
            "/brain/chat_candidate": node.create_publisher(String, "/brain/chat_candidate", rel),
            "/brain/skill_request": node.create_publisher(String, "/brain/skill_request", rel),
            "/state/reactive_stop/status": node.create_publisher(String, "/state/reactive_stop/status", be)}
    depth_pub = None
    spin(0.5)
    rec = None

    def pub(topic, obj, kind, session_id=None):
        msg = String()
        msg.data = json.dumps(obj, ensure_ascii=False)
        pubs[topic].publish(msg)
        t = time.time()
        rec["publishes"].append({"kind": kind, "topic": topic, "t": t, "session_id": session_id,
                                 "skill": obj.get("proposed_skill", obj.get("skill")) if isinstance(obj, dict) else None})
        return t

    def pub_world(w, kind):
        nonlocal depth_pub
        if w["topic"] == "/capability/depth_clear":
            if depth_pub is None:
                depth_pub = node.create_publisher(Bool, "/capability/depth_clear", latched)
            depth_pub.publish(Bool(data=bool(w["json"])))
            t = time.time()
            rec["publishes"].append({"kind": kind, "topic": w["topic"], "t": t, "session_id": None, "skill": None})
            return t
        return pub(w["topic"], w["json"], kind)

    def speech(text, intent, kind):
        sid = uuid.uuid4().hex[:8]
        return sid, pub("/event/speech_intent_recognized", speech_payload(text, intent, sid), kind, sid)

    def gate(t_from, expect_result, cooldown):
        """終態 skill_result（若預期有）＋tts_playing false＋cooldown；逾時記 gap_timeout。"""
        t0 = time.time()
        timed_out = False
        while True:
            spin(0.2)
            done = (not expect_result) or any(t > t_from and str(r.get("status", "")).upper() in TERMINAL for t, r in state["results"])
            tts_ok = state["tts"] is None or state["tts"][1] is False
            if done and tts_ok:
                break
            if time.time() - t0 > gate_timeout:
                timed_out = True
                break
        spin(cooldown)
        return {"waited_s": round(time.time() - t0, 3), "gap_timeout": timed_out}

    def read_confirm():
        """等 Enter 期間持續 spin（watchdog 照常運作）；stdin 不能 select 時（測試替身）退回直接 readline。"""
        import select
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            return sys.stdin.readline()
        while True:
            spin(0.2)
            ready, _, _ = select.select([fd], [], [], 0)
            if ready:
                return sys.stdin.readline()

    out = open(out_path, "a", encoding="utf-8")
    code = 0
    for c in cases:
        if confirm_each:
            print(f"\n下一案 {c['id']}（{c['category']}）：{c.get('notes', '')}\n  預期：{json.dumps(c.get('expect'), ensure_ascii=False)}"
                  "\n  按 Enter 注入（輸入 q 結束）", flush=True)
            line = read_confirm()
            spin(0.2)  # 派送前再處理一次佇列中的訊息：等待期間若有違規，spin 會以 4 中止
            if line == "":  # EOF：沒有人確認，絕不派送
                print("[inject] stdin EOF，未確認，中止", flush=True)
                code = 3
                break
            ans = line.strip().lower()
            if ans == "q":
                break
            if ans not in ("", "y"):
                print(f"[inject] 輸入 {ans!r} 不是確認，中止", flush=True)
                code = 3
                break
        rec = {"id": c["id"], "category": c["category"], "t_world": None, "t_speech": None, "t_candidate": None,
               "t_skill_request": None, "t_followup": None, "t_repeat": None, "session_id": None, "publishes": []}
        t_start = time.time()
        if c.get("world"):
            rec["t_world"] = pub_world(c["world"], "world")
            spin(0.5)
        sid = None
        intent = (c.get("speech") or {}).get("intent")
        if c.get("speech"):
            sid, rec["t_speech"] = speech(c["speech"]["text"], intent, "speech")
            rec["session_id"] = sid
            spin(0.3)
        if c.get("candidate") and sid:
            rec["t_candidate"] = pub("/brain/chat_candidate", candidate_payload(c["candidate"], sid, intent), "candidate", sid)
        if c.get("skill_request"):
            sr = c["skill_request"]
            rec["t_skill_request"] = pub("/brain/skill_request", {
                "skill": sr["skill"], "args": sr.get("args") or {}, "request_id": f"pawexp-{c['id']}",
                "source": "studio_button", "created_at": time.time()}, "skill_request")
        if c.get("repeat_after_s") is not None:
            spin(float(c["repeat_after_s"]))
            if c.get("skill_request"):
                rec["t_repeat"] = pub("/brain/skill_request", {
                    "skill": c["skill_request"]["skill"], "args": {}, "request_id": f"pawexp-{c['id']}-2",
                    "source": "studio_button", "created_at": time.time()}, "repeat_skill_request")
            elif c.get("speech"):
                sid2, rec["t_repeat"] = speech(c["speech"]["text"], intent, "repeat_speech")
                rec["session_id_repeat"] = sid2
                spin(0.3)
                if c.get("candidate"):
                    pub("/brain/chat_candidate", candidate_payload(c["candidate"], sid2, intent), "repeat_candidate", sid2)
        if c.get("followup_speech"):
            f = c["followup_speech"]
            spin(float(f.get("delay_s", 1.0)))
            sid3, rec["t_followup"] = speech(f["text"], f.get("intent"), "followup_speech")
            rec["session_id_followup"] = sid3
        expect_result = (c.get("expect") or {}).get("skill_result") is not None
        rec["gate"] = gate(t_start, expect_result, float(c.get("wait_cooldown_s", 1.5)))
        if c.get("world_reset"):
            rec["t_world_reset"] = pub_world(c["world_reset"], "world_reset")
            spin(0.5)
            if c.get("reset_confirm"):  # 重置後用 show_status 確認仲裁恢復（預期 ACCEPTED）
                sid4, rec["t_reset_confirm"] = speech("你現在狀態還好嗎", "status", "reset_confirm_speech")
                spin(0.3)
                pub("/brain/chat_candidate", candidate_payload({"reply_text": "", "proposed_skill": "show_status"}, sid4, "status"),
                    "reset_confirm_candidate", sid4)
                rec["session_id_reset_confirm"] = sid4
                rec["gate_reset_confirm"] = gate(rec["t_reset_confirm"], True, 6.0)
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        print(f"[inject] {c['id']} done gate={rec['gate']}", flush=True)
    out.close()
    spin(1.0)
    return close(code)


def _loads(s):
    try:
        return json.loads(s)
    except ValueError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--only", default="", help="逗號分隔的類別（C2）或案例 id")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--confirm-each", action="store_true")
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="不連 ROS，只印每案會送的訊息")
    ap.add_argument("--whitelist", default=",".join(WHITELIST))
    ap.add_argument("--gate-timeout", type=float, default=40.0)
    ap.add_argument("--out", default="inject.jsonl")
    a = ap.parse_args()
    data = json.load(open(a.cases, encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    errs = validate(cases, [w for w in a.whitelist.split(",") if w])
    if errs:
        for e in errs:
            print("✗", e)
        print(f"FAIL：{len(errs)} 個問題，未送出任何訊息")
        return 1
    counts = Counter(c["category"] for c in cases)
    print("  ".join(f"{k}={counts.get(k, 0)}" for k in CATEGORIES) + f"  total={len(cases)}")
    if a.validate_only:
        print("OK（validate-only）")
        return 0
    only = [x for x in a.only.split(",") if x]
    sel = [c for c in cases if not only or c["category"] in only or c["id"] in only]
    if a.limit is not None:
        sel = sel[:a.limit]
    print(f"注入 {len(sel)} 案：{', '.join(c['id'] for c in sel)}", flush=True)
    if a.dry_run:
        return dry_run(sel)
    return run(sel, a.out, a.confirm_each, a.gate_timeout)


if __name__ == "__main__":
    sys.exit(main())
