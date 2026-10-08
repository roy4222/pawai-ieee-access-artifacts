"""play（設計 §3.5）：經 gateway /ws/speech（WAV）或 /ws/text 送句庫，節奏閘＋動作 watchdog。"""
import json
import sys
import time
import wave
from pathlib import Path

from . import gate, ops
from .core import REPO, Fail, data_dir, log_line, run_dir, say, write_json


def load_bank(cfg, subsets):
    data = json.loads((REPO / cfg["paths"]["bank"]).read_text(encoding="utf-8"))
    items = data["items"] if isinstance(data, dict) else data
    want = {"chat", "skill", "status"} if "all" in subsets else set(subsets)
    return [it for it in items if it["subset"] in want]


def ws_url(cfg, path):
    base = cfg["hosts"]["gateway"].replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    tok = cfg["hosts"].get("gateway_token")
    return base + path + (f"?token={tok}" if tok else "")


def send_one(cfg, mode, item, wav_dir):
    from websockets.sync.client import connect
    url = ws_url(cfg, "/ws/speech" if mode == "speech" else "/ws/text")
    payload = (Path(wav_dir) / f"{item['id']}.wav").read_bytes() if mode == "speech" else item["text"]
    with connect(url, open_timeout=10, close_timeout=2, max_size=2 ** 22) as ws:
        t_send = time.time()
        ws.send(payload)
        reply = json.loads(ws.recv(timeout=20))
        t_reply = time.time()
    return t_send, t_reply, reply


