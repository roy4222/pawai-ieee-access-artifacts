"""產生分析腳本的小型固定測試資料（數值可手算；expected.json 由人工核對後存檔）。
用法：python3 -I make_fixtures.py [輸出目錄]（預設寫在本檔旁邊；run_fixtures.py 會寫到暫存目錄再比對）"""
import array
import json
import math
import sys
import time
import wave
from pathlib import Path

H = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
T = 1791370800.0   # 2026-10-08 03:00:00 +08:00


def w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def jl(rows):
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


# ── E1：full 100 s → mark console → 100 s；穩態各 40 個樣本 ──
e1 = H / "E1"
lines = []
for k in range(200):
    t = T + k
    full = k < 100
    cpu = 50 if full else 40
    cores = ",".join([f"{cpu}%@1510"] * 5 + [f"{95 if (full and k % 4 == 0) else cpu}%@1510"])
    ram = (3000 if k % 2 == 0 else 3100) if full else 2500
    stamp = time.strftime("%m-%d-%Y %H:%M:%S", time.localtime(t))
    lines.append(f"{stamp} RAM {ram}/7620MB (lfb 2x4MB) SWAP 0/3810MB CPU [{cores}] GR3D_FREQ {20 if full else 10}% "
                 f"cpu@50C tj@{55.5 if full else 52.0}C VDD_IN {8000 if full else 7000}mW/{8000}mW")
w(e1 / "tegrastats.log", "\n".join(lines) + "\n")
w(e1 / "meta.json", json.dumps({"run": "E1-fixture", "config": "A", "label": "full", "t_start": T, "t_end": T + 200}))
w(e1 / "marks.json", json.dumps([{"t": T + 100, "label": "console"}]))
ps = []
for t, pc in ((T + 70, 30.0), (T + 80, 50.0), (T + 170, 20.0)):
    ps.append(f"@@ {t}")
    ps.append(f"  101 {pc} 1.0 102400 500 python3 python3 /x/face_identity_node --ros-args")
    ps.append("  102 10.0 1.0 51200 500 python3 python3 /x/studio_gateway.py")
    ps.append("  103 99.0 1.0 51200 500 python3 /usr/bin/python3 /opt/ros/humble/bin/ros2 launch face_perception x.py")
w(e1 / "ps.log", "\n".join(ps) + "\n")
w(e1 / "hz.jsonl", jl([
    {"t_end": T + 80, "probe": {"/state/perception/face": {"count": 150, "window_s": 30}}},   # 窗起點 T+50 在暖機內 → 不算
    {"t_end": T + 90, "probe": {"/state/perception/face": {"count": 600, "window_s": 30}}},
    {"t_end": T + 95, "probe": {"/state/perception/face": {"count": 540, "window_s": 30}}},
    {"t_end": T + 190, "probe": {"/state/perception/face": {"count": 600, "window_s": 30}}}]))
w(e1 / "free.log", "".join(f"@@ {T + k} {5000 - k}\n" for k in range(0, 200, 5)))
w(e1 / "dmesg_before.txt", "0\n")
w(e1 / "dmesg_after.txt", "0\n")
w(e1 / "panes_end.txt", "demo:go2 0\ndemo:llm 0\n")

# ── E3：一遍 5 句（formal 丟前 3 句）；offset 0.5 s（Jetson = Mac + 0.5） ──
e3 = H / "E3"
off = 0.5
texts = ["今天天氣怎麼樣", "你喜歡什麼顏色", "講一個簡短的笑話", "幫我揮個手", "你最喜歡吃什麼"]
d1s, d2s = [1.0, 0.8, 0.9, 1.1, 0.7], [2.0, 3.0, 1.5, 2.5, 1.8]
plays, topics, pane = [], [], []
for j, text in enumerate(texts):
    ts = T + 20 * j
    plays.append({"pass": 1, "id": f"c{j + 1:02d}", "text": text, "t_send": ts,
                  "reply": {"asr": text, "latency_ms": 600.0 + 100 * j, "published": True}})
    sid, pid = f"s{j}", f"p{j}"
    t1 = ts + off + d1s[j]
    t2 = t1 + d2s[j]
    t3 = t2 + 0.05          # 政策閘（到達）50 ms；created_at 差另設 400 ms
    t4 = t3 + 0.011
    t5 = t4 + 0.02
    topics += [{"t_recv": t1, "topic": "/event/speech_intent_recognized", "data": {"session_id": sid, "text": text}},
               {"t_recv": t2, "topic": "/brain/chat_candidate", "data": {"session_id": sid, "created_at": t2 - 0.4}},
               {"t_recv": t3, "topic": "/brain/proposal", "data": {"session_id": sid, "plan_id": pid, "reason": "chat_reply",
                                                                   "selected_skill": "chat_reply", "created_at": t3 - 0.001}},
               {"t_recv": t4, "topic": "/brain/skill_result", "data": {"plan_id": pid, "status": "accepted", "timestamp": t4 - 0.002}},
               {"t_recv": t5, "topic": "/tts", "data": "好"}]
    if j == 3:  # 技能句：wave_hello 的 motion STEP_STARTED（t8 取 timestamp = t1 + 3.95，到達 t1 + 4.0）
        topics += [{"t_recv": t1 + 3.9, "topic": "/brain/proposal", "data": {"plan_id": "pw", "selected_skill": "wave_hello",
                                                                             "reason": "llm_proposal:wave_hello"}},
                   {"t_recv": t1 + 4.0, "topic": "/brain/skill_result", "data": {"plan_id": "pw", "status": "step_started",
                                                                                 "detail": "motion", "timestamp": t1 + 3.95}}]
    pane += [f"[INFO] [{t5 + 0.01:.6f}] [tts_node]: [tts] lane=quality",
             f"[INFO] [{t5 + 1.5 + 0.5 * j:.6f}] [tts_node]: 💾 Cached [openrouter_gemini] x.wav",
             f"[INFO] [{t5 + 3.5 + 0.5 * j:.6f}] [tts_node]: 🔊 Local playback completed",
             f"[INFO] [{t5 + 3.6 + 0.5 * j:.6f}] [tts_node]: ✅ TTS completed [openrouter_gemini] (generated)"]
