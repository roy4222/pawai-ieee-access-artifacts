"""env-check、up、down、record（設計 §3.1–3.4）。"""
import json
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from . import faults
from .core import HARNESS, REPO, Fail, proc_pat, data_dir, log_line, read_json, run_dir, say, write_json

ROS = "source /opt/ros/humble/setup.zsh && source ~/elder_and_dog/install/setup.zsh"
DEMO_SESSIONS = ("demo", "lidarmon", "exp")


# ── 共用 ──
def sync_scripts(r):
    r.ssh(f"mkdir -p {r.exp}/runs", "sync: mkdir")
    r.run(["rsync", "-a", f"{HARNESS / 'jetson'}/", f"{r.jetson}:{r.exp}/"], "sync: rsync scripts")


def clock(r, n=10):
    """一條常駐 ssh 讀 stdin 回 date，量 RTT 最小的樣本：offset = t_jetson − (t_send + rtt/2)。"""
    if r.dry:
        r.ssh("while read l; do date +%s.%N; done", "clock")
        return {"offset_s": 0.0, "rtt_s": 0.0, "n": 0}
    argv = ["ssh", r.jetson, "while read l; do date +%s.%N; done"]
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    samples = []
    try:
        for _ in range(n + 1):
            t_send = time.time()
            p.stdin.write("x\n")
            p.stdin.flush()
            line = p.stdout.readline()
            t_recv = time.time()
            if not line:
                break
            samples.append((t_recv - t_send, float(line) - (t_send + (t_recv - t_send) / 2)))
    finally:
        p.stdin.close()
        p.wait(timeout=10)
    log_line(f"ssh {r.jetson} 'while read l; do date +%s.%N; done' rc={p.returncode} samples={len(samples)}")
    samples = samples[1:]  # 第一筆含連線建立
    if not samples:
        raise Fail("clock", "ssh 沒有回時間")
    rtt, off = min(samples)
    return {"offset_s": off, "rtt_s": rtt, "n": len(samples), "t": time.time()}


def state_path(cfg):
    return data_dir(cfg) / "state.json"


