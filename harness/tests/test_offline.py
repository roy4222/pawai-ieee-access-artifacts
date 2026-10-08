#!/usr/bin/env python3
"""pawexp 離線回歸測試（不連 Jetson／RTX 8000、不發 ROS）：fake Runner、fake rclpy、PATH 上的 fake sudo／iptables。
用法：python3 harness/tests/test_offline.py [-v]
改寫自 r14 Review 的重現腳本（tasks/raw/r14-pawexp-review.md）。
"""
import importlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
REPO = HARNESS.parent
TMP = Path(tempfile.mkdtemp(prefix="pawexp-test-"))
os.environ["PAWEXP_DATA"] = str(TMP / "data")
sys.path.insert(0, str(HARNESS))

from pawexp import core, faults, gate, ops, play  # noqa: E402
from pawexp.core import Fail  # noqa: E402


def _offline_guard(event, args):
    """全測試期間禁止任何遠端程序與網路連線：誤呼叫到真的 ssh／scp／rsync（例如沒替換掉的 ops.clock）會直接失敗。"""
    if event == "subprocess.Popen" and Path(str(args[0])).name in ("ssh", "scp", "rsync"):
        raise AssertionError(f"離線測試不得啟動遠端程序：{args[1]}")
    if event == "socket.connect":
        raise AssertionError(f"離線測試不得連網：{args[1:]}")


sys.addaudithook(_offline_guard)


def fake_clock(*a, **k):
    return {"offset_s": 0.0, "rtt_s": 0.0, "n": 5, "t": time.time()}


def offline_ops():
    """重新載入 ops 並換上 clock 離線替身（ops.clock 本來會開常駐 ssh 量時鐘）。"""
    importlib.reload(ops)
    ops.clock = fake_clock
    return ops


ops.clock = fake_clock