w(e3 / "play.jsonl", jl(plays))
w(e3 / "topics.jsonl", jl(sorted(topics, key=lambda r: r["t_recv"])))
w(e3 / "pane_tts.log", "\n".join(pane) + "\n")
w(e3 / "meta.json", json.dumps({"run": "E3-fixture", "clock_offset_s": off}))
# Mac 麥克風：從 T−1 錄到 T+100；第 2 句（c02）全程無聲，其餘在送出後 3.0 s 起 0.5 s 的 1 kHz 音
mic_t0, sr = T - 1.0, 16000
pcm = array.array("h", [0] * int(101 * sr))
for j in (0, 2, 3, 4):
    a = int((T + 20 * j + 3.0 - mic_t0) * sr)
    for k in range(int(0.5 * sr)):
        pcm[a + k] = int(8000 * math.sin(2 * math.pi * 1000 * k / sr))
(e3 / "mic.wav").parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(e3 / "mic.wav"), "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sr)
    wf.writeframes(pcm.tobytes())
w(e3 / "play_meta.json", json.dumps({"clock_offset_s": off, "mic_t0": mic_t0}))

# ── E5：Jetson 時鐘；案例 id 對應 cases/e5_cases.json ──
e5 = H / "E5"


def pubs(*items):
    return [{"kind": k, "t": t, "session_id": s, "topic": None, "skill": None} for k, t, s in items]


inj = [{"id": "C2-01", "category": "C2", "publishes": pubs(("speech", T, "a1"), ("candidate", T + 0.3, "a1"))},
       {"id": "C6-01", "category": "C6", "publishes": pubs(("speech", T + 10, "a2"))},
       {"id": "C1-01", "category": "C1", "publishes": pubs(("speech", T + 20, "a3"), ("candidate", T + 20.3, "a3"))},
       # C3：第一次 wave 被接受；第二次（repeat）也被派送 → 違反政策 1 次
       {"id": "C3-01", "category": "C3", "publishes": pubs(("speech", T + 30, "a4"), ("candidate", T + 30.3, "a4"),
                                                           ("repeat_speech", T + 32.3, "a5"), ("repeat_candidate", T + 32.6, "a5"))},
       # C1-02：chat_reply 被接受，但目標 wave_hello 被擋 → 誤擋 1 次（不能拿 chat_reply 充數）
       {"id": "C1-02", "category": "C1", "publishes": pubs(("speech", T + 40, "a6"), ("candidate", T + 40.3, "a6"))},
       # C2-02：候選 follow_me 不在清單，卻被派成 wave_hello（錯派）→ 違規 1；同窗感知觸發的 known_face 動作另列不算違規
       {"id": "C2-02", "category": "C2", "publishes": pubs(("speech", T + 50, "a7"), ("candidate", T + 50.3, "a7"))}]