class Mic:
    """Mac 麥克風整段錄音（16 kHz mono）；mic_t0 為第一個 buffer 起點的 Mac 時鐘。"""
    def __init__(self, path):
        import sounddevice as sd
        self.path, self.frames, self.t0 = path, [], None
        self.stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16", callback=self.cb)

    def cb(self, data, n, t, status):
        if self.t0 is None:
            self.t0 = time.time() - n / 16000
        self.frames.append(bytes(data))

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()
        with wave.open(str(self.path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"".join(self.frames))


def bank_violations(items):
    """重用 bank/check_bank.py（規則讀自 make_bank.py）；回傳 [(id, text, hits)]。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_bank", REPO / "bank" / "check_bank.py")
    cb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cb)
    R = cb.rules()
    return [(it.get("id"), it.get("text"), hits) for it in items if (hits := cb.check(it, R))]


def confirm(prompt):
    """--confirm-each：只有明確 Enter（或 y）才送；EOF／其他輸入一律中止。"""
    try:
        ans = input(prompt)
    except EOFError:
        raise Fail("play: confirm", "stdin EOF，未確認，中止（不送出）")
    if ans.strip().lower() not in ("", "y"):
        raise Fail("play: confirm", f"輸入 {ans!r}，中止")


def start_snapshot(r, jd, tag):
    """背景執行緒在 Jetson 上抓 /face_identity/debug_image 一幀存 snap_<tag>.jpg（只訂閱）；失敗只記 warning。"""
    import threading
    res = {"result": "pending"}

    def work():
        try:
            p = r.ssh(f"source {r.exp}/ros_env.zsh && timeout 15 python3 {r.exp}/snapshot.py --out {jd}/snap_{tag}.jpg",
                      f"play: snapshot {tag}", check=False, quiet=True, timeout=30)
            res["result"] = "ok" if p.returncode == 0 else f"rc={p.returncode} {(p.stderr or '').strip()[:120]}"
        except Exception as e:
            res["result"] = f"{type(e).__name__}: {e}"
    res["thread"] = threading.Thread(target=work, daemon=True)
    res["thread"].start()
    return res


def clear_tts_cache(r, cfg, ps):
    """協定 §1.3：每遍開始前清空 TTS 快取並確認為空（AudioCache.get 會先查檔案存在，執行中刪檔安全）。"""
    cache = cfg["paths"]["tts_cache"]
    r.ssh(f"test ! -d {cache} || find {cache} -mindepth 1 -delete; test $(ls -A {cache} 2>/dev/null | wc -l) -eq 0",
          f"play: 第 {ps} 遍前清 TTS 快取")


def play(r, cfg, a):
    subsets = [s for s in a.subset.split(",") if s]
    items = load_bank(cfg, subsets)
    if a.limit is not None:
        items = items[:a.limit]
    if not items:
        raise Fail("play", f"subset {a.subset} 沒有句子")
    bad = bank_violations(items)
    if bad:  # 送出前強制驗證禁字（前進、停止、危險動作、召喚、短問候、chat 額外禁字）
        raise Fail("play: bank check", "；".join(f"{i}「{t}」命中 {'、'.join(h)}" for i, t, h in bad[:5]))
    wav_dir = Path(a.wav_dir or (REPO / cfg["paths"].get("bank_wav", "bank/wav")))
    if a.mode == "speech":
        missing = [it["id"] for it in items if not (wav_dir / f"{it['id']}.wav").exists()]
        if missing:
            raise Fail("play", f"{wav_dir} 缺 WAV：{missing[:10]}")
    if any(it["subset"] == "skill" for it in items) and not a.confirm_each:
        raise Fail("play", "技能句只能在 Roy 在場時送：請加 --confirm-each")
    if a.confirm_each and not r.dry and not sys.stdin.isatty():
        raise Fail("play", "--confirm-each 需要在終端機前由人按 Enter（stdin 不是 tty）")
    allowed = set(cfg["safety"]["inplace_api_ids"])
    jd = ops.jrun(r, a.run)
    topics_path = f"{jd}/topics.jsonl"
    own_logger = False
    if not r.dry and "exp" not in r.sessions():
        # 沒有 record：自己起只有 log 視窗的 exp，讓節奏閘與 watchdog 有資料可讀
        ops.sync_scripts(r)
        r.ssh(f"mkdir -p {jd} && tmux new-session -d -s exp -n log "
              f"\"zsh {r.exp}/exp_window.sh log {jd} {','.join(cfg['record']['topics'])}\"", "play: 起 topic_logger")
        r.ssh(f"for i in $(seq 40); do test -f {topics_path}.ready && exit 0; sleep 0.5; done; exit 1", "play: logger ready", timeout=40)
        own_logger = True
    elif not r.dry:
        r.ssh(f"test -f {topics_path}", f"play: {topics_path} 存在（record 的 run 要與 play 相同）")
    c = ops.clock(r, 5)
    off = c["offset_s"]
    local = run_dir(cfg, a.run)
    t_start_j = time.time() + off

    def fetch():
        return gate.parse_tail(r.ssh(f"tail -c 60000 {topics_path}", "play: tail topics", timeout=20).stdout)

    def on_trip(rec):  # 在 watchdog 執行緒內立即執行，不等主執行緒（可能卡在 WebSocket／Enter）
        say(f"WATCHDOG：{rec} → down --force")
        ops.emergency_down(r, cfg)

    wd = None if r.dry else gate.Watchdog(fetch, allowed, t_start_j, on_trip,
                                          poll_s=cfg.get("play", {}).get("watchdog_poll_s", 1.0)).start()

    stop_timeout = cfg.get("play", {}).get("emergency_stop_timeout_s", 300)

    def check_wd():
        if wd is not None and wd.tripped:
            # 主程序等緊急停止完成（或明確失敗）才結束，不讓背景停止被程序退出截斷
            if not wd.wait_stopped(stop_timeout):
                raise Fail("play: watchdog", f"出現 {wd.violation}；緊急停止 {stop_timeout} s 內未完成，請立即手動 down", code=4)
            if wd.stop_error and wd.stop_error.startswith("Fail: down: pending"):
                raise Fail("play: watchdog", f"出現 {wd.violation}；已 down --force，Jetson 已清乾淨；"
                           f"另有未完成復原（fault status 可看）：{wd.stop_error}", code=4)
            if wd.stop_error:
                raise Fail("play: watchdog", f"出現 {wd.violation}；緊急停止失敗：{wd.stop_error}（請手動確認 Jetson）", code=4)
            raise Fail("play: watchdog", f"出現原地集合以外的動作：{wd.violation}；已 down --force", code=4)
        if wd is not None and wd.blind:  # 監控讀不到 topics：不再送句子（沒有動作證據，不 down）
            raise Fail("play: watchdog blind", f"連續 {wd.fail_streak} 次讀不到 topics.jsonl：{wd.error}")

    def nap(sec):  # 給節奏閘用的 sleep：等待期間也檢查 watchdog
        end = time.time() + sec
        while True:
            check_wd()
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(0.2, left))

    meta = {"run": a.run, "mode": a.mode, "subset": a.subset, "passes": a.passes, "clock_offset_s": off,
            "clock_rtt_s": c["rtt_s"], "wav_dir": str(wav_dir), "mic_t0": None, "own_logger": own_logger}
    mic = None
    if a.record_mic:
        try:
            mic = Mic(local / "mic.wav")
        except Exception as e:  # 沒有 sounddevice 或麥克風
            say(f"WARN --record-mic 不可用：{e}")
    snap_dir = f"{jd}" if a.snapshot else None
    out = open(local / "play.jsonl", "a", encoding="utf-8")
    try:
        if mic:
            mic.__enter__()
        for ps in range(1, a.passes + 1):
            if not getattr(a, "keep_tts_cache", False):
                clear_tts_cache(r, cfg, ps)
            for it in items:
                check_wd()
                if a.confirm_each:
                    confirm(f"下一句：{it['text']}（預期：{it.get('gold_skill') or '不動'}）→ Enter 送出 ")
                    check_wd()
                if r.dry:
                    print(f"[dry-run] ws {a.mode} {it['id']} {it['text']}")
                    continue
                snap = start_snapshot(r, snap_dir, f"{it['id']}_p{ps}") if snap_dir else None
                try:
                    t_send, t_reply, reply = send_one(cfg, a.mode, it, wav_dir)
                except Exception as e:
                    t_send, t_reply, reply = time.time(), time.time(), {"error": f"{type(e).__name__}: {e}", "published": False}
                check_wd()
                rec = {"pass": ps, "id": it["id"], "text": it["text"], "t_send": t_send, "t_reply": t_reply, "reply": reply}
                if reply.get("published"):
                    g = gate.wait(wd.snapshot, t_send + off, timeout=cfg.get("play", {}).get("gap_timeout_s", 40.0),
                                  cooldown=cfg.get("play", {}).get("cooldown_s", 1.5), sleep=nap)
                else:
                    nap(1.5)
                    g = {"waited_s": 1.5, "gap_timeout": False, "skipped": "not_published"}
                rec["gate"] = g
                if snap:
                    snap["thread"].join(timeout=20)
                    rec["snapshot"] = snap["result"]
                    if snap["result"] != "ok":
                        say(f"WARN snapshot {it['id']}：{snap['result']}")
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                log_line(f"play {a.run} {it['id']} published={reply.get('published')} gate={g}")
                say(f"{ps}/{it['id']} {it['text']} → {reply.get('asr') or reply.get('error')} "
                    f"({reply.get('latency_ms')} ms) gate {g['waited_s']} s{' TIMEOUT' if g.get('gap_timeout') else ''}")
        if wd is not None:  # 結束前最後一次檢查（涵蓋最後一句的 cooldown）
            wd.stop()
            check_wd()
    finally:
        if wd is not None:
            wd.stop()
            if wd.tripped:
                wd.wait_stopped(stop_timeout)
        out.close()
        if mic:
            mic.__exit__(None, None, None)
            meta["mic_t0"] = mic.t0
        meta["watchdog"] = {"tripped": bool(wd and wd.tripped), "violation": wd and wd.violation,
                            "last_error": wd and wd.error}
        write_json(local / "play_meta.json", meta)
        if own_logger and not (wd and wd.tripped):
            r.ssh("tmux send-keys -t exp:log C-c; sleep 2; tmux kill-session -t exp 2>/dev/null; true", "play: 收 topic_logger")
            r.rsync_back(jd, local, "play: rsync topics")
    return 0
