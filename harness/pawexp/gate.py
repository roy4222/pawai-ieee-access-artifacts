#!/usr/bin/env python3
"""play／inject 的節奏閘與動作 watchdog（設計 §3、§3.5）。純邏輯，不依賴套件其他模組，可單獨 --selftest。

時間一律用 Jetson 時鐘（topics.jsonl 的 t_recv）；呼叫端把 Mac 的 t_send 換算後傳入。
"""
import argparse
import json
import sys
import time

TERMINAL = {"COMPLETED", "BLOCKED_BY_SAFETY", "STEP_FAILED"}


def _data(rec):
    d = rec.get("data")
    return d if isinstance(d, dict) else {}


def ready(lines, t_send):
    """(a) t_send 之後有終態 skill_result；(b) 最新 tts_playing 為 false，且 t_send 之後若有 /tts，之後要看到 false。"""
    term = [r for r in lines if r.get("topic") == "/brain/skill_result" and r["t_recv"] > t_send
            and str(_data(r).get("status", "")).upper() in TERMINAL]
    if not term:
        return False
    tp = [r for r in lines if r.get("topic") == "/state/tts_playing"]
    if tp and _data(tp[-1]).get("data") is not False:
        return False
    tts = [r["t_recv"] for r in lines if r.get("topic") == "/tts" and r["t_recv"] > t_send]
    if tts and not any(r["t_recv"] > tts[-1] and _data(r).get("data") is False for r in tp):
        return False
    return True


def wait(fetch, t_send, timeout=40.0, cooldown=1.5, poll=1.0, clock=time.time, sleep=time.sleep):
    """fetch() 回傳 topics.jsonl 尾端的記錄串列。回傳 {waited_s, gap_timeout}。clock 須與 t_send 同一時鐘基準的差值可用。"""
    t0 = clock()
    while True:
        if ready(fetch(), t_send):
            sleep(cooldown)
            return {"waited_s": round(clock() - t0, 3), "gap_timeout": False}
        if clock() - t0 >= timeout:
            return {"waited_s": round(clock() - t0, 3), "gap_timeout": True}
        sleep(poll)


def motion_violations(lines, allowed_api_ids, since=0.0):
    """/webrtc_req 出現原地集合以外的 api_id，或 /cmd_vel 有任何訊息 → 回傳違規記錄。"""
    bad = []
    for r in lines:
        if r.get("t_recv", 0) < since:
            continue
        if r.get("topic") == "/cmd_vel":
            bad.append(r)
        elif r.get("topic") == "/webrtc_req":
            api = _data(r).get("api_id")
            if api not in allowed_api_ids:
                bad.append(r)
    return bad


