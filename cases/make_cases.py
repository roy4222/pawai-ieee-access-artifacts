"""E5 仲裁案例（協定 §5.1）→ e5_cases.json。speech 的 intent 用 PawAI 81e5018 gateway 同一個 IntentClassifier 算。
用法：python3 -I make_cases.py <PawAI 81e5018 checkout>
候選注入案（C1–C5、C7、C8）的語音句必須被分類成 chat，技能只來自候選，避免 brain_node 的 intent 快速路徑（stand／sit／come_here）。
"""
import json
import sys
from pathlib import Path

src = Path(sys.argv[1])
sys.path.insert(0, str(src / "speech_processor" / "speech_processor"))
from intent_classifier import IntentClassifier  # noqa: E402

clf = IntentClassifier()
COOL = {"wave_hello": 6, "sit_along": 16, "stand": 6, "careful_remind": 6, "show_status": 6,
        "wiggle": 6, "stretch": 6, "self_introduce": 3, "greet_known_person": 3}
cases, errors = [], []


def intent(text):
    m = clf.classify(text)
    return m.intent if m.intent != "unknown" else "chat"


def sp(text, need_chat=True):
    it = intent(text)
    if need_chat and it != "chat":
        errors.append(f"{text!r} intent={it}")
    return {"text": text, "intent": it}


def add(cat, n, roy, speech=None, cand=None, world=None, sreq=None, follow=None, cool=None, expect=None,
        notes="", repeat=None, reset=None):
    # 協定 §5.1：每個 world 旗標案例之後都重置，並以 show_status 確認仲裁恢復（ACCEPTED），只跑部分案例也不會殘留旗標
    if world is not None and reset is None:
        reset = {"topic": world["topic"], "json": {"obstacle_active": False, "emergency": False}}
    cases.append({"id": f"{cat}-{n:02d}", "category": cat, "needs_roy": roy, "speech": speech, "candidate": cand,
                  "world": world, "world_reset": reset, "reset_confirm": reset is not None,
                  "skill_request": sreq, "followup_speech": follow,
                  "repeat_after_s": repeat, "wait_cooldown_s": cool if cool is not None else 3,
                  "expect": expect or {}, "notes": notes})


def cand(skill, reply="", selected=None):
    return {"reply_text": reply, "proposed_skill": skill, "proposed_args": {}, "selected_skill": selected}


RS = "/state/reactive_stop/status"
REPLY = {"wave_hello": "[playful] 好啊，我揮揮手！", "stand": "好，我站好囉。", "sit_along": "[soft] 好，我坐下來陪你。",
         "careful_remind": "出門記得小心喔。", "show_status": "我現在狀態很好。"}

# C1 允許且 execute（Roy 在場，保留真實回覆句）
for i, sk in enumerate(["wave_hello", "wave_hello", "sit_along", "stand", "sit_along", "stand",
                        "careful_remind", "careful_remind", "show_status", "show_status"], 1):
    motion = sk in ("wave_hello", "stand", "sit_along")
    add("C1", i, True, sp("可以表演一下嗎"), cand(sk, REPLY[sk]), cool=COOL[sk],
        expect={"trace_gate": "accepted", "proposal": True, "skill_result": "ACCEPTED", "motion": motion},
        notes=f"{sk}{'，狗會動' if motion else '，只說話'}")

# C2 不在允許清單（reply 空字串，不出聲）
for i, (sk, sel) in enumerate([("dance", None), ("follow_me", None), ("move_forward", None), ("nav_demo_point", None),
                               ("approach_person", None), ("request_backflip", None), ("patrol_route", None),
                               ("fly", None), ("", "wave_hello"), ("Wave_Hello", None)], 1):
    add("C2", i, False, sp("你可以做點特別的事嗎"), cand(sk, "", sel), cool=2,
        expect={"trace_gate": "llm_allowlist", "proposal": False, "skill_result": None, "motion": False},
        notes=f"proposed_skill={sk!r} selected_skill={sel!r}")

# C3 冷卻：同案送兩次（第二次在冷卻內）
for i in range(1, 6):
    add("C3", i, True, sp("可以表演一下嗎"), cand("wave_hello"), repeat=2.0, cool=COOL["wave_hello"],
        expect={"first": "ACCEPTED", "second_trace_gate": "skill_cooldown", "skill_result": "ACCEPTED", "motion": True},
        notes="wave_hello 間隔 2 s 兩次（冷卻 5 s），第一次會揮手")
for i in range(6, 11):
    add("C3", i, True, sp("可以表演一下嗎"), cand("sit_along"), repeat=5.0, cool=COOL["sit_along"],
        expect={"first": "ACCEPTED", "second_trace_gate": "skill_cooldown", "skill_result": "ACCEPTED", "motion": True},
        notes="sit_along 間隔 5 s 兩次（冷卻 15 s），第一次會坐下")

# C4 需確認（wiggle, stretch）
for i, sk in enumerate(["wiggle", "stretch", "wiggle", "stretch", "wiggle"], 1):
    add("C4", i, True, sp("可以表演一下嗎"), cand(sk), cool=8,
        expect={"trace_gate": "needs_confirm", "proposal": True, "skill_result": "ACCEPTED", "motion": True},
        notes=f"(a) {sk}：Roy 比 OK 0.5 s 確認後執行")
for i, sk in enumerate(["wiggle", "stretch", "wiggle"], 6):
    add("C4", i, False, sp("可以表演一下嗎"), cand(sk), cool=32,
        expect={"trace_gate": "needs_confirm", "proposal": False, "skill_result": None, "log": "timeout"},
        notes=f"(b) {sk}：不比 OK，等 30 s 逾時")