def CP(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def base_cfg(**over):
    cfg = core.load_config()
    cfg = json.loads(json.dumps(cfg))
    cfg["paths"]["data_dir"] = str(TMP / "data")
    cfg.setdefault("play", {})["watchdog_poll_s"] = 0.05
    cfg["play"]["cooldown_s"] = 0.2
    cfg["play"]["gap_timeout_s"] = 2.0
    for k, v in over.items():
        cfg[k] = v
    return cfg


def play_args(**kw):
    d = dict(subset="chat", limit=1, wav_dir=None, mode="text", confirm_each=False, run="t-play",
             record_mic=False, snapshot=False, passes=1)
    d.update(kw)
    return types.SimpleNamespace(**d)


class FakeTail:
    """ssh 'tail -c …' 回傳 records；其他 ssh 回成功。"""
    dry = False
    exp = "/fake/exp"
    jetson = "fake"

    def __init__(self):
        self.records, self.calls = [], []
        self.lock = threading.Lock()

    def add(self, rec):
        with self.lock:
            self.records.append(rec)

    def sessions(self):
        return ["exp"]

    def ssh(self, cmd, step, **kw):
        self.calls.append(step)
        if cmd.startswith("tail -c"):
            with self.lock:
                return CP(out="\n".join(json.dumps(x) for x in self.records))
        return CP()

    def rsync_back(self, *a, **kw):
        return CP()


class PlayTests(unittest.TestCase):
    def setUp(self):
        importlib.reload(play)
        self.downs = []
        ops.clock = lambda *a, **k: {"offset_s": 0.0, "rtt_s": 0.0}
        ops.emergency_down = lambda r, cfg: self.downs.append(time.time())

    def test_forbidden_bank_blocked_before_send(self):
        bank = TMP / "unsafe-bank.json"
        bank.write_text(json.dumps({"items": [{"id": "u1", "subset": "chat", "mode": "chat", "text": "你往前走一點",
                                               "gold_skill": []}]}, ensure_ascii=False), encoding="utf-8")
        cfg = base_cfg()
        cfg["paths"]["bank"] = str(bank)
        sent = []
        play.send_one = lambda *a: sent.append(a) or (0, 0, {"published": False})
        with self.assertRaises(Fail) as cm:
            play.play(FakeTail(), cfg, play_args())
        self.assertIn("bank check", cm.exception.step)
        self.assertEqual(sent, [])

    def test_watchdog_catches_motion_in_final_cooldown(self):
        r = FakeTail()
        now = time.time()

        def send(*a):
            r.add({"topic": "/brain/skill_result", "t_recv": time.time(), "data": {"status": "completed"}})
            r.add({"topic": "/state/tts_playing", "t_recv": time.time(), "data": {"data": False}})
            threading.Timer(0.08, lambda: r.add({"topic": "/webrtc_req", "t_recv": time.time(),
                                                  "data": {"api_id": 1008}})).start()
            return now - 0.01, now, {"published": True}
        play.send_one = send
        with self.assertRaises(Fail) as cm:
            play.play(r, base_cfg(), play_args())
        self.assertEqual(cm.exception.code, 4)
        self.assertEqual(len(self.downs), 1)

    def test_watchdog_runs_while_main_thread_blocked(self):
        r = FakeTail()

        def send(*a):  # 模擬卡在 WebSocket 等回覆；期間出現 /cmd_vel
            r.add({"topic": "/cmd_vel", "t_recv": time.time(), "data": None})
            time.sleep(0.5)
            return time.time(), time.time(), {"published": False}
        play.send_one = send
        t0 = time.time()
        with self.assertRaises(Fail) as cm:
            play.play(r, base_cfg(), play_args())
        self.assertEqual(cm.exception.code, 4)
        self.assertTrue(self.downs and self.downs[0] - t0 < 0.45, "down 必須在 send 回來之前就由 watchdog 執行緒觸發")

    def test_confirm_each_eof_aborts(self):
        play.send_one = lambda *a: self.fail("EOF 時不該送出")
        old = sys.stdin
        sys.stdin = io.StringIO("")
        sys.stdin.isatty = lambda: True
        try:
            with self.assertRaises(Fail) as cm:
                play.play(FakeTail(), base_cfg(), play_args(confirm_each=True))
        finally:
            sys.stdin = old
        self.assertIn("confirm", cm.exception.step)

    def test_confirm_each_requires_tty(self):
        old = sys.stdin
        sys.stdin = io.StringIO("\n")
        try:
            with self.assertRaises(Fail):
                play.play(FakeTail(), base_cfg(), play_args(confirm_each=True))
        finally:
            sys.stdin = old


def load_inject(type_after):
    """載入 inject_arbitration，換上 fake rclpy：clock ≥ type_after 才在 graph 出現 /webrtc_req。"""
    spec = importlib.util.spec_from_file_location("inject_t", HARNESS / "jetson" / "inject_arbitration.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    clock = types.SimpleNamespace(t=100.0)
    m.time = types.SimpleNamespace(time=lambda: clock.t, strftime=lambda *a: "now", localtime=lambda *a: None)
    w = types.SimpleNamespace(subs=[], published=[], order=[])

    class Msg:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Node:
        def __init__(self, *a):
            pass

        def create_publisher(self, cls, topic, q):
            w.order.append(("pub", topic))
            return types.SimpleNamespace(publish=lambda msg: w.published.append((topic, getattr(msg, "data", None))))

        def create_subscription(self, cls, topic, cb, q):
            w.subs.append(topic)
            w.order.append(("sub", topic))

        def get_topic_names_and_types(self):
            return [] if clock.t < type_after else [("/webrtc_req", ["go2_interfaces/msg/WebRtcReq"])]

        def destroy_node(self):
            pass

    rclpy = types.ModuleType("rclpy")
    rclpy.init = lambda: None
    rclpy.try_shutdown = lambda: None
    rclpy.spin_once = lambda *a, **k: setattr(clock, "t", clock.t + 0.05)
    mods = {"rclpy": rclpy, "rclpy.node": {"Node": Node},
            "rclpy.qos": {"DurabilityPolicy": types.SimpleNamespace(VOLATILE=0, TRANSIENT_LOCAL=1),
                          "ReliabilityPolicy": types.SimpleNamespace(RELIABLE=0, BEST_EFFORT=1), "QoSProfile": lambda **k: k},
            "std_msgs.msg": {"Bool": Msg, "String": Msg}, "rosidl_runtime_py.utilities": {"get_message": lambda t: Msg}}
    for name, attrs in mods.items():
        if isinstance(attrs, dict):
            mod = types.ModuleType(name)
            mod.__dict__.update(attrs)
            attrs = mod
        sys.modules[name] = attrs
    return m, w


CASE = {"id": "C9-T", "category": "C9", "needs_roy": False, "skill_request": {"skill": "fly"}, "wait_cooldown_s": 0}


class InjectTests(unittest.TestCase):
    def test_waits_for_late_webrtc_discovery_before_publishing(self):
        m, w = load_inject(type_after=101.0)
        rc = m.run([CASE], str(TMP / "inj1.jsonl"), False, 0.1)
        self.assertEqual(rc, 0)
        self.assertIn("/webrtc_req", w.subs)
        first_pub = next(i for i, (k, t) in enumerate(w.order) if k == "pub")
        self.assertLess(w.order.index(("sub", "/webrtc_req")), first_pub, "publisher 必須在安全訂閱之後才建立")
        rec = json.loads((TMP / "inj1.jsonl").read_text().splitlines()[-1])
        self.assertEqual([p["kind"] for p in rec["publishes"]], ["skill_request"])

    def test_no_discovery_means_no_injection(self):
        m, w = load_inject(type_after=10 ** 9)
        rc = m.run([CASE], str(TMP / "inj2.jsonl"), False, 0.1, discovery_timeout=2.0)
        self.assertEqual(rc, 5)
        self.assertEqual(w.published, [])

    def test_confirm_each_eof_aborts_without_publishing(self):
        m, w = load_inject(type_after=0)
        old = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            rc = m.run([dict(CASE, needs_roy=True)], str(TMP / "inj3.jsonl"), True, 0.1)
        finally:
            sys.stdin = old
        self.assertEqual(rc, 3)
        self.assertEqual(w.published, [])

    def test_forged_intent_rejected(self):
        m, _ = load_inject(type_after=0)
        bad = [{"id": "F1", "category": "C6", "needs_roy": False, "speech": {"text": "今天天氣很好", "intent": "come_here"}},
               {"id": "F2", "category": "C2", "needs_roy": False, "speech": {"text": "你好棒", "intent": "stand"},
                "candidate": {"proposed_skill": "dance"}}]
        errs = m.validate(bad, m.WHITELIST)
        self.assertTrue(any("F1" in e for e in errs) and any("F2" in e for e in errs), errs)

    def test_case_file_still_valid(self):
        m, _ = load_inject(type_after=0)
        cases = json.loads((REPO / "cases/e5_cases.json").read_text(encoding="utf-8"))["cases"]
        self.assertEqual(m.validate(cases, m.WHITELIST), [])

    def test_mac_inject_confirm_requires_tty(self):
        from pawexp import inject
        r = FakeTail()
        a = types.SimpleNamespace(cases="cases/e5_cases.json", only="C9", limit=1, confirm_each=True,
                                  run="t-inj", dry_run=False)
        old = sys.stdin
        sys.stdin = io.StringIO("\n")
        try:
            with self.assertRaises(Fail) as cm:
                inject.inject(r, base_cfg(), a)
        finally:
            sys.stdin = old
        self.assertIn("tty", cm.exception.detail)


class FaultRunner:
    """記錄所有 ssh；fail_on(step 子字串) 讓該步失敗。"""
    jetson = "fake"
    rtx = "fake-rtx"
    exp = "/fake/exp"

    def __init__(self, dry=False, fail_on=(), outputs=None):
        self.dry, self.fail_on, self.outputs, self.calls = dry, fail_on, outputs or {}, []

    def ssh(self, cmd, step, check=True, **kw):
        self.calls.append((step, cmd))
        for key, out in self.outputs.items():
            if key in step:
                return out
        if any(f in step for f in self.fail_on):
            if check:
                raise Fail(step, "boom")
            return CP(1, "", "boom")
        return CP(0, "FREE_USED_MB 900")

    rtx_ssh = ssh

    def tmux_windows(self, session):
        return ["go2", "camera", "face", "vision", "executive", "tts", "llm", "camtf", "depth_safety", "fox",
                "object", "gateway"]

    def run(self, argv, step, **kw):
        return self.ssh(" ".join(argv), step, **kw)


def fault_cfg(sudo=False):
    cfg = base_cfg()
    cfg["jetson"]["sudo"] = sudo
    return cfg


class FaultTests(unittest.TestCase):
    def setUp(self):
        importlib.reload(faults)
        offline_ops()
        self.cfg = fault_cfg()
        faults.save_state(self.cfg, [])

    def test_partial_apply_is_persisted_and_revertable(self):
        faults.wait_pane = lambda *a, **k: (_ for _ in ()).throw(Fail("wait_pane", "timeout"))
        with self.assertRaises(Fail):
            faults.apply(FaultRunner(), self.cfg, "llm_primary_bad")
        st = faults.load_state(self.cfg)
        self.assertEqual([(e["name"], e["status"]) for e in st], [("llm_primary_bad", "partial")])
        faults.wait_pane = lambda *a, **k: ""
        r = FaultRunner()
        faults.revert(r, self.cfg, "all")
        self.assertEqual(faults.load_state(self.cfg), [])
        self.assertTrue(any("llm_window.sh" in c for _, c in r.calls), "partial 也要真的跑 revert")

    def test_dry_revert_does_not_touch_real_state(self):
        faults.wait_pane = lambda *a, **k: ""
        faults.apply(FaultRunner(), self.cfg, "llm_both_bad")          # 真 entry
        faults.apply(FaultRunner(dry=True), self.cfg, "offline_mode")  # dry entry
        faults.revert(FaultRunner(dry=True), self.cfg, "all")
        self.assertEqual([(e["name"], e["dry_run"]) for e in faults.load_state(self.cfg)], [("llm_both_bad", False)])

    def test_down_cleans_first_even_if_revert_fails_and_keeps_pending(self):
        cfg = fault_cfg(sudo=True)
        faults.save_state(cfg, [{"name": "cloud_refused", "status": "active", "scope": "system", "dry_run": False,
                                 "applied_at": 0, "one_way": False, "sudo": True, "items": None, "revert": []},
                                {"name": "llm_both_bad", "status": "active", "scope": "demo", "dry_run": False,
                                 "applied_at": 0, "one_way": False, "sudo": False, "items": None, "revert": []}])
        r = FaultRunner(fail_on=("fault revert cloud_refused",),
                        outputs={"ollama ps": CP(0, '{"models":[]}')})
        with self.assertRaises(Fail) as cm:
            ops.down(r, cfg, types.SimpleNamespace(force=True))
        steps = [st for st, _ in r.calls]
        self.assertEqual(cm.exception.step, "down: pending")
        self.assertLess(steps.index("down: cleanup"), next(i for i, st in enumerate(steps) if "fault revert" in st))
        st = faults.load_state(cfg)
        self.assertEqual([(e["name"], e["status"]) for e in st], [("cloud_refused", "pending_revert")])

    def test_emergency_down_skips_sync_and_cleans_first(self):
        faults.save_state(self.cfg, [])
        r = FaultRunner(outputs={"ollama ps": CP(0, '{"models":[]}')})
        ops.emergency_down(r, self.cfg)
        steps = [st for st, _ in r.calls]
        self.assertEqual(steps[0], "down: cleanup", "緊急停止第一步就是清理，不先同步腳本")
        self.assertFalse(any("sync" in st for st in steps))

    def test_emergency_down_failure_is_raised(self):
        faults.save_state(self.cfg, [])
        r = FaultRunner(fail_on=("down: cleanup",), outputs={"ollama ps": CP(255, "")})
        with self.assertRaises(Fail):
            ops.emergency_down(r, self.cfg)


class SudoRevertTests(unittest.TestCase):
    """用 PATH 上的 fake sudo／iptables 真的執行 plan 產生的 shell 指令。"""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="sudo-", dir=TMP))
        (self.d / "sudo").write_text('#!/bin/sh\n[ "$1" = -n ] && shift\nexec "$@"\n')
        self.rules = self.d / "rules.json"
        (self.d / "iptables").write_text("#!" + sys.executable + """
import sys, json, pathlib
import os
if os.environ.get("IPT_FAIL"): sys.exit(int(os.environ["IPT_FAIL"]))
p = pathlib.Path(%r); rules = json.loads(p.read_text())
op, spec = sys.argv[1], " ".join(sys.argv[2:])
if op == "-S": print("\\n".join(rules))
elif op == "-C": sys.exit(0 if spec in rules else 1)
elif op == "-I": rules.insert(0, spec)
elif op == "-D":
    if spec not in rules: sys.exit(1)
    rules.remove(spec)
p.write_text(json.dumps(rules))
""" % str(self.rules))
        for f in ("sudo", "iptables"):
            (self.d / f).chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.d}:{os.environ['PATH']}")

    def sh(self, cmd):
        return subprocess.run(["bash", "-c", cmd], env=self.env, capture_output=True, text=True)

    def test_iptables_revert_only_removes_own_rules(self):
        other = "OUTPUT -p tcp -d 203.0.113.9 --dport 443 -j DROP"
        self.rules.write_text(json.dumps([other]))
        p = faults.plan({"jetson": {"sudo": True}}, "cloud_timeout", ["192.0.2.2", "192.0.2.3"])
        for _, cmd in p["apply"]:
            self.assertEqual(self.sh(cmd).returncode, 0)
        self.assertEqual(len(json.loads(self.rules.read_text())), 3)
        for _, cmd in p["revert"]:
            self.assertEqual(self.sh(cmd).returncode, 0, cmd)
        self.assertEqual(json.loads(self.rules.read_text()), [other])

    def test_hosts_revert_exact_lines_only(self):
        hosts = self.d / "hosts"
        orig = "127.0.0.1 localhost\n192.0.2.7 openrouter.ai # admin override\n192.0.2.9 openrouterXai\n127.0.0.1 openrouter.ai\n"
        hosts.write_text(orig)
        p = faults.plan({"jetson": {"sudo": True}}, "tts_cloud_refused")
        for _, cmd in p["apply"]:
            self.assertEqual(self.sh(cmd.replace("/etc/hosts", str(hosts))).returncode, 0)
        self.assertIn("# pawexp:tts_cloud_refused", hosts.read_text())
        for _, cmd in p["revert"]:
            r = self.sh(cmd.replace("/etc/hosts", str(hosts)).replace("sed -i ", "sed -i '' " if sys.platform == "darwin" else "sed -i "))
            self.assertEqual(r.returncode, 0, (cmd, r.stderr))
        self.assertEqual(hosts.read_text(), orig)


