"""inject（設計 §3.7）：E5 仲裁注入。前置：demo:llm 必須不存在、record 在跑；需操作者在場的案例必須 --confirm-each。"""
import json
import shlex
import subprocess
import sys
from pathlib import Path

from . import ops
from .core import HARNESS, REPO, Fail, log_line, say

SCRIPT = HARNESS / "jetson" / "inject_arbitration.py"


def select(cases, only, limit):
    sel = [c for c in cases if not only or c["category"] in only or c["id"] in only]
    return sel[:limit] if limit is not None else sel


def inject(r, cfg, a):
    path = Path(a.cases)
    if not path.is_absolute():
        path = (REPO / path) if (REPO / path).exists() else path.resolve()
    if not path.exists():
        raise Fail("inject", f"找不到案例檔 {path}")
    wl = ",".join(cfg["safety"]["skill_request_whitelist"])
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "--cases", str(path), "--validate-only", "--whitelist", wl],
                       capture_output=True, text=True)
    print(p.stdout.strip())
    if p.returncode != 0:
        raise Fail("inject: validate", "案例檔不合法，未送出任何訊息")
    data = json.loads(path.read_text(encoding="utf-8"))
    only = [x for x in a.only.split(",") if x]
    sel = select(data["cases"] if isinstance(data, dict) else data, only, a.limit)
    if not sel:
        raise Fail("inject", f"--only {a.only} 沒有選到案例")
    if a.confirm_each and not r.dry and not sys.stdin.isatty():
        raise Fail("inject", "--confirm-each 需要在終端機前由人按 Enter（stdin 不是 tty）")
    attended = [c["id"] for c in sel if c.get("needs_operator")]
    if attended and not a.confirm_each:
        raise Fail("inject", f"{attended} 需要操作者在場（會動）：請加 --confirm-each")
    say(f"選到 {len(sel)} 案：{', '.join(c['id'] for c in sel)}")
    wins = r.tmux_windows("demo") if not r.dry else []
    if "llm" in wins:
        raise Fail("inject: precheck", "demo:llm 還在（LangGraph 會搶先發候選）；先 tmux kill-window -t demo:llm")
    if not r.dry and not wins:
        raise Fail("inject: precheck", "demo 沒有在跑")
    if not r.dry and "exp" not in r.sessions():
        raise Fail("inject: precheck", "沒有 record（exp session）；先 pawexp record start --run <id>，分析要靠 topics.jsonl")
    p = r.ssh(f"{ops.ROS} && timeout 5 ros2 topic echo --once /capability/depth_clear 2>/dev/null | head -2; true",
              "inject: depth_clear", check=False, quiet=True, timeout=30)
    say(f"/capability/depth_clear：{p.stdout.strip() or '(沒收到)'}")
    ops.sync_scripts(r)
    jd = ops.jrun(r, a.run)
    r.ssh(f"mkdir -p {jd}", "inject: mkdir")
    r.scp(path, f"{jd}/cases.json", "inject: scp cases")
    args = f"--cases {jd}/cases.json --out {jd}/inject.jsonl --whitelist {shlex.quote(wl)}"
    if only:
        args += f" --only {shlex.quote(','.join(only))}"
    if a.limit is not None:
        args += f" --limit {a.limit}"
    if a.confirm_each:
        args += " --confirm-each"
    cmd = f"source {r.exp}/ros_env.zsh && python3 {r.exp}/inject_arbitration.py {args}"
    argv = ["ssh", r.jetson, cmd]
    if r.dry:
        print("[dry-run] " + " ".join(shlex.quote(x) for x in argv))
        log_line("[dry-run] " + " ".join(shlex.quote(x) for x in argv))
        loc = [sys.executable, "-I", str(SCRIPT), "--cases", str(path), "--whitelist", wl, "--dry-run"]
        if only:
            loc += ["--only", ",".join(only)]
        if a.limit is not None:
            loc += ["--limit", str(a.limit)]
        subprocess.run(loc)  # 本機印出序列化結果，不連 ROS
        return 0
    # 前景執行、stdin 直通（--confirm-each 在 Jetson 端讀 Enter）
    rc = subprocess.run(argv).returncode
    log_line(f"{' '.join(shlex.quote(x) for x in argv)} rc={rc}")
    if rc == 4:
        say("WATCHDOG（Jetson 端）→ down --force")
        try:
            ops.emergency_down(r, cfg)
        except Fail as e:
            if e.step == "down: pending":
                raise Fail("inject: watchdog", f"出現原地集合以外的動作；已 down --force，Jetson 已清乾淨；"
                           f"另有未完成復原（fault status 可看）：{e.detail}", code=4)
            raise Fail("inject: watchdog", f"出現原地集合以外的動作；緊急停止失敗：{e.step}: {e.detail}（請手動確認 Jetson）", code=4)
        raise Fail("inject: watchdog", "出現原地集合以外的 /webrtc_req 或 /cmd_vel；已 down --force", code=4)
    if rc != 0:
        raise Fail("inject", f"inject_arbitration.py 結束碼 {rc}")
    r.rsync_back(jd, ops.run_dir(cfg, a.run), "inject: rsync")
    say(f"inject {a.run} 完成")
    return 0