# ── env-check ──
def env_check(r, cfg, a):
    J = lambda cmd, step, t=30: r.ssh(cmd, step, check=False, quiet=True, timeout=t)

    def R(cmd, step, t=20):
        try:
            return r.rtx_ssh(cmd, step, check=False, quiet=True, timeout=t)
        except Fail as e:  # RTX 8000 不通只記為該項 NG，不中止其餘檢查
            return subprocess.CompletedProcess([], 255, "", f"{e.step}: {e.detail}")
    res = {}

    def item(key, ok, raw):
        res[key] = {"ok": ok, "raw": raw.strip() if isinstance(raw, str) else raw}
        say(f"env-check {key}: {'OK' if ok else ('—' if ok is None else 'NG')}")

    p = R("curl -s --max-time 5 localhost:8001/health", "env: sensevoice")
    item("sensevoice", '"status":"ok"' in p.stdout.replace(" ", ""),
         p.stdout if p.returncode != 255 else f"ssh {r.rtx} 不通：{p.stderr.strip()[:200]}")
    p = J("curl -s --max-time 5 localhost:8001/health; echo; curl -s --max-time 5 localhost:8000/v1/models", "env: tunnel")
    if p.returncode == 255:
        raise Fail("env-check", f"ssh {r.jetson} 連不上：{p.stderr.strip()[:200]}")
    lines = p.stdout.strip().splitlines()
    item("tunnel", len(lines) >= 2 and lines[0].startswith("{") and lines[-1].startswith("{"), p.stdout[:600])
    p = J("grep -c GATEWAY_AUTH_TOKEN ~/elder_and_dog/.env || true", "env: gateway_token")
    item("gateway_token", p.stdout.strip() == "0", p.stdout)
    p = J("ping -c1 -W2 192.168.123.161 >/dev/null && echo go2_ok; ls /dev/rplidar /dev/ttyUSB0 2>&1; "
          "lsusb | grep -i -E 'intel|8086:0b07' && echo d435_ok", "env: devices")
    item("devices", "go2_ok" in p.stdout and "d435_ok" in p.stdout and "/dev/ttyUSB0" in p.stdout and "cannot access '/dev/ttyUSB0'" not in p.stdout, p.stdout)
    pv = J("cat /etc/nv_tegra_release; nvpmodel -q 2>&1 | head -3; ollama --version 2>&1; "
           f"{ROS} && echo ROS_DISTRO=$ROS_DISTRO; "
           "python3 -c 'import importlib.metadata as m\nfor p in (\"onnxruntime-gpu\",\"onnxruntime\",\"sherpa-onnx\",\"faster-whisper\",\"ctranslate2\",\"edge-tts\",\"piper-tts\",\"rclpy\"):\n  try: print(p, m.version(p))\n  except Exception: pass'",
           "env: versions jetson", 60)
    rv = R("nvidia-smi --query-gpu=index,name,memory.used --format=csv; "
           "~/miniconda3/envs/pawai_gpu/bin/pip show funasr 2>/dev/null | grep -E '^(Name|Version)'; "
           "systemctl --user cat vllm.service 2>/dev/null | grep -E 'ExecStart|max-model-len'; "
           "curl -s --max-time 5 localhost:8000/v1/models", "env: versions rtx", 30)
    item("versions", "R36" in pv.stdout, {"jetson": pv.stdout.strip(), "rtx8000": rv.stdout.strip() or rv.stderr.strip()})
    p = J(f"{ROS} && ros2 bag --help >/dev/null 2>&1 && echo rosbag_ok; which tegrastats; sudo -n true 2>/dev/null && echo sudo_ok || echo sudo_no",
          "env: rosbag_tegrastats_sudo")
    item("rosbag_tegrastats_sudo", "tegrastats" in p.stdout, p.stdout)
    pane = data_dir(cfg) / "last_up_asr_pane.txt"
    if pane.exists():
        txt = pane.read_text(errors="ignore")
        item("asr_node", not re.search(r"Traceback|InputStream|PortAudioError|Error opening", txt), txt[-1500:])
    else:
        item("asr_node", None, "尚未 up 過（pawexp up 會存 demo:asr pane 尾 20 行）")
    c = clock(r)
    write_json(data_dir(cfg) / "clock.json", c)
    item("clock", c["rtt_s"] < 0.2, json.dumps(c))
    p = J("ls -l ~/.local/bin/piper ~/models/piper/zh_CN-huayan-medium.onnx 2>&1; cat ~/.local/bin/piper 2>/dev/null", "env: piper")
    item("piper", "No such file" not in p.stdout and ".local/bin/piper" in p.stdout, p.stdout)
    p = J("ls -l /dev/rplidar /dev/ttyUSB0 2>&1", "env: lidar_port")
    item("lidar_port", "cannot access '/dev/rplidar'" not in p.stdout, p.stdout)
    if not r.dry:
        write_json(data_dir(cfg) / "env_check.json", res)
    bad = [k for k, v in res.items() if v["ok"] is False]
    say(f"env_check.json：{len(res)} 項；不通：{', '.join(bad) or '無'}")
    return 0


# ── up ──
def pane_tail(r, target, n=30):
    return r.ssh(f"tmux capture-pane -t {target} -pJ -S -300 2>/dev/null | tail -{n}", f"tail {target}",
                 check=False, quiet=True, timeout=20).stdout


