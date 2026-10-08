"""fault apply|revert|status（設計 §3.8）。狀態檔 ~/pawai-exp-data/faults.json。

每個 entry：status ∈ {pending_apply, active, partial, pending_revert}；scope ∈ {demo, system}；dry_run；items（sudo 版的精確目標）。
- apply 先寫 pending_apply 再動遠端；成功改 active，中途失敗留 partial（revert 可重入，照樣處理）。
- scope=demo（重開 demo 視窗、offline_mode、asr_node_off）：demo 收掉就自然消失，down 時直接清掉紀錄。
- scope=system（sudo 的 hosts／iptables、RTX 8000 的 asr_down）：down 必須真的 revert，失敗留 pending_revert。
- --dry-run 的 entry 標 dry_run；revert --dry-run 只移除 dry entry，真 entry 只印指令不動。
- sudo 版只撤銷本工具加的：iptables 規則帶 comment pawexp:<fault>、hosts 行尾 # pawexp:<fault>，刪除用完全相同的規格。
"""
import json
import re
import time
import urllib.request

from .core import Fail, proc_pat, data_dir, read_json, say, write_json

NAMES = ["llm_primary_bad", "llm_both_bad", "cloud_timeout", "cloud_refused", "tts_cloud_refused",
         "asr_node_off", "asr_down", "offline_mode"]
EXP = "/home/jetson/exp"


BOGUS = "bogus/none"
LLM = {"main": "", "primary_bad": f"openrouter_gemini_model:={BOGUS}",
       "both_bad": f"openrouter_gemini_model:={BOGUS} openrouter_deepseek_model:={BOGUS}",
       "timeout": "openrouter_request_timeout_s:=0.05"}
TTS = {"main": "", "gemini_bad": f"openrouter_gemini_model:={BOGUS}",
       "both_bad": f"openrouter_gemini_model:={BOGUS} edge_tts_voice:=bogus-Voice"}


def _window(win, script, args):
    run = f"zsh {EXP}/{script} {args}".strip()
    return ("jetson", f"tmux kill-window -t demo:{win} 2>/dev/null; tmux new-window -t demo -n {win} && "
                      f"tmux send-keys -t demo:{win} '{run}' Enter")


def _llm(mode):
    return [_window("llm", "llm_window.sh", LLM[mode]),
            ("wait_pane", ("demo:llm", "conversation_graph_node ready", 90))]


def _tts(mode):
    return [_window("tts", "tts_window.sh", TTS[mode]),
            ("wait_pane", ("demo:tts", "Enhanced TTS Node Initialized", 60))]


def _tag(fault):
    return f"pawexp:{fault}"


def _ipt_spec(ip, fault):
    return f"OUTPUT -p tcp -d {ip} --dport 443 -m comment --comment {_tag(fault)} -j DROP"


def _host_line(host, fault):
    return f"127.0.0.1 {host} # {_tag(fault)}"


def _bre(text):
    """sed 基本正規式：只跳脫 BRE 特殊字元（GNU／BSD sed 行為一致；re.escape 會連空白、# 都跳脫）。"""
    return re.sub(r"([.\[\]*^$\\/])", r"\\\1", text)


def _hosts_add(fault, *hosts):
    return [("jetson", f"grep -qxF '{_host_line(h, fault)}' /etc/hosts || "
                       f"echo '{_host_line(h, fault)}' | sudo -n tee -a /etc/hosts >/dev/null") for h in hosts]


def _hosts_del(fault, *hosts):
    exprs = " ".join("-e '/^" + _bre(_host_line(h, fault)) + "$/d'" for h in hosts)
    return [("jetson", f"sudo -n sed -i {exprs} /etc/hosts"),
            ("jetson_check", f"! grep -q '# {_tag(fault)}$' /etc/hosts")]


# iptables -C：rc=0 規則存在、rc=1 不存在，其他（例如 4＝鎖／資源問題）是查詢失敗。
# 先 sudo -n true：免密 sudo 不通時 sudo 本身也回 1，會被誤讀成「規則不存在」。
_SUDO_OK = "sudo -n true 2>/dev/null || { echo 'sudo -n 不可用' >&2; exit 3; }"


def _ipt_check(spec):
    return f"sudo -n iptables -C {spec} 2>/dev/null; rc=$?"


def _ipt_add(fault, ips):
    return [("jetson", f"{_SUDO_OK}; {_ipt_check(_ipt_spec(ip, fault))}; "
                       f"if [ $rc -eq 1 ]; then sudo -n iptables -I {_ipt_spec(ip, fault)}; "
                       f"elif [ $rc -ne 0 ]; then echo \"iptables -C 查詢失敗 rc=$rc\" >&2; exit 2; fi") for ip in ips]