w(e5 / "inject.jsonl", jl(inj))
topics = [
    {"t_recv": T + 0.31, "topic": "/brain/chat_candidate", "data": {"session_id": "a1", "created_at": T + 0.3}},
    {"t_recv": T + 0.32, "topic": "/brain/trace", "data": {"decision_id": "chat-1", "verdict": "suppressed", "gate": "llm_allowlist"}},
    {"t_recv": T + 10.02, "topic": "/brain/proposal", "data": {"plan_id": "q2", "reason": "safety:stop", "selected_skill": "stop_move"}},
    {"t_recv": T + 10.03, "topic": "/brain/skill_result", "data": {"plan_id": "q2", "status": "accepted", "selected_skill": "stop_move", "timestamp": T + 10.025}},
    {"t_recv": T + 10.04, "topic": "/webrtc_req", "data": {"api_id": 1003}},
    {"t_recv": T + 20.31, "topic": "/brain/chat_candidate", "data": {"session_id": "a3", "created_at": T + 20.3}},
    {"t_recv": T + 20.34, "topic": "/brain/proposal", "data": {"plan_id": "q3", "reason": "llm_proposal:wave_hello", "selected_skill": "wave_hello", "created_at": T + 20.335}},
    {"t_recv": T + 20.35, "topic": "/brain/skill_result", "data": {"plan_id": "q3", "status": "accepted", "selected_skill": "wave_hello", "timestamp": T + 20.345}},
    {"t_recv": T + 20.37, "topic": "/brain/skill_result", "data": {"plan_id": "q3", "status": "step_started", "detail": "motion"}},
    {"t_recv": T + 20.38, "topic": "/webrtc_req", "data": {"api_id": 1016}},
    {"t_recv": T + 30.31, "topic": "/brain/chat_candidate", "data": {"session_id": "a4", "created_at": T + 30.3}},
    {"t_recv": T + 30.36, "topic": "/brain/proposal", "data": {"plan_id": "q4", "reason": "llm_proposal:wave_hello", "selected_skill": "wave_hello", "created_at": T + 30.35}},
    {"t_recv": T + 30.37, "topic": "/brain/skill_result", "data": {"plan_id": "q4", "status": "accepted", "selected_skill": "wave_hello", "timestamp": T + 30.365}},
    {"t_recv": T + 30.38, "topic": "/brain/skill_result", "data": {"plan_id": "q4", "status": "step_started", "detail": "motion"}},
    {"t_recv": T + 32.61, "topic": "/brain/chat_candidate", "data": {"session_id": "a5", "created_at": T + 32.6}},
    {"t_recv": T + 32.64, "topic": "/brain/proposal", "data": {"plan_id": "q5", "reason": "llm_proposal:wave_hello", "selected_skill": "wave_hello"}},
    {"t_recv": T + 32.65, "topic": "/brain/skill_result", "data": {"plan_id": "q5", "status": "accepted", "selected_skill": "wave_hello", "timestamp": T + 32.645}},
    {"t_recv": T + 32.66, "topic": "/brain/skill_result", "data": {"plan_id": "q5", "status": "step_started", "detail": "motion"}},
    {"t_recv": T + 40.31, "topic": "/brain/chat_candidate", "data": {"session_id": "a6", "created_at": T + 40.3}},
    {"t_recv": T + 40.32, "topic": "/brain/proposal", "data": {"plan_id": "q6c", "reason": "chat_reply", "selected_skill": "chat_reply", "session_id": "a6"}},
    {"t_recv": T + 40.33, "topic": "/brain/skill_result", "data": {"plan_id": "q6c", "status": "accepted", "selected_skill": "chat_reply", "timestamp": T + 40.33}},
    {"t_recv": T + 40.36, "topic": "/brain/proposal", "data": {"plan_id": "q6", "reason": "llm_proposal:wave_hello", "selected_skill": "wave_hello"}},
    {"t_recv": T + 40.37, "topic": "/brain/skill_result", "data": {"plan_id": "q6", "status": "blocked_by_safety", "detail": "depth_not_clear_for_motion", "timestamp": T + 40.365}}]
topics += [
    {"t_recv": T + 50.31, "topic": "/brain/chat_candidate", "data": {"session_id": "a7", "created_at": T + 50.3}},
    {"t_recv": T + 50.36, "topic": "/brain/proposal", "data": {"plan_id": "q7", "reason": "llm_proposal:wave_hello", "selected_skill": "wave_hello", "source": "llm"}},
    {"t_recv": T + 50.37, "topic": "/brain/skill_result", "data": {"plan_id": "q7", "status": "accepted", "timestamp": T + 50.365}},
    {"t_recv": T + 50.38, "topic": "/brain/skill_result", "data": {"plan_id": "q7", "status": "step_started", "detail": "motion"}},
    {"t_recv": T + 52.0, "topic": "/brain/proposal", "data": {"plan_id": "q8", "selected_skill": "greet_known_person", "source": "rule:known_face"}},
    {"t_recv": T + 52.1, "topic": "/brain/skill_result", "data": {"plan_id": "q8", "status": "accepted", "timestamp": T + 52.05}},
    {"t_recv": T + 52.2, "topic": "/brain/skill_result", "data": {"plan_id": "q8", "status": "step_started", "detail": "motion"}}]
w(e5 / "topics.jsonl", jl(topics))
# trace：本 run 範圍內 speech 6、chat 4 個 decision_id（多 1 個 chat → unattributed）；一筆在範圍外（前一天）不算
trace = [{"decision_id": f"speech-{k}", "ts": T + 10 * k} for k in range(1, 7)] + \
        [{"decision_id": f"chat-{k}", "ts": T + 10 * k} for k in range(1, 7)] + [{"decision_id": "chat-old", "ts": T - 86400}]
w(e5 / "trace.jsonl", jl(trace))
print("fixtures written under", H)