def up(r, cfg, a):
    paths = cfg["paths"]
    kills = [k for k in a.kill.split(",") if k]
    live = [s for s in r.sessions() if s in DEMO_SESSIONS]
    if live:
        raise Fail("up: status", f"Jetson 已有 session {live}；先 pawexp down")
    sync_scripts(r)
    if a.clear_tts_cache:
        cache = paths["tts_cache"]
        parent = cache.rsplit("/", 1)[0]
        r.ssh(f"test -d {parent} && {{ test ! -d {cache} || find {cache} -mindepth 1 -delete; }} && "
              f"echo cleared $(ls -A {cache} 2>/dev/null | wc -l)", "up: clear tts cache")
    say("啟動 demo（start_full_demo_tmux.sh，約 70 s）")
    r.ssh(f"cd {paths['pawai_repo']} && bash scripts/start_full_demo_tmux.sh > {r.exp}/last_demo_start.log 2>&1",
          "up: start_full_demo_tmux.sh", timeout=300)
    health(r, cfg)
    if not r.dry:
        (data_dir(cfg) / "last_up_asr_pane.txt").write_text(pane_tail(r, "demo:asr", 20), encoding="utf-8")
    for k in kills:
        r.ssh(f"tmux kill-window -t demo:{k}", f"up: kill {k}")
        if not r.dry:
            time.sleep(2)
            if k in r.tmux_windows("demo"):
                raise Fail(f"up: kill {k}", f"demo:{k} 還在")
        if k == "asr":
            r.ssh(f"sleep 2; ! pgrep -f '{proc_pat('stt_intent_node')}'", "up: verify stt_intent_node gone")
    if a.config == "B":
        port = a.rpl_port or cfg.get("jetson", {}).get("rpl_port", "/dev/rplidar")
        r.ssh(f"cd {paths['pawai_repo']} && RPLIDAR_SERIAL_PORT={port} bash scripts/start_lidar_monitor_tmux.sh "
              f"> {r.exp}/last_lidar_start.log 2>&1", "up: start_lidar_monitor_tmux.sh", timeout=120)
        r.ssh(f"tmux new-window -t lidarmon -n reactive && tmux send-keys -t lidarmon:reactive 'zsh {r.exp}/reactive_window.sh' Enter",
              "up: reactive window")
        r.ssh(f"{ROS} && timeout 30 ros2 topic echo --once --qos-reliability best_effort /scan_rplidar >/dev/null && echo scan_ok",
              "up: /scan_rplidar 有訊息", timeout=60)
        p = r.ssh(f"{ROS} && timeout 10 ros2 topic echo /cmd_vel 2>/dev/null | head -c 200; true", "up: /cmd_vel 10 s", timeout=40)
        if p.stdout.strip():
            raise Fail("up: /cmd_vel", f"config B 起來後 /cmd_vel 有訊息：{p.stdout[:200]}")
    write_json(state_path(cfg), {"config": a.config, "kill": kills, "t_up": time.time()})
    say(f"up 完成：config {a.config}，kill={kills or '無'}")
    return 0


def health(r, cfg, limit=90):
    if r.dry:
        r.ssh("tmux capture-pane -t demo:llm -pJ -S -300", "up: health")
        return
    t0 = time.time()
    while True:
        llm = r.ssh("tmux capture-pane -t demo:llm -pJ -S -300", "up: health llm pane", check=False, quiet=True, timeout=20).stdout
        topics = r.ssh(f"{ROS} && ros2 topic info /brain/chat_candidate | grep -c 'Publisher count: [1-9]'; "
                       "ros2 topic info /tts | grep -c 'Publisher count: [1-9]'", "up: health topics",
                       check=False, quiet=True, timeout=40).stdout.split()
        gw = r.ssh("curl -s --max-time 3 localhost:8080/health", "up: health gateway", check=False, quiet=True, timeout=20).stdout
        checks = {"llm_ready": "conversation_graph_node ready" in llm, "openrouter_on": "openrouter=on" in llm,
                  "chat_candidate_pub": topics[:1] == ["1"], "tts_pub": topics[1:2] == ["1"],
                  "gateway_ok": '"status":"ok"' in gw.replace(" ", "")}
        if all(checks.values()):
            say(f"health OK（{time.time() - t0:.0f} s）")
            return
        if time.time() - t0 > limit:
            for w in ("llm", "gateway"):
                print(f"--- demo:{w} 尾 30 行 ---")
                print(pane_tail(r, f"demo:{w}"))
            raise Fail("up: health", f"{limit} s 內未全過：{checks}（demo 留著，由人決定 down）")
        time.sleep(5)