def _ipt_del(fault, ips):
    steps = [("jetson", f"{_SUDO_OK}; while :; do {_ipt_check(_ipt_spec(ip, fault))}; "
                        f"[ $rc -eq 1 ] && break; "
                        f"[ $rc -ne 0 ] && {{ echo \"iptables -C 查詢失敗 rc=$rc\" >&2; exit 2; }}; "
                        f"sudo -n iptables -D {_ipt_spec(ip, fault)} || exit 1; done") for ip in ips]
    return steps + [("jetson_check", f"{_SUDO_OK}; {_ipt_check(_ipt_spec(ip, fault))}; [ $rc -eq 1 ]") for ip in ips]


def plan(cfg, name, items=None):
    """回傳 {apply, revert, scope, one_way, note}；每步是 (kind, arg)。sudo 版 cloud_timeout 需要 items（IP 清單）。"""
    sudo = bool(cfg.get("jetson", {}).get("sudo"))
    rtx = cfg.get("rtx8000", {})
    sess, asr_cmd = rtx.get("asr_session", "pawai-asr"), rtx.get("asr_cmd", "")
    demo = "demo"
    if name == "llm_primary_bad":
        return dict(apply=_llm("primary_bad"), revert=_llm("main"), scope=demo)
    if name == "llm_both_bad":
        return dict(apply=_llm("both_bad"), revert=_llm("main"), scope=demo)
    if name == "cloud_timeout":
        if not sudo:
            return dict(apply=_llm("timeout"), revert=_llm("main"), scope=demo,
                        note="no-sudo：以 openrouter_request_timeout_s:=0.05 模擬逾時")
        ips = items or []
        return dict(apply=_ipt_add(name, ips), revert=_ipt_del(name, ips), scope="system",
                    note=f"sudo：只對解析到的 {len(ips)} 個 IP 加帶 comment {_tag(name)} 的 DROP")
    if name == "cloud_refused":
        if not sudo:
            return dict(apply=_llm("both_bad") + _tts("gemini_bad"), revert=_llm("main") + _tts("main"), scope=demo,
                        note="no-sudo：LLM 兩模型無效＋TTS Gemini 模型無效")
        return dict(apply=_hosts_add(name, "openrouter.ai"), revert=_hosts_del(name, "openrouter.ai"), scope="system")
    if name == "tts_cloud_refused":
        if not sudo:
            return dict(apply=_llm("both_bad") + _tts("both_bad"), revert=_llm("main") + _tts("main"), scope=demo,
                        note="no-sudo：LLM 兩模型無效＋TTS Gemini 模型與 edge 聲音無效")
        hosts = ("openrouter.ai", "speech.platform.bing.com")
        return dict(apply=_hosts_add(name, *hosts), revert=_hosts_del(name, *hosts), scope="system")
    if name == "asr_node_off":
        return dict(apply=[("jetson", f"tmux kill-window -t demo:asr 2>/dev/null; sleep 2; ! pgrep -f '{proc_pat('stt_intent_node')}'")],
                    revert=[], one_way=True, scope=demo, note="單向：恢復要重新 up")
    if name == "asr_down":
        return dict(apply=[("rtx", f"tmux send-keys -t {sess} C-c"),
                           ("wait_jetson", ("! curl -sf --max-time 2 localhost:8001/health", 30))],
                    revert=[("rtx", f"tmux send-keys -t {sess} '{asr_cmd}' Enter"),
                            ("wait_jetson", ("curl -sf --max-time 2 localhost:8001/health", 120))], scope="system")
    if name == "offline_mode":
        return dict(apply=[("http", ("POST", "/api/offline_mode", {"enabled": True}))],
                    revert=[("http", ("POST", "/api/offline_mode", {"enabled": False}))], scope=demo)
    raise Fail("fault", f"未知的 fault：{name}（可用：{', '.join(NAMES)}）")


def describe(step):
    kind, arg = step
    if kind == "jetson":
        return f"ssh jetson {arg!r}"
    if kind == "jetson_check":
        return f"check: ssh jetson {arg!r}"
    if kind == "rtx":
        return f"ssh rtx8000 {arg!r}"
    if kind == "wait_pane":
        return f"wait {arg[0]} pane 出現 {arg[1]!r}（≤{arg[2]} s）"
    if kind == "wait_jetson":
        return f"wait ssh jetson {arg[0]!r}（≤{arg[1]} s）"
    if kind == "http":
        return f"{arg[0]} {arg[1]} {json.dumps(arg[2])}"
    return f"{kind} {arg}"