ANALYSIS = REPO / "analysis"


def gen_fixtures():
    """在暫存目錄生成 fixtures（不改 repo），回傳目錄。"""
    d = TMP / "fixtures"
    if not d.exists():
        d.mkdir()
        src = (ANALYSIS / "fixtures" / "make_fixtures.py").read_text(encoding="utf-8")
        (d / "make_fixtures.py").write_text(src, encoding="utf-8")
        subprocess.run([sys.executable, "-I", str(d / "make_fixtures.py")], check=True, capture_output=True,
                       env=dict(os.environ, TZ="Asia/Taipei"))
    return d


def analyze(exp, run_dir, *extra):
    out = TMP / f"out-{exp}-{abs(hash((str(run_dir),) + extra))}"
    p = subprocess.run([sys.executable, "-I", str(ANALYSIS / f"analyze_{exp}.py"), str(run_dir), str(out), *extra],
                       capture_output=True, text=True)
    s = json.loads((out / "summary.json").read_text()) if (out / "summary.json").exists() else None
    return p.returncode, s, p.stderr


class P1Tests(unittest.TestCase):
    def test_down_reports_ollama_ssh_failure(self):
        offline_ops()
        importlib.reload(faults)
        cfg = fault_cfg()
        faults.save_state(cfg, [])
        r = FaultRunner(outputs={"ollama ps": CP(255, "", "ssh: connect timed out")})
        with self.assertRaises(Fail) as cm:
            ops.down(r, cfg, types.SimpleNamespace(force=False))
        self.assertIn("ollama", cm.exception.detail.lower())

    def test_llm_bench_wrapper_propagates_child_failure(self):
        d = Path(tempfile.mkdtemp(prefix="bench-", dir=TMP))
        b = d / "bin"
        b.mkdir()
        sh = (HARNESS / "jetson" / "run_llm_bench.sh").read_text().replace(". ~/elder_and_dog/.env", ". /dev/null")
        (d / "run.sh").write_text(sh)
        for name, body in {"python3": "exit 3", "nvpmodel": "exit 0", "free": "echo mem", "ollama": "exit 0",
                           "tegrastats": "exit 0"}.items():
            (b / name).write_text(f"#!/bin/sh\n{body}\n")
            (b / name).chmod(0o755)
        p = subprocess.run(["bash", str(d / "run.sh"), str(d / "data"), "0", "", "openrouter", "x"],
                           env=dict(os.environ, PATH=f"{b}:{os.environ['PATH']}"), capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("rc=3", (d / "data" / "done.txt").read_text())

    def test_record_stop_fails_on_missing_collection(self):
        offline_ops()
        cfg = base_cfg()
        d = core.run_dir(cfg, "t-missing")
        core.write_json(d / "meta_start.json", {"run": "t-missing", "t_start": 100, "clock": {"offset_s": 0, "rtt_s": 0}})

        class R(FaultRunner):
            def jetson_time(self):
                return 200.0

            def rsync_back(self, *a, **k):
                return CP()
        with self.assertRaises(Fail) as cm:
            ops.record_stop(R(), cfg, types.SimpleNamespace(run="t-missing"))
        self.assertIn("topics.jsonl", cm.exception.detail)
        self.assertIn("topics.jsonl", json.loads((d / "meta.json").read_text())["missing"])

    def test_e1_missing_inputs_is_undetermined(self):
        import shutil
        e1 = TMP / "e1-missing"
        shutil.copytree(gen_fixtures() / "E1", e1)
        (e1 / "hz.jsonl").unlink()
        (e1 / "panes_end.txt").unlink()
        rc, s, _ = analyze("e1", e1)
        self.assertEqual(rc, 1)
        self.assertFalse(s["valid"])
        self.assertIsNone(s["stability"]["stable"])


class MeasurementTests(unittest.TestCase):
    def test_fixtures_regenerate_and_match(self):
        p = subprocess.run([sys.executable, "-I", str(ANALYSIS / "fixtures" / "run_fixtures.py")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_e3_missing_topics_is_invalid_not_zero_canned(self):
        d = TMP / "e3-missing"
        d.mkdir(exist_ok=True)
        (d / "play.jsonl").write_text(json.dumps({"id": "c01", "pass": 1, "t_send": 101,
                                                  "reply": {"published": True, "latency_ms": 100}}) + "\n")
        core.write_json(d / "meta.json", {"clock_offset_s": 0.0})
        rc, s, _ = analyze("e3", d, "--mode", "acceptance")
        self.assertEqual(rc, 1)
        self.assertFalse(s["valid"])
        self.assertIsNone(s["canned_rate"])

    def test_e3_policy_gate_uses_arrival_time(self):
        rc, s, _ = analyze("e3", gen_fixtures() / "E3", "--mode", "acceptance")
        self.assertAlmostEqual(s["segments"]["t3-t2"]["median"], 0.05, places=3)   # 到達差
        self.assertAlmostEqual(s["policy_gate_src_ms"]["median"], 449.0, places=1)  # created_at 差另列

    def test_e3_mic_onset_not_borrowed_from_next_utterance(self):
        rc, s, _ = analyze("e3", gen_fixtures() / "E3", "--mode", "acceptance")
        self.assertIsNone(s["rows"][1].get("t9-t0"))
        self.assertEqual(s["segments"]["t9-t0"]["n"], 4)

    def test_e5_per_injection_target_and_repeat(self):
        rc, s, _ = analyze("e5", gen_fixtures() / "E5")
        ev = {(e["id"], e["kind"]): e for e in s["events"]}
        self.assertTrue(ev[("C1-02", "candidate")]["false_block"], "chat_reply 被接受不能抵目標技能被擋")
        self.assertTrue(ev[("C3-01", "repeat_candidate")]["out_of_policy_dispatch"])
        for v in s["trace_completeness"].values():
            self.assertTrue(v["rate"] is None or v["rate"] <= 1.0)

    def test_e2a_requires_opencc_and_reports_intent_retention(self):
        d = TMP / "e2a"
        d.mkdir(exist_ok=True)
        rows = [{"tier": "sv_local", "pass": 1, "id": "c01", "ref": "今天天氣怎麼樣", "ok": True, "hyp": "今天天气怎么样",
                 "latency_ms": 100, "audio_s": 2.0},
                {"tier": "sv_local", "pass": 1, "id": "k05", "ref": "站起來", "ok": True, "hyp": "沾起來",
                 "latency_ms": 120, "audio_s": 1.0}]
        (d / "asr.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        rc, _, err = analyze("e2a", d, "--mode", "acceptance")
        if importlib.util.find_spec("opencc") is None:
            self.assertEqual(rc, 2)
            self.assertIn("opencc-python-reimplemented", err)
        # in-process：以 stub OpenCC（只轉這三個字）驗證正規化與意圖保留率
        stub = types.ModuleType("opencc")
        stub.OpenCC = lambda cfg: types.SimpleNamespace(convert=lambda t: t.replace("气", "氣").replace("么", "麼").replace("样", "樣"))
        sys.modules["opencc"] = stub
        try:
            spec = importlib.util.spec_from_file_location("e2a_t", ANALYSIS / "analyze_e2a.py")
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            m.C._CC = None
            m.main(str(d), str(TMP / "e2a-out"), "acceptance")
        finally:
            del sys.modules["opencc"]
        t = json.loads((TMP / "e2a-out" / "summary.json").read_text())["tiers"]["sv_local"]
        self.assertEqual(t["sentence_acc"], 0.5)
        self.assertAlmostEqual(t["cer_micro"], 1 / 10)
        self.assertEqual(t["intent_retention"], 0.5)   # 「站起來」→ stand；「沾起來」不是


class P2Tests(unittest.TestCase):
    def _bench(self, windows, state):
        from pawexp import bench
        offline_ops()
        importlib.reload(bench)
        cfg = base_cfg()
        core.write_json(ops.state_path(cfg), state) if state is not None else ops.state_path(cfg).unlink(missing_ok=True)
        ops.sync_scripts = lambda r: None
        ops.clock = lambda *a, **k: {"offset_s": 0.012, "rtt_s": 0.008}
        bench.launch = lambda *a, **k: 0
        texts = TMP / "texts.json"
        texts.write_text(json.dumps([{"id": "x", "text": "hello"}]))

        class R(FaultRunner):
            def jetson_time(self):
                return 123.0

            def scp(self, *a, **k):
                return CP()

            def rsync_back(self, *a, **k):
                return CP()

            def tmux_windows(self, s):
                return windows
        run = f"t-bench-{len(windows)}-{bool(state)}"
        a = types.SimpleNamespace(run=run, what="tts", texts=str(texts), limit=None, tiers="piper")
        try:
            bench.bench(R(), cfg, a)
            err = None
        except Fail as e:
            err = e
        return json.loads((core.run_dir(cfg, run) / "meta.json").read_text()), err

    def test_bench_writes_run_meta(self):
        meta, err = self._bench(["go2", "tts", "gateway"], {"config": "A", "kill": ["asr"]})
        self.assertIsNone(err)
        self.assertEqual((meta["kind"], meta["config"], meta["kill"], meta["clock_offset_s"], meta["clock_rtt_s"]),
                         ("bench tts", "A", ["asr"], 0.012, 0.008))
        self.assertEqual(meta["missing_fields"], [])

    def test_bench_meta_demo_off_is_explicit(self):
        meta, err = self._bench([], {"config": "A", "kill": ["asr"]})   # 舊 state 不能沿用
        self.assertIsNone(err)
        self.assertEqual((meta["config"], meta["kill"], meta["demo_windows"]), ("demo_off", [], []))

    def test_bench_meta_missing_config_fails(self):
        meta, err = self._bench(["go2", "tts"], None)   # demo 在跑但沒有 up 的 state
        self.assertIsNotNone(err)
        self.assertIn("config", meta["missing_fields"])

    def test_play_clears_tts_cache_every_pass_and_snapshots(self):
        importlib.reload(play)
        ops.clock = lambda *a, **k: {"offset_s": 0.0, "rtt_s": 0.0}
        ops.emergency_down = lambda r, cfg: None
        r = FakeTail()
        play.send_one = lambda *a: (time.time(), time.time(), {"published": False})
        play.play(r, base_cfg(), play_args(passes=2, snapshot=True))
        self.assertEqual(sum("清 TTS 快取" in c for c in r.calls), 2)
        rows = [json.loads(l) for l in (core.run_dir(base_cfg(), "t-play") / "play.jsonl").read_text().splitlines()]
        self.assertTrue(all(row.get("snapshot") == "ok" for row in rows[-2:]))

    def test_c7_every_flag_case_resets_and_confirms(self):
        cases = json.loads((REPO / "cases/e5_cases.json").read_text(encoding="utf-8"))["cases"]
        flagged = [c for c in cases if c.get("world")]
        self.assertEqual(len(flagged), 9)
        self.assertTrue(all(c["world_reset"] and c["reset_confirm"] for c in flagged))
        m, w = load_inject(type_after=0)
        rc = m.run([flagged[0]], str(TMP / "inj-c7.jsonl"), False, 0.1)
        self.assertEqual(rc, 0)
        kinds = [p["kind"] for p in json.loads((TMP / "inj-c7.jsonl").read_text().splitlines()[-1])["publishes"]]
        self.assertEqual(kinds[-3:], ["world_reset", "reset_confirm_speech", "reset_confirm_candidate"])


class AfterReviewTests(unittest.TestCase):
    def test_record_stop_does_not_require_pane_of_killed_window(self):
        offline_ops()
        cfg = base_cfg()
        d = core.run_dir(cfg, "t-nollm")
        core.write_json(d / "meta_start.json", {"run": "t-nollm", "t_start": 100, "clock": {"offset_s": 0, "rtt_s": 0}})
        for f in ops.RECORD_REQUIRED:
            if f != "pane_llm.log":
                (d / f).write_text("x\n")
        (d / "pane_llm.log").write_text("")

        class R(FaultRunner):
            def jetson_time(self):
                return 200.0

            def rsync_back(self, *a, **k):
                return CP()

            def tmux_windows(self, s):
                return ["go2", "camera", "tts", "gateway"]   # 沒有 llm
        self.assertEqual(ops.record_stop(R(), cfg, types.SimpleNamespace(run="t-nollm")), 0)
        meta = json.loads((d / "meta.json").read_text())
        self.assertNotIn("pane_llm.log", meta["required"])
        self.assertEqual(meta["missing"], [])

    def test_watchdog_blind_stops_play(self):
        importlib.reload(play)
        ops.clock = lambda *a, **k: {"offset_s": 0.0, "rtt_s": 0.0}
        ops.emergency_down = lambda r, cfg: self.fail("失明不是動作證據，不該 down")

        class Blind(FakeTail):
            def ssh(self, cmd, step, **kw):
                if cmd.startswith("tail -c"):
                    raise Fail(step, "ssh: connect timed out")
                return CP()

        def send(*a):
            time.sleep(0.5)
            return time.time(), time.time(), {"published": False}
        play.send_one = send
        with self.assertRaises(Fail) as cm:
            play.play(Blind(), base_cfg(), play_args(limit=3))
        self.assertIn("blind", cm.exception.step)


class SelfMatchTests(unittest.TestCase):
    """主腦 18:57 實機 bug：ssh 遠端 zsh -c 的指令列含有目標字串，pgrep -f 比對到自己 → 誤判。用本機 zsh -c 重現。"""

    def zsh(self, cmd):
        # macOS 的 pgrep 預設排除自己的祖先程序；加 -a 才會像 Jetson（Linux procps）一樣比對到父 zsh -c
        if sys.platform == "darwin":
            cmd = cmd.replace("pgrep -f", "pgrep -a -f")
        return subprocess.run(["zsh", "-c", cmd], capture_output=True, text=True)

    def test_naive_pattern_matches_its_own_shell(self):
        # 對照組：舊寫法在「沒有任何目標程序」時仍回 rc=1（比對到 zsh -c 自己的指令列）
        self.assertEqual(self.zsh("true; ! pgrep -f pawexp_selfmatch_probe").returncode, 1)

    def test_asr_node_off_check_ignores_own_command_line(self):
        cmd = faults.plan({"jetson": {}}, "asr_node_off")["apply"][0][1]
        self.assertIn("[s]tt_intent_node", cmd)
        check = "true; " + cmd.split("sleep 2; ", 1)[1]   # 換掉 tmux／sleep，只留 pgrep 檢查
        # 換成測試專用的唯一名稱（結構不變）：macOS 的 -a 會把測試執行環境的所有祖先也算進來
        check = check.replace("[s]tt_intent_node", "[p]awexp_selfmatch_stt")
        self.assertIn("awexp_selfmatch_stt", check)        # 遠端指令列含有目標字串（首字包在 [] 裡）
        self.assertEqual(self.zsh(check).returncode, 0, "沒有 stt_intent_node 時必須判定已關閉")

    def test_bracket_pattern_still_detects_real_process(self):
        name = "pawexp_selfmatch_decoy"
        decoy = subprocess.Popen(["bash", "-c", f"exec -a {name} sleep 30"])
        try:
            time.sleep(0.3)
            r = self.zsh(f"true; ! pgrep -f '{core.proc_pat(name)}'")
            self.assertEqual(r.returncode, 1, "真的有程序在跑時必須偵測得到")
        finally:
            decoy.kill()

    def test_all_remote_pgrep_pkill_use_bracket_pattern(self):
        import re
        texts = [ops.inline_cleanup_cmd(), faults.plan({"jetson": {}}, "asr_node_off")["apply"][0][1],
                 (HARNESS / "pawexp" / "ops.py").read_text(encoding="utf-8")]
        for t in texts:
            for m in re.finditer(r"p(?:grep|kill)(?: -9)? -f '([^']+)'", t):
                self.assertTrue(m.group(1).startswith("[") or m.group(1).startswith("{proc_pat"), m.group(0))
        sh = (HARNESS / "jetson" / "cleanup_exp.sh").read_text(encoding="utf-8")
        self.assertNotRegex(sh, r'pkill -9 -f "\$p"')
        fn = re.search(r"^pat\(\).*$", sh, re.M).group(0)
        out = subprocess.run(["bash", "-c", f"{fn}\npat stt_intent_node; pat 'ros2 launch'"], capture_output=True, text=True)
        self.assertEqual(out.stdout.split("\n")[:2], ["[s]tt_intent_node", "[r]os2 launch"])

    def test_inline_cleanup_kills_target_without_killing_itself(self):
        name = "pawexp_selfmatch_victim"
        decoy = subprocess.Popen(["bash", "-c", f"exec -a {name} sleep 30"])
        try:
            time.sleep(0.3)
            r = self.zsh(f"pkill -9 -f '{core.proc_pat(name)}' 2>/dev/null; sleep 0.3; echo alive")
            self.assertIn("alive", r.stdout, "pkill 不能殺到自己這條 shell")
            self.assertIsNotNone(decoy.poll(), "目標程序必須被清掉")
        finally:
            if decoy.poll() is None:
                decoy.kill()


class ReReviewSafetyTests(unittest.TestCase):
    def setUp(self):
        importlib.reload(faults)
        offline_ops()
        importlib.reload(play)
        ops.clock = lambda *a, **k: {"offset_s": 0.0, "rtt_s": 0.0}
        faults.save_state(base_cfg(), [])

    def _stalling_runner(self):
        class R(FakeTail):
            def ssh(self, cmd, step, **kw):
                if step.startswith("sync"):
                    self.calls.append(step)
                    threading.Event().wait(60)   # 模擬同步卡住
                if step == "down: ollama ps" or "ollama ps" in step:
                    self.calls.append(step)
                    return CP(0, '{"models":[]}')
                return super().ssh(cmd, step, **kw)

            def run(self, *a, **k):
                return CP()
        return R()

    def test_f1_emergency_stop_not_blocked_by_sync_and_awaited(self):
        r = self._stalling_runner()

        def send(*a):
            r.add({"topic": "/cmd_vel", "t_recv": time.time(), "data": None})
            time.sleep(0.2)
            return time.time(), time.time(), {"published": False}
        play.send_one = send
        t0 = time.monotonic()
        with self.assertRaises(Fail) as cm:
            play.play(r, base_cfg(), play_args())
        self.assertEqual(cm.exception.code, 4)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertIn("down: cleanup", r.calls, "主程序返回前，緊急停止的清理必須已經執行")
        self.assertFalse(any(c.startswith("sync") for c in r.calls))
        self.assertIn("已 down", cm.exception.detail)

    def test_f1_emergency_stop_failure_reported(self):
        r = FakeTail()
        ops.emergency_down = lambda *a: (_ for _ in ()).throw(Fail("down: cleanup", "ssh 斷線"))

        def send(*a):
            r.add({"topic": "/cmd_vel", "t_recv": time.time(), "data": None})
            time.sleep(0.2)
            return time.time(), time.time(), {"published": False}
        play.send_one = send
        with self.assertRaises(Fail) as cm:
            play.play(r, base_cfg(), play_args())
        self.assertEqual(cm.exception.code, 4)
        self.assertIn("緊急停止失敗", cm.exception.detail)

    def test_f1_stop_ok_with_pending_revert_not_reported_as_failure(self):
        cfg = fault_cfg(sudo=True)
        faults.save_state(cfg, [{"name": "cloud_refused", "status": "active", "scope": "system", "dry_run": False,
                                 "applied_at": 0, "one_way": False, "sudo": True, "items": None, "revert": []}])

        class R(FakeTail):
            def ssh(self, cmd, step, **kw):
                if "ollama ps" in step:
                    return CP(0, '{"models":[]}')
                if step.startswith("fault revert"):
                    raise Fail(step, "sudo -n 不可用")
                return super().ssh(cmd, step, **kw)
        r = R()

        def send(*a):
            r.add({"topic": "/cmd_vel", "t_recv": time.time(), "data": None})
            time.sleep(0.2)
            return time.time(), time.time(), {"published": False}
        play.send_one = send
        with self.assertRaises(Fail) as cm:
            play.play(r, cfg, play_args())
        self.assertEqual(cm.exception.code, 4)
        self.assertIn("Jetson 已清乾淨", cm.exception.detail)
        self.assertNotIn("緊急停止失敗", cm.exception.detail)
        faults.save_state(cfg, [])

    def test_f2_violation_while_waiting_for_enter_blocks_dispatch(self):
        m, w = load_inject(type_after=0)
        node_cls = sys.modules["rclpy.node"].Node
        callbacks = {}
        orig_sub = node_cls.create_subscription
        node_cls.create_subscription = lambda self, cls, topic, cb, q: (callbacks.__setitem__(topic, cb),
                                                                         orig_sub(self, cls, topic, cb, q))[-1]
        rclpy = sys.modules["rclpy"]
        orig_spin, pending = rclpy.spin_once, []

        def spin(*a, **k):
            orig_spin(*a, **k)
            if pending:
                callbacks["/cmd_vel"](object())
                pending.clear()
        rclpy.spin_once = spin

        class Input:
            def readline(self):
                pending.append("cmd_vel 在等 Enter 時到達")
                return "\n"
        old = sys.stdin
        sys.stdin = Input()
        try:
            with self.assertRaises(SystemExit) as cm:
                m.run([dict(CASE, needs_roy=True, skill_request={"skill": "wave_hello"})], str(TMP / "f2.jsonl"), True, 0.1)
        finally:
            sys.stdin = old
        self.assertEqual(cm.exception.code, 4)
        self.assertFalse([t for t, _ in w.published if t == "/brain/skill_request"], "違規後不得派送")


class ReReviewFaultTests(unittest.TestCase):
    def setUp(self):
        importlib.reload(faults)
        offline_ops()
        self.cfg = fault_cfg(sudo=True)
        faults.save_state(self.cfg, [])

    def test_f3_legacy_entry_kept_and_never_auto_reverted(self):
        faults.save_state(self.cfg, [{"name": "cloud_refused", "applied_at": 0, "one_way": False, "sudo": True,
                                      "revert": ["ssh jetson \"sudo -n sed -i -e '/openrouter.ai/d' /etc/hosts\""]}])
        r = FaultRunner()
        with self.assertRaises(Fail):
            faults.revert(r, self.cfg, "all")
        self.assertEqual(r.calls, [], "舊格式不得自動執行任何復原")
        st = faults.load_state(self.cfg)
        self.assertEqual([(e["name"], e["status"]) for e in st], [("cloud_refused", "legacy_manual")])
        r = FaultRunner(outputs={"ollama ps": CP(0, '{"models":[]}')})
        with self.assertRaises(Fail) as cm:
            ops.down(r, self.cfg, types.SimpleNamespace(force=True))
        self.assertEqual(cm.exception.step, "down: pending")
        self.assertEqual(len(faults.load_state(self.cfg)), 1)
        faults.ack(self.cfg, "cloud_refused")
        self.assertEqual(faults.load_state(self.cfg), [])

    def test_f4_failed_cleanup_keeps_demo_fault_pending(self):
        faults.save_state(self.cfg, [{"name": "llm_both_bad", "status": "active", "scope": "demo", "dry_run": False,
                                      "applied_at": 0, "one_way": False, "sudo": False, "items": None, "revert": []}])
        r = FaultRunner(fail_on=("down: cleanup",), outputs={"ollama ps": CP(0, '{"models":[]}')})
        with self.assertRaises(Fail):
            ops.down(r, self.cfg, types.SimpleNamespace(force=True))
        self.assertEqual([(e["name"], e["status"]) for e in faults.load_state(self.cfg)], [("llm_both_bad", "pending_revert")])


class ReReviewIptablesTests(SudoRevertTests):
    def run_steps(self, steps, env):
        for _, cmd in steps:
            p = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
            if p.returncode:
                raise Fail("step", f"rc={p.returncode} {p.stderr.strip()}")

    def test_f5_query_failure_keeps_rule_and_journal(self):
        importlib.reload(faults)
        cfg = fault_cfg(sudo=True)
        self.rules.write_text(json.dumps([]))
        p = faults.plan({"jetson": {"sudo": True}}, "cloud_timeout", ["192.0.2.2"])
        self.run_steps(p["apply"], self.env)
        before = json.loads(self.rules.read_text())
        self.assertEqual(len(before), 1)
        faults.save_state(cfg, [{"name": "cloud_timeout", "status": "active", "scope": "system", "dry_run": False,
                                 "applied_at": 0, "one_way": False, "sudo": True, "items": ["192.0.2.2"], "revert": []}])
        env = dict(self.env, IPT_FAIL="4")
        test = self

        class Runner:
            dry = False

            def ssh(self, cmd, step, **kw):
                r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
                if r.returncode:
                    raise Fail(step, f"rc={r.returncode} {r.stderr.strip()}")
                return r
        with self.assertRaises(Fail):
            faults.revert(Runner(), cfg, "all")
        self.assertEqual(json.loads(self.rules.read_text()), before, "查詢失敗時規則必須還在")
        self.assertEqual([(e["name"], e["status"]) for e in faults.load_state(cfg)], [("cloud_timeout", "pending_revert")])

    def test_f5_sudo_unavailable_is_not_rule_absent(self):
        (self.d / "sudo").write_text('#!/bin/sh\n[ "$2" = true ] && exit 1\n[ "$1" = -n ] && shift\nexec "$@"\n')
        p = faults.plan({"jetson": {"sudo": True}}, "cloud_timeout", ["192.0.2.2"])
        r = subprocess.run(["bash", "-c", p["revert"][-1][1]], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)


def load_analysis(name):
    spec = importlib.util.spec_from_file_location(name + "_t", ANALYSIS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ev(t, topic, **data):
    return {"t_recv": t, "topic": "/brain/" + topic, "data": data}


class ReReviewAnalysisTests(unittest.TestCase):
    """複審時的重現案例。"""

    def setUp(self):
        self.e5 = load_analysis("analyze_e5")
        self.cases = {c["id"]: c for c in json.loads(self.e5.DEFAULT_CASES.read_text())["cases"]}

    def test_f6_wrong_skill_dispatch_is_violation(self):
        rows = [ev(100.4, "proposal", plan_id="p", selected_skill="wave_hello"),
                ev(100.5, "skill_result", plan_id="p", status="ACCEPTED"),
                ev(100.6, "skill_result", plan_id="p", status="STEP_STARTED", detail="motion")]
        e = self.e5.evaluate(self.cases["C2-01"], "candidate", {"t": 100.3, "session_id": "s"}, 160, rows)
        self.assertTrue(e["out_of_policy_dispatch"])

    def test_f6_crossplan_not_conflated(self):
        rows = [ev(100.4, "proposal", plan_id="p1", selected_skill="dance"),
                ev(100.5, "skill_result", plan_id="p1", status="ACCEPTED"),
                ev(100.6, "proposal", plan_id="p2", selected_skill="dance"),
                ev(100.7, "skill_result", plan_id="p2", status="STEP_STARTED", detail="motion")]
        e = self.e5.evaluate(self.cases["C2-01"], "candidate", {"t": 100.3, "session_id": "s"}, 160, rows)
        self.assertFalse(e["out_of_policy_dispatch"])
        self.assertEqual(e["motion_without_accept"], 1)

    def test_f6_false_block_by_target_only(self):
        rows = [ev(100.4, "proposal", plan_id="chat", selected_skill="chat_reply"),
                ev(100.5, "skill_result", plan_id="chat", status="ACCEPTED"),
                ev(100.6, "proposal", plan_id="m", selected_skill="stand"),
                ev(100.7, "skill_result", plan_id="m", status="ACCEPTED")]
        e = self.e5.evaluate(self.cases["C1-01"], "candidate", {"t": 100.3, "session_id": "s"}, 160, rows)
        self.assertTrue(e["false_block"], "目標 wave_hello 沒被接受；別的技能被接受不能抵")

    def test_f7_t7_not_borrowed_from_next_utterance(self):
        e3 = load_analysis("analyze_e3")
        sp = {"t_recv": 100.1, "topic": "/event/speech_intent_recognized", "data": {"session_id": "s"}}
        rows = [sp, ev(100.2, "proposal", session_id="s", plan_id="c", selected_skill="chat_reply"),
                {"t_recv": 100.3, "topic": "/tts", "data": "x"}]
        row = e3.per_utterance({"t_send": 100, "id": "x", "reply": {"published": True}}, rows, 0, [], [(115.0, "")], 110)
        self.assertIsNone(row.get("t7"))

    def test_f9_t8_uses_timestamp_and_primary_skill_plan(self):
        e3 = load_analysis("analyze_e3")
        sp = {"t_recv": 100.1, "topic": "/event/speech_intent_recognized", "data": {"session_id": "s"}}
        rows = [sp, ev(100.2, "proposal", session_id="s", plan_id="m", selected_skill="stand"),
                ev(100.5, "skill_result", plan_id="m", status="STEP_STARTED", detail="motion", timestamp=100.4)]
        row = e3.per_utterance({"t_send": 100, "id": "x", "reply": {"published": True}}, rows, 0, [], [], 110)
        self.assertEqual(row.get("t8"), 100.4)

    def test_f10_e1_missing_segment_is_invalid(self):
        import shutil
        d = TMP / "partial-e1"
        shutil.copytree(gen_fixtures() / "E1", d, dirs_exist_ok=True)
        lines = (d / "tegrastats.log").read_text().splitlines()
        (d / "tegrastats.log").write_text("\n".join(lines[:100]) + "\n")   # console 段整段沒有資源樣本
        rc, s, _ = analyze("e1", d)
        self.assertEqual(rc, 1)
        self.assertFalse(s["valid"])
        self.assertIsNone(s["stability"]["stable"])
        self.assertIn("console", s["input_problems"]["coverage"])

    def test_f10_e3_only_state_messages_is_invalid(self):
        import shutil
        d = TMP / "partial-e3"
        shutil.copytree(gen_fixtures() / "E3", d, dirs_exist_ok=True)
        (d / "topics.jsonl").write_text(json.dumps({"topic": "/state/tts_playing", "t_recv": 1791370800.0,
                                                    "data": {"data": False}}) + "\n")
        rc, s, _ = analyze("e3", d, "--mode", "acceptance")
        self.assertEqual(rc, 1)
        self.assertFalse(s["valid"])
        self.assertIsNone(s["canned_rate"])


VENV_PY = Path(os.environ.get("PAWEXP_OPENCC_PYTHON", str(REPO / ".venv" / "bin" / "python")))   # 裝有 opencc-python-reimplemented 的 Python


@unittest.skipUnless(VENV_PY.exists(), "沒有裝 OpenCC 的 .venv")
class E2aEndToEndTests(unittest.TestCase):
    def test_e2a_real_s2twp_numbers(self):
        d = TMP / "e2a-e2e"
        d.mkdir(exist_ok=True)
        rows = [("c01", "今天天氣怎麼樣", "今天天气怎么样？"),   # s2twp 後完全相同（標點去掉）
                ("c02", "你喜歡什麼顏色", "你喜什麼色"),        # 刪 2 字
                ("k05", "站起來", "站起来"),                    # 簡轉繁後相同；意圖 stand
                ("k04", "請你坐下", "请你做下")]                # 替換 1 字；意圖 sit → chat（不保留）
        (d / "asr.jsonl").write_text("".join(json.dumps({"tier": "sv_local", "pass": 1, "id": i, "ref": ref, "ok": True,
                                                          "hyp": hyp, "latency_ms": 100 + 10 * k, "audio_s": 2.0},
                                                         ensure_ascii=False) + "\n"
                                              for k, (i, ref, hyp) in enumerate(rows)), encoding="utf-8")
        out = TMP / "e2a-e2e-out"
        p = subprocess.run([str(VENV_PY), "-I", str(ANALYSIS / "analyze_e2a.py"), str(d), str(out), "--mode", "acceptance"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        t = json.loads((out / "summary.json").read_text())["tiers"]["sv_local"]
        self.assertEqual(t["n"], 4)
        self.assertAlmostEqual(t["cer_micro"], round(3 / 21, 3))            # (0+2+0+1)/(7+7+3+4)
        self.assertAlmostEqual(t["cer_per_sentence_mean"], round((2 / 7 + 1 / 4) / 4, 3))
        self.assertEqual(t["sentence_acc"], 0.5)
        self.assertEqual(t["intent_retention"], 0.75)
        self.assertAlmostEqual(t["latency_s"]["median"], 0.115)
        # formal 模式：前 3 句是暖機，只剩 1 句
        p = subprocess.run([str(VENV_PY), "-I", str(ANALYSIS / "analyze_e2a.py"), str(d), str(out)], capture_output=True, text=True)
        self.assertEqual(json.loads((out / "summary.json").read_text())["tiers"]["sv_local"]["n"], 1)


class WaiveTests(unittest.TestCase):
    def _copy(self, name):
        import shutil
        d = TMP / name
        shutil.copytree(gen_fixtures() / "E1", d, dirs_exist_ok=True)
        (d / "free.log").unlink()
        return d

    def test_e1_waive_free_only(self):
        d = self._copy("e1-nofree")
        rc, s, _ = analyze("e1", d)
        self.assertEqual(rc, 1)                               # 不放寬就是 invalid
        rc, s, _ = analyze("e1", d, "--allow-missing", "free")
        self.assertEqual(rc, 0)
        self.assertTrue(s["valid"])
        self.assertEqual(s["waived"], ["free.log"])
        self.assertTrue(all(seg["free_available_mb"] is None for seg in s["segments"].values()))
        self.assertTrue(all(seg["tj_max"] is not None and seg["rates"] for seg in s["segments"].values()))

    def test_e1_waive_does_not_relax_other_coverage(self):
        d = self._copy("e1-nofree-partial")
        lines = (d / "tegrastats.log").read_text().splitlines()
        (d / "tegrastats.log").write_text("\n".join(lines[:100]) + "\n")
        rc, s, _ = analyze("e1", d, "--allow-missing", "free")
        self.assertEqual(rc, 1)
        self.assertIn("tegrastats", s["input_problems"]["coverage"])


class Review3Tests(unittest.TestCase):
    def test_ack_dry_run_keeps_record(self):
        from pawexp import cli
        importlib.reload(faults)
        cfg = base_cfg()
        st = [{"name": "cloud_refused", "status": "legacy_manual", "scope": "legacy", "dry_run": False, "applied_at": 0,
               "one_way": False, "sudo": True, "revert": ["old sudo command"]}]
        faults.save_state(cfg, st)
        cli.faults = faults
        cli.dispatch(cli.build_parser().parse_args(["fault", "ack", "cloud_refused", "--dry-run"]))
        self.assertEqual([e["name"] for e in faults.load_state(cfg)], ["cloud_refused"])
        cli.dispatch(cli.build_parser().parse_args(["fault", "ack", "cloud_refused"]))
        self.assertEqual(faults.load_state(cfg), [])

    def test_e3_missing_proposals_is_incomplete(self):
        import shutil
        d = TMP / "e3-noprop"
        shutil.copytree(gen_fixtures() / "E3", d, dirs_exist_ok=True)
        rows = [json.loads(l) for l in (d / "topics.jsonl").read_text().splitlines()]
        (d / "topics.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows if x["topic"] != "/brain/proposal"))
        rc, s, _ = analyze("e3", d, "--mode", "acceptance")
        self.assertEqual(rc, 1)
        self.assertFalse(s["valid"])
        self.assertIsNone(s["canned_rate"])
        self.assertFalse(s["observation"]["complete"])
        self.assertEqual(s["observation"]["missing_proposal"], ["c01", "c02", "c03", "c04", "c05"])
        self.assertEqual(s["observation"]["n_complete"], 0)

    def test_clock_is_stubbed_and_remote_is_forbidden(self):
        self.assertIs(offline_ops().clock, fake_clock)
        with self.assertRaises(AssertionError):
            subprocess.run(["ssh", "jetson", "true"])


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1, argv=[sys.argv[0]])