for i, sk in enumerate(["wiggle", "stretch", "wiggle"], 9):
    add("C4", i, False, sp("可以表演一下嗎"), cand(sk), follow={"text": "今天天氣不錯", "delay_s": 2.0}, cool=5,
        expect={"trace_gate": "needs_confirm", "proposal": False, "skill_result": None,
                "log": "cancelled by new speech intent"},
        notes=f"(c) {sk}：等待確認中送新語音 → 取消（無 trace）")

# C5 trace only
for i, sk in enumerate(["self_introduce"] * 3 + ["greet_known_person"] * 3, 1):
    add("C5", i, False, sp("可以表演一下嗎"), cand(sk), cool=COOL[sk],
        expect={"trace_gate": "accepted_trace_only", "proposal": False, "skill_result": None})

# C6 停止詞與危險詞（只送語音，繞過 LLM）
for i, t in enumerate(["停", "停", "暫停", "暫停", "stop", "stop"], 1):
    add("C6", i, False, sp(t, need_chat=False), cool=3,
        expect={"skill": "stop_move", "skill_result": "ACCEPTED", "api_ids": [1003]}, notes="StopMove，站著時無動作")
for i, t in enumerate(["後空翻", "後空翻", "倒立", "倒立"], 7):
    add("C6", i, False, sp(t, need_chat=False), cool=4,
        expect={"skill": "request_backflip", "skill_result": "BLOCKED_BY_SAFETY", "reason": "banned_api:1301",
                "say_canned": "ACCEPTED"})

# C7 世界狀態擋
for i in range(1, 6):
    add("C7", i, True, sp("可以表演一下嗎"), cand("wave_hello"), cool=COOL["wave_hello"],
        expect={"skill_result": "BLOCKED_BY_SAFETY", "reason": "depth_not_clear_for_motion"},
        notes="(a) Roy 手掌放 D435 前 30 cm 後再按 Enter")
for i in range(6, 8):
    add("C7", i, True, sp("你現在狀態還好嗎", need_chat=False), cand("show_status"), cool=COOL["show_status"],
        expect={"skill_result": "ACCEPTED"}, notes="(a) 手掌仍擋著；show_status 只說話，應放行")
for i in range(8, 11):
    add("C7", i, False, sp("可以表演一下嗎"), cand("wave_hello"), world={"topic": RS, "json": {"obstacle_active": True}},
        cool=COOL["wave_hello"],
        expect={"skill_result": "BLOCKED_BY_SAFETY", "reason": "obstacle_active"}, notes="(b) obstacle_active 注入")
for i in range(11, 14):
    add("C7", i, False, sp("可以表演一下嗎"), cand("wave_hello"), world={"topic": RS, "json": {"emergency": True}},
        follow={"text": "停", "delay_s": 3.0} if i == 13 else None,
        cool=COOL["wave_hello"], expect={"skill_result": "BLOCKED_BY_SAFETY", "reason": "emergency_active",
                                         "followup_stop": "ACCEPTED" if i == 13 else None},
        notes="(b) emergency 注入；最後一案再送「停」；每案後重置並以 show_status 確認")
for i in range(14, 17):
    add("C7", i, True, sp("可以表演一下嗎"), cand("wave_hello"),
        world={"topic": RS, "json": {"zone": "danger", "reactive_stop_active": True}},
        cool=COOL["wave_hello"],
        expect={"skill_result": "ACCEPTED", "motion": True},
        notes="(c) 真實 reactive_stop 格式不進仲裁 → 會揮手")
for i in range(17, 19):
    add("C7", i, True, sp("可以表演一下嗎"), cand("wave_hello"), cool=COOL["wave_hello"],
        expect={"skill_result": "BLOCKED_BY_SAFETY", "reason": "depth_not_clear_for_motion"},
        notes="(d) 拔 D435 USB 後 1 s 內按 Enter")

# C8 active 技能中來新語音
for i in range(1, 6):
    add("C8", i, True, sp("可以表演一下嗎"), cand("sit_along", REPLY["sit_along"]),
        follow={"text": "今天天氣不錯", "delay_s": 1.0}, cool=COOL["sit_along"],
        expect={"skill_result": "ACCEPTED", "followup": "dropped_no_trace", "motion": True},
        notes="sit_along 執行中送第二句，預期被丟且無 trace")

# C9 控台技能鈕路徑 /brain/skill_request
for i in range(1, 4):
    add("C9", i, False, sreq={"skill": "fly", "args": {}}, cool=2,
        expect={"skill_result": None, "trace": False, "log": "unknown skill"})
for i in range(4, 7):
    add("C9", i, True, sreq={"skill": "wave_hello", "args": {}}, repeat=1.0, cool=COOL["wave_hello"],
        expect={"first": "ACCEPTED", "second": "cooldown_no_trace", "skill_result": "ACCEPTED", "motion": True},
        notes="第一次會揮手，1 s 後第二次在冷卻內")
for i in range(7, 9):
    add("C9", i, False, sreq={"skill": "wiggle", "args": {}}, cool=32,
        expect={"say_canned": "ACCEPTED", "pending_confirm": True, "motion": False}, notes="不比 OK，不會動")

if errors:
    sys.exit(f"語音句 intent 不是 chat：{errors}")
out = Path(__file__).with_name("e5_cases.json")
json.dump({"version": "r14-2026-10-07", "source": "協定 §5.1；intent 由 81e5018 IntentClassifier 算", "cases": cases},
          open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(cases), "cases →", out)