def http(cfg, method, path, body, dry):
    url = cfg["hosts"]["gateway"].rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if cfg["hosts"].get("gateway_token"):
        headers["Authorization"] = "Bearer " + cfg["hosts"]["gateway_token"]
    from .core import log_line
    if dry:
        print(f"[dry-run] {method} {url} {json.dumps(body)}")
        log_line(f"[dry-run] {method} {url} {json.dumps(body)}")
        return
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as r:
            out = r.read().decode("utf-8", "ignore")
        log_line(f"{method} {url} {json.dumps(body)} rc=0 {out[:120]}")
    except Exception as e:
        log_line(f"{method} {url} {json.dumps(body)} rc=err {e}")
        raise Fail(f"http {path}", str(e))


def execute(r, cfg, steps, label):
    for kind, arg in steps:
        if kind in ("jetson", "jetson_check"):
            r.ssh(arg, f"{label}: {kind}", timeout=60)
        elif kind == "rtx":
            r.rtx_ssh(arg, f"{label}: rtx", timeout=30)
        elif kind == "http":
            http(cfg, *arg, dry=r.dry)
        elif kind == "wait_pane":
            wait_pane(r, *arg, step=label)
        elif kind == "wait_jetson":
            cmd, limit = arg
            if r.dry:
                r.ssh(cmd, label)
                continue
            t0 = time.time()
            while r.ssh(cmd, f"{label}: wait", check=False, quiet=True, timeout=20).returncode != 0:
                if time.time() - t0 > limit:
                    raise Fail(label, f"{limit} s 內未達成：{cmd}")
                time.sleep(2)


def wait_pane(r, target, pattern, limit, step="wait"):
    cmd = f"tmux capture-pane -t {target} -pJ -S -300"
    if r.dry:
        r.ssh(cmd, step)
        return ""
    t0 = time.time()
    while True:
        out = r.ssh(cmd, f"{step}: capture {target}", check=False, quiet=True, timeout=20).stdout
        if pattern in out:
            return out
        if time.time() - t0 > limit:
            tail = "\n".join(out.strip().splitlines()[-30:])
            print(tail)
            raise Fail(step, f"{target} 在 {limit} s 內沒出現 {pattern!r}")
        time.sleep(3)


def _state_path(cfg):
    return data_dir(cfg) / "faults.json"


def load_state(cfg):
    """舊 schema（d6bcc36 之前，沒有 scope／status）遷移成 legacy_manual：保留紀錄、不自動復原。
    舊版 hosts／iptables 注入沒有 pawexp 標記，新版的精確刪除對不到，必須由人確認後用 fault ack 清除。"""
    st = read_json(_state_path(cfg), [])
    changed = False
    for e in st:
        if "scope" not in e:
            e.update(status="legacy_manual", scope="legacy", dry_run=False, items=e.get("items"),
                     last_error="舊格式紀錄（無 scope／標記）：不自動復原，請人工確認 /etc/hosts、iptables、demo 視窗後 fault ack")
            changed = True
    if changed:
        save_state(cfg, st)
    return st


def save_state(cfg, st):
    write_json(_state_path(cfg), st)


def _cfg_for(cfg, e):
    return dict(cfg, jetson=dict(cfg.get("jetson", {}), sudo=e.get("sudo", False)))