# ── down ──
def cleanup_lists():
    """從 jetson/cleanup_exp.sh 讀 SESSIONS 與 PATS（單一來源），給腳本不在時的內嵌後備清理用。"""
    sh = (HARNESS / "jetson" / "cleanup_exp.sh").read_text(encoding="utf-8")
    sessions = re.search(r'^SESSIONS="([^"]+)"', sh, re.M).group(1).split()
    body = re.search(r"^PATS=\((.*?)\)", sh, re.M | re.S).group(1)
    pats = [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', body)]
    return sessions, pats


def inline_cleanup_cmd():
    sessions, pats = cleanup_lists()
    kills = "; ".join(f"tmux kill-session -t {s} 2>/dev/null" for s in sessions)
    pk = "; ".join(f"pkill -9 -f '{proc_pat(p)}' 2>/dev/null" for p in pats)  # 不比對到自己這條 ssh 的 zsh -c
    return f"{kills}; sleep 2; {pk}; sleep 2; echo FREE_USED_MB $(free -m | awk '/^Mem:/{{print $3}}'); true"


def down(r, cfg, a, sync=True):
    """順序：先清 Jetson（不被任何復原失敗擋住）→ 驗殘留 → 復原 system scope 的 fault → 回報。
    sync=False（緊急停止）：不先同步腳本，避免卡在 rsync／ssh mkdir 而延後停止。"""
    force = bool(getattr(a, "force", False))
    if sync:
        try:
            sync_scripts(r)
        except Fail as e:
            say(f"WARN 同步腳本失敗（{e.detail[:120]}），改用 Jetson 上既有的 cleanup_exp.sh 或內嵌清單")
    flag = " --force" if force else ""
    p = r.ssh(f"bash {r.exp}/cleanup_exp.sh{flag}", "down: cleanup", check=False, timeout=120)
    if p.returncode == 127 or "No such file" in (p.stderr or ""):
        say("cleanup_exp.sh 不在 Jetson 上，改用內嵌清單（與 cleanup_exp.sh 同源）")
        r.ssh(inline_cleanup_cmd(), "down: inline cleanup", check=False, timeout=120)
        p = r.ssh(f"bash {r.exp}/cleanup_exp.sh --verify-only", "down: verify", check=False, timeout=60)
        if p.returncode == 127 or "No such file" in (p.stderr or ""):
            raise Fail("down: verify", "已用內嵌清單清理，但 cleanup_exp.sh 不在 Jetson 上，無法驗證殘留；請手動檢查 tmux ls／ps")
    print((p.stdout or "").strip())
    used = _free_used(p.stdout)
    limit = cfg["jetson"]["idle_ram_mb_max"]
    if p.returncode != 0 or (used is not None and used > limit):
        if force:
            p = r.ssh(f"bash {r.exp}/cleanup_exp.sh --force", "down: cleanup force", check=False, timeout=120)
            print((p.stdout or "").strip())
            used = _free_used(p.stdout)
    residual = p.returncode != 0 or (used is not None and used > limit)
    pending = []
    try:
        unload_ollama(r)
    except Fail as e:
        pending.append(f"ollama: {e.detail}")
    try:  # demo 確定已收：demo scope 的 fault 清紀錄；cleanup 沒確認成功就保留成 pending。system scope 一律真的 revert
        faults.revert(r, cfg, "all", drop_demo=not residual, keep_demo=residual)
    except Fail as e:
        pending.append(e.detail)
    if residual:
        raise Fail("down: verify", f"有殘留或 RAM used {used} MB > {limit}" + ("" if force else "（可用 down --force）")
                   + (f"；另有未完成：{pending}" if pending else ""))
    if pending:
        raise Fail("down: pending", f"Jetson 已清乾淨，但有未完成項目（狀態已保留，fault status 可看）：{pending}")
    say(f"down 完成：無殘留，RAM used {used} MB")
    return 0


def emergency_down(r, cfg):
    """watchdog 用：down --force，但不先同步腳本（同步可能卡住），直接跑 Jetson 上既有的 cleanup（不在就用內嵌清單）。
    失敗會拋出，讓 watchdog 記成 stop_error，主程序據此以結束碼 4 明確回報。"""
    down(r, cfg, SimpleNamespace(force=True), sync=False)


def _free_used(out):
    m = re.search(r"FREE_USED_MB (\d+)", out or "")
    return int(m.group(1)) if m else None


def _ollama_models(r, step):
    p = r.ssh("curl -s --max-time 5 localhost:11434/api/ps", step, check=False, quiet=True, timeout=20)
    if p.returncode != 0:
        raise Fail(step, f"查不到 Ollama 狀態（rc={p.returncode} {(p.stderr or '').strip()[:120]}）")
    try:
        return [m["name"] for m in json.loads(p.stdout).get("models", [])]
    except (ValueError, AttributeError) as e:
        raise Fail(step, f"Ollama /api/ps 回應無法解析：{(p.stdout or '')[:120]!r}（{e}）")


def unload_ollama(r):
    """對每個已載入模型送 keep_alive:0，之後再查一次必須為空；連線或解析失敗都回報失敗。"""
    if r.dry:
        r.ssh("curl -s --max-time 5 localhost:11434/api/ps", "down: ollama ps")
        return
    for m in _ollama_models(r, "down: ollama ps"):
        body = json.dumps({"model": m, "keep_alive": 0})
        r.ssh(f"curl -s --max-time 30 localhost:11434/api/generate -d '{body}' >/dev/null", f"down: unload {m}", timeout=60)
    left = _ollama_models(r, "down: ollama ps（卸載後）")
    if left:
        raise Fail("down: ollama", f"卸載後仍有模型：{left}")


# ── record ──
# record stop 後必須存在且非空（dmesg_* 可能是 "0"，仍非空）
RECORD_REQUIRED = ["tegrastats.log", "free.log", "ps.log", "hz.jsonl", "topics.jsonl", "dmesg_before.txt", "dmesg_after.txt",
                   "panes_end.txt", "pane_tts.log", "pane_llm.log", "pane_gateway.log", "trace.jsonl"]
def jrun(r, run_id):
    return f"{r.exp}/runs/{run_id}"


RUN_META_REQUIRED = ("run", "kind", "config", "kill", "demo_windows", "clock_offset_s", "clock_rtt_s",
                     "t_start", "t_end", "harness_sha", "rc")


def harness_sha():
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def run_meta(r, cfg, run_id, kind, args, rc_fn):
    """bench 等一次性 run 的 meta.json：開始記組態與時鐘，結束補 t_end／rc，並驗必要欄位（呼叫端以 with 包住）。"""
    class _Meta:
        def __enter__(self):
            st = read_json(state_path(cfg), {})
            wins = [] if r.dry else r.tmux_windows("demo")
            if not wins:   # demo 沒在跑：明確寫出，不沿用上一次 up 的 state
                config, kill = "demo_off", []
            else:          # demo 在跑：組態來自 up 的 state；state 不在就是 None（必要欄位驗證會擋）
                config, kill = st.get("config"), st.get("kill")
            c = clock(r, 5)
            self.meta = {"run": run_id, "kind": kind, "args": args, "config": config, "kill": kill, "demo_windows": wins,
                         "clock_offset_s": c.get("offset_s"), "clock_rtt_s": c.get("rtt_s"),
                         "t_start": r.jetson_time() if not r.dry else time.time(), "harness_sha": harness_sha(),
                         "faults_active": faults.load_state(cfg)}
            write_json(run_dir(cfg, run_id) / "meta.json", self.meta)
            return self.meta

        def __exit__(self, et, ev, tb):
            self.meta["t_end"] = r.jetson_time() if not r.dry else time.time()
            self.meta["rc"] = rc_fn() if et is None else f"{getattr(ev, 'step', et.__name__)}: {getattr(ev, 'detail', ev)}"
            self.meta["missing_fields"] = [k for k in RUN_META_REQUIRED if self.meta.get(k) is None or self.meta.get(k) == ""]
            write_json(run_dir(cfg, run_id) / "meta.json", self.meta)
            if et is None and self.meta["missing_fields"] and not r.dry:
                raise Fail("bench: meta", f"meta.json 缺必要欄位：{self.meta['missing_fields']}（資料已收，組態不可追溯）")
            return False
    return _Meta()


def record(r, cfg, a):
    if a.action == "start":
        return record_start(r, cfg, a)
    if a.action == "mark":
        t = r.jetson_time() if not r.dry else time.time()
        path = run_dir(cfg, a.run) / "marks.json"
        marks = read_json(path, [])
        marks.append({"t": t, "label": a.label})
        write_json(path, marks)
        say(f"mark {a.label!r} @ {t:.3f}")
        return 0
    return record_stop(r, cfg, a)


def record_start(r, cfg, a):
    if "exp" in r.sessions():
        raise Fail("record start", "Jetson 已有 exp session（上一個 record 沒 stop？）")
    sync_scripts(r)
    d = jrun(r, a.run)
    rec = cfg["record"]
    c = clock(r, 5)
    r.ssh(f"mkdir -p {d} && (dmesg 2>/dev/null | grep -ciE 'out of memory|oom-killer|killed process' || true) > {d}/dmesg_before.txt; "
          f"tmux capture-pane -t demo:object -pJ -S -3000 2>/dev/null | grep 'ONNX session ready' > {d}/object_pane.txt; true",
          "record start: dmesg/object")
    for w in ("tts", "llm", "gateway"):
        r.ssh(f"tmux pipe-pane -t demo:{w} -o 'cat >> {d}/pane_{w}.stream' 2>/dev/null; true", f"record start: pipe {w}")
    topics = ",".join(rec["topics"])
    wins = [("tegra", ""), ("free", ""), ("ps", ""), ("log", topics), ("hz", ",".join(rec["hz_topics"]))]
    if rec.get("use_rosbag"):
        wins.append(("bag", topics))
    first = True
    for name, arg in wins:
        cmd = f"zsh {r.exp}/exp_window.sh {name} {d} {arg}".strip()
        if first:
            r.ssh(f"tmux new-session -d -s exp -n {name} \"{cmd}\"", f"record start: {name}")
            first = False
        else:
            r.ssh(f"tmux new-window -t exp -n {name} \"{cmd}\"", f"record start: {name}")
    t_start = r.jetson_time() if not r.dry else time.time()
    if not r.dry:
        r.ssh(f"for i in $(seq 40); do test -f {d}/topics.jsonl.ready && test -f {d}/tegrastats.log && exit 0; sleep 0.5; done; exit 1",
              "record start: logger 訂閱完成（≤20 s）", timeout=40)
        alive = r.tmux_windows("exp")
        missing = [n for n, _ in wins if n not in alive]
        if missing:
            raise Fail("record start", f"exp 視窗沒起來：{missing}")
    st = read_json(state_path(cfg), {})
    write_json(run_dir(cfg, a.run) / "meta_start.json",
               {"run": a.run, "label": a.label, "t_start": t_start, "clock": c, "config": st.get("config"),
                "kill": st.get("kill")})
    say(f"record {a.run} 開始（Jetson {d}）")
    return 0


def record_stop(r, cfg, a):
    d = jrun(r, a.run)
    local = run_dir(cfg, a.run)
    r.ssh("for w in $(tmux list-windows -t exp -F '#{window_name}' 2>/dev/null); do tmux send-keys -t exp:$w C-c; done; "
          "sleep 3; tmux kill-session -t exp 2>/dev/null; true", "record stop: exp")
    t_end = r.jetson_time() if not r.dry else time.time()
    demo_windows = [] if r.dry else r.tmux_windows("demo")
    r.ssh(f"(dmesg 2>/dev/null | grep -ciE 'out of memory|oom-killer|killed process' || true) > {d}/dmesg_after.txt; "
          f"tmux list-panes -a -F '#{{session_name}}:#{{window_name}} #{{pane_dead}}' > {d}/panes_end.txt 2>/dev/null; "
          f"for w in tts llm gateway; do tmux pipe-pane -t demo:$w 2>/dev/null; "
          f"tmux capture-pane -t demo:$w -pJ -S -5000 > {d}/pane_$w.log 2>/dev/null; done; "
          f"T=$(ls -t {cfg['paths']['trace_dir']}/*.jsonl 2>/dev/null | head -1); [ -n \"$T\" ] && cp \"$T\" {d}/trace.jsonl; true",
          "record stop: collect")
    r.rsync_back(d, local, "record stop: rsync", timeout=600)
    for w in ("tts", "llm", "gateway"):  # pipe-pane 全程串流優先（capture-pane 受 history-limit 限制）
        s = local / f"pane_{w}.stream"
        if s.exists() and s.stat().st_size > 0:
            txt = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\r", "", s.read_text(errors="ignore"))
            (local / f"pane_{w}.log").write_text(txt, encoding="utf-8")
    ms = read_json(local / "meta_start.json", {})
    sha = harness_sha()
    meta = {"run": a.run, "label": ms.get("label"), "config": ms.get("config"), "kill": ms.get("kill"),
            "clock_offset_s": (ms.get("clock") or {}).get("offset_s"), "clock_rtt_s": (ms.get("clock") or {}).get("rtt_s"),
            "t_start": ms.get("t_start"), "t_end": t_end, "harness_sha": sha,
            "faults_active": faults.load_state(cfg)}
    # pane log 只對收集當下存在的 demo 視窗要求（E5／--kill llm 時 demo:llm 本來就不在）
    required = [f for f in RECORD_REQUIRED
                if not (f.startswith("pane_") and f[5:-4] not in demo_windows)]
    missing = [f for f in required if not (local / f).exists() or (local / f).stat().st_size == 0]
    meta.update(required=required, missing=missing, demo_windows_at_stop=demo_windows)
    if "trace.jsonl" in missing:
        missing[missing.index("trace.jsonl")] = "trace.jsonl（gateway 本次未寫 trace 或 trace_dir 不對）"
    write_json(local / "meta.json", meta)
    if missing:
        raise Fail("record stop: verify", f"資料已拉回 {local}，但缺檔或為空：{missing}")
    say(f"record {a.run} 結束，資料在 {local}")
    return 0