class Watchdog:
    """獨立執行緒每 poll_s 讀一次 topics 尾端並快取；出現違規就在執行緒內立刻呼叫 on_trip（只一次）。
    主執行緒在任何等待（WebSocket、Enter、cooldown）期間被卡住都不影響監控；結束時 stop() 會做最後一次檢查。"""

    def __init__(self, fetch, allowed_api_ids, since, on_trip, poll_s=1.0, blind_after=5):
        import threading
        self.fetch, self.allowed, self.since, self.on_trip, self.poll_s = fetch, set(allowed_api_ids), since, on_trip, poll_s
        self.lines, self.violation, self.error = [], None, None
        self.blind_after, self.fail_streak, self.blind = blind_after, 0, False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._tripped = threading.Event()
        self._stopped = threading.Event()   # 緊急停止（on_trip）跑完
        self.stop_error = None
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @property
    def tripped(self):
        return self._tripped.is_set()

    def start(self):
        self.check_once()
        self._thread.start()
        return self

    def check_once(self):
        try:
            lines = self.fetch()
        except Exception as e:  # 讀不到資料本身不是違規；連續 blind_after 次就判定監控失明
            self.error = f"{type(e).__name__}: {e}"
            self.fail_streak += 1
            if self.fail_streak >= self.blind_after:
                self.blind = True
            return None
        self.fail_streak = 0
        with self._lock:
            self.lines = lines
        bad = motion_violations(lines, self.allowed, since=self.since)
        if bad:
            self._trip(bad[0])
        return bad

    def _trip(self, rec):
        with self._lock:
            if self._tripped.is_set():
                return
            self.violation = rec
            self._tripped.set()
        import threading

        def run():
            try:
                self.on_trip(rec)
            except Exception as e:
                detail = f"{e.step}: {e.detail}" if hasattr(e, "step") else str(e)
                self.stop_error = f"{type(e).__name__}: {detail}"
            finally:
                self._stopped.set()
        # 非 daemon：主程式就算提早結束，直譯器也會等緊急停止跑完才退出
        threading.Thread(target=run, daemon=False, name="pawexp-emergency-stop").start()

    def wait_stopped(self, timeout):
        """等緊急停止完成；回傳 True＝已完成（成功與否看 stop_error）、False＝逾時仍在跑。"""
        return self._stopped.wait(timeout)

    def _loop(self):
        while not self._stop.wait(self.poll_s):
            self.check_once()

    def snapshot(self):
        with self._lock:
            return list(self.lines)

    def stop(self):
        """停止執行緒並做最後一次檢查；回傳違規記錄（無則 None）。"""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self.poll_s + 30)
        self.check_once()
        return self.violation


def parse_tail(text):
    out = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # tail -c 切到半行
        if isinstance(rec, dict) and "t_recv" in rec:
            out.append(rec)
    return out


def _selftest(timeout):
    start = time.time()

    def make_fetch(events):
        def fetch():
            now = time.time() - start
            return [dict(e, t_recv=start + e["dt"]) for e in events if e["dt"] <= now]
        return fetch

    t_send = start
    case_a = [
        {"dt": -5.0, "topic": "/state/tts_playing", "data": {"data": False}},
        {"dt": 0.1, "topic": "/tts", "data": "好啊"},
        {"dt": 0.15, "topic": "/state/tts_playing", "data": {"data": True}},
        {"dt": 0.2, "topic": "/brain/skill_result", "data": {"status": "COMPLETED", "plan_id": "p1"}},
        {"dt": 0.3, "topic": "/state/tts_playing", "data": {"data": False}},
    ]
    a = wait(make_fetch(case_a), t_send, timeout=timeout, cooldown=1.5, poll=0.2)
    ok_a = (not a["gap_timeout"]) and 1.5 <= a["waited_s"] <= 2.5
    print(json.dumps({"case": "A", **a, "pass": ok_a}))

    start = time.time()
    case_b = [{"dt": 0.1, "topic": "/state/tts_playing", "data": {"data": False}},
              {"dt": 0.2, "topic": "/brain/skill_result", "data": {"status": "ACCEPTED", "plan_id": "p2"}}]
    b = wait(make_fetch(case_b), start, timeout=timeout, cooldown=1.5, poll=0.2)
    ok_b = b["gap_timeout"] and timeout <= b["waited_s"] <= timeout + 1.0
    print(json.dumps({"case": "B", **b, "pass": ok_b}))

    lines = [{"t_recv": 1, "topic": "/webrtc_req", "data": {"api_id": 1016}},
             {"t_recv": 2, "topic": "/webrtc_req", "data": {"api_id": 1008}},
             {"t_recv": 3, "topic": "/cmd_vel", "data": None}]
    v = motion_violations(lines, {1002, 1003, 1004, 1005, 1016, 1017, 1020})
    ok_w = len(v) == 2
    print(json.dumps({"case": "watchdog", "violations": len(v), "pass": ok_w}))
    return 0 if ok_a and ok_b and ok_w else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="pawexp 節奏閘")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--timeout", type=float, default=40.0)
    a = ap.parse_args()
    if not a.selftest:
        ap.error("只支援 --selftest")
    sys.exit(_selftest(a.timeout))