def resolve_items(r, cfg, name):
    """sudo 版 cloud_timeout：先唯讀解析 openrouter.ai 的 IPv4，寫進狀態後才動 iptables。"""
    if name != "cloud_timeout" or not cfg.get("jetson", {}).get("sudo"):
        return None
    if r.dry:
        r.ssh("getent ahostsv4 openrouter.ai", "fault: resolve")
        return ["<openrouter-ip>"]
    out = r.ssh("getent ahostsv4 openrouter.ai | awk '{print $1}' | sort -u", "fault: resolve openrouter.ai", timeout=30).stdout
    ips = [x for x in out.split() if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", x)]
    if not ips:
        raise Fail("fault apply", "openrouter.ai 解析不到 IPv4，不加任何規則")
    return ips


def apply(r, cfg, name):
    st = load_state(cfg)
    if any(e["name"] == name and e.get("dry_run", False) == r.dry for e in st):
        raise Fail("fault apply", f"{name} 已在清單（{'dry-run' if r.dry else 'active'}）；先 revert")
    items = resolve_items(r, cfg, name)
    p = plan(cfg, name, items)
    if p.get("note"):
        say(p["note"])
    for s in p["apply"]:
        print("  apply:", describe(s))
    entry = {"name": name, "status": "pending_apply", "scope": p["scope"], "dry_run": r.dry, "applied_at": time.time(),
             "one_way": bool(p.get("one_way")), "sudo": bool(cfg.get("jetson", {}).get("sudo")), "items": items,
             "revert": [describe(s) for s in p["revert"]] or ["(單向，不 revert；重新 up)"]}
    st.append(entry)
    save_state(cfg, st)  # 動遠端之前先持久化復原紀錄
    try:
        execute(r, cfg, p["apply"], f"fault apply {name}")
    except Fail:
        entry["status"] = "partial"
        save_state(cfg, st)
        say(f"fault {name} 套用中途失敗：已記為 partial，revert all 會處理")
        raise
    entry["status"] = "active"
    save_state(cfg, st)
    say(f"fault {name} applied{'（dry-run）' if r.dry else ''}")


def _revert_entry(r, cfg, e):
    p = plan(_cfg_for(cfg, e), e["name"], e.get("items"))
    for s in p["revert"]:
        print("  revert:", describe(s))
    execute(r, cfg, p["revert"], f"fault revert {e['name']}")


def ack(cfg, name, dry=False):
    """人工確認後清除需人工處理的紀錄（legacy_manual／pending_revert／partial）。dry-run 只列出、不刪。"""
    st = load_state(cfg)
    hit = [e for e in st if e["name"] == name or name == "all"]
    if not hit:
        raise Fail("fault ack", f"{name} 不在清單")
    if dry:
        for e in hit:
            say(f"[dry-run] 會清除 fault {e['name']}（{e.get('status')}）；紀錄保留未動")
        return
    keep = [e for e in st if e not in hit]
    save_state(cfg, keep)
    for e in hit:
        say(f"fault {e['name']}（{e.get('status')}）已由人工確認清除")


def revert(r, cfg, name, scopes=("demo", "system"), drop_demo=False, keep_demo=False):
    """name='all' 逆序處理。dry-run：只移除 dry entry，真 entry 只印指令。
    drop_demo=True（down 用）：demo scope 的真 entry 不執行、直接清掉（demo 已收）。失敗的留 pending_revert，最後統一 Fail。"""
    st = load_state(cfg)
    targets = list(reversed(st)) if name == "all" else [e for e in st if e["name"] == name]
    if name != "all" and not targets:
        raise Fail("fault revert", f"{name} 不在清單")
    failed = []
    for e in targets:
        if e.get("status") == "legacy_manual":  # 舊格式：絕不自動處理
            print(f"  {e['name']}：舊格式紀錄，需人工確認後 fault ack（原 revert：{e.get('revert')}）")
            failed.append(f"{e['name']}(legacy_manual)")
            continue
        if keep_demo and e.get("scope") == "demo" and not e.get("dry_run"):
            e["status"] = "pending_revert"
            e["last_error"] = "down 的 cleanup 未確認成功，demo 視窗狀態不明，保留紀錄"
            save_state(cfg, st)
            failed.append(e["name"])
            continue
        if r.dry and not e.get("dry_run"):
            print(f"  [dry-run] 真 entry {e['name']}（{e.get('status')}）不動，revert 指令：")
            _revert_entry(r, cfg, e)
            continue
        if e.get("dry_run") and r.dry and not e.get("one_way"):
            _revert_entry(r, cfg, e)  # dry entry：印出 revert 指令（Runner 是 dry，不連線）
        if not e.get("dry_run") and not e.get("one_way") and e.get("scope") in scopes:
            if drop_demo and e.get("scope") == "demo":
                say(f"fault {e['name']}：demo 已收，紀錄清除（cleared_by_down）")
            else:
                try:
                    _revert_entry(r, cfg, e)
                except Fail as err:
                    e["status"] = "pending_revert"
                    e["last_error"] = f"{err.step}: {err.detail}"
                    save_state(cfg, st)
                    failed.append(e["name"])
                    continue
        st = [x for x in st if x is not e]
        save_state(cfg, st)
        say(f"fault {e['name']} reverted{'（dry-run entry）' if e.get('dry_run') else ''}")
    if failed:
        raise Fail("fault revert", f"未完成復原（pending_revert，狀態已保留）：{failed}")


def status(cfg):
    st = load_state(cfg)
    if not st:
        print("(none)")
        return
    for e in st:
        t = time.strftime("%H:%M:%S", time.localtime(e["applied_at"]))
        flags = [e.get("status", "active"), e.get("scope", "?")] + (["dry-run"] if e.get("dry_run") else []) \
            + (["one_way"] if e.get("one_way") else [])
        print(f"{e['name']}  applied {t}  [{', '.join(flags)}]")
        if e.get("last_error"):
            print(f"    last_error: {e['last_error']}")
        for d in e["revert"]:
            print(f"    revert: {d}")
