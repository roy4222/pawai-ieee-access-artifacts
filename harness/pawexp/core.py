"""設定、遠端執行、log 與失敗處理。所有 ssh／scp／rsync 都經過 Runner.run。"""
import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent          # <repo>/harness
REPO = HARNESS.parent
TOML = HARNESS / "pawexp.toml"

REQUIRED = {
    "hosts": ["jetson", "rtx8000", "gateway"],
    "paths": ["pawai_repo", "exp_dir", "tts_cache", "trace_dir", "data_dir", "bank"],
    "jetson": ["idle_ram_mb_max"],
    "safety": ["inplace_api_ids", "skill_request_whitelist"],
    "record": ["topics", "hz_topics"],
}


def proc_pat(name):
    """遠端 pgrep／pkill -f 用的樣式：首字包成 [x]（例 [s]tt_intent_node、[r]os2）。
    ssh 的遠端 zsh -c 指令列本身含有這串字；寫成 [x]yz 後指令列裡是 "[x]yz"，正規式只比對 "xyz"，不會比對到自己。"""
    return f"[{name[0]}]{name[1:]}"


class Fail(Exception):
    """任何步驟失敗：印 [pawexp] FAIL <step>: <detail> 並以 2 結束。"""
    def __init__(self, step, detail="", code=2):
        super().__init__(step)
        self.step, self.detail, self.code = step, detail, code


def load_config(path=TOML):
    try:
        cfg = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Fail("config", f"{path}: {e}")
    missing = []
    for sec, keys in REQUIRED.items():
        for k in keys:
            v = cfg.get(sec, {}).get(k)
            if v is None or v == "" or v == []:
                missing.append(f"{sec}.{k}")
    if missing:
        raise Fail("config", f"{Path(path).name} 缺必要鍵或為空：{', '.join(missing)}")
    return cfg


def data_dir(cfg=None):
    d = (cfg or {}).get("paths", {}).get("data_dir") or "~/pawai-exp-data"
    p = Path(os.environ.get("PAWEXP_DATA", d)).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_line(text):
    with open(data_dir() / "pawexp.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {text}\n")


def say(msg):
    print(f"[pawexp] {msg}", flush=True)


class Runner:
    def __init__(self, cfg, dry_run=False):
        self.cfg, self.dry = cfg, dry_run
        self.jetson = cfg["hosts"]["jetson"]
        self.rtx = cfg["hosts"]["rtx8000"]
        self.exp = cfg["paths"]["exp_dir"]

    def run(self, argv, step, timeout=120, check=True, quiet=False):
        cmd = " ".join(shlex.quote(a) for a in argv)
        if self.dry:
            print(f"[dry-run] {cmd}", flush=True)
            log_line(f"[dry-run] {cmd}")
            return subprocess.CompletedProcess(argv, 0, "", "")
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            log_line(f"{cmd} rc=timeout({timeout}s)")
            raise Fail(step, f"timeout {timeout}s: {cmd[:200]}")
        log_line(f"{cmd} rc={p.returncode}")
        if check and p.returncode != 0:
            err = "\n".join((p.stderr or p.stdout or "").strip().splitlines()[:5])
            raise Fail(step, f"rc={p.returncode} {err}")
        if not quiet and p.returncode != 0:
            say(f"{step}: rc={p.returncode}（不中止）")
        return p

    def ssh(self, cmd, step, host=None, **kw):
        return self.run(["ssh", host or self.jetson, cmd], step, **kw)

    def rtx_ssh(self, cmd, step, **kw):
        return self.run(["ssh", self.rtx, cmd], step, **kw)

    def scp(self, src, dst, step, recursive=False, **kw):
        argv = ["scp", "-q"] + (["-r"] if recursive else []) + [str(src), f"{self.jetson}:{dst}"]
        return self.run(argv, step, **kw)

    def rsync_back(self, remote_dir, local_dir, step, **kw):
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        return self.run(["rsync", "-a", f"{self.jetson}:{remote_dir.rstrip('/')}/", f"{str(local_dir).rstrip('/')}/"], step, **kw)

    def jetson_time(self):
        p = self.ssh("date +%s.%N", "jetson-time", timeout=20)
        return float(p.stdout.strip() or time.time())

    def tmux_windows(self, session):
        p = self.ssh(f"tmux list-windows -t {session} -F '#{{window_name}}' 2>/dev/null || true", f"tmux-list:{session}", timeout=20)
        return [w for w in p.stdout.split() if w]

    def sessions(self):
        p = self.ssh("tmux ls -F '#{session_name}' 2>/dev/null || true", "tmux-ls", timeout=20)
        return [s for s in p.stdout.split() if s]


def run_dir(cfg, run_id):
    d = data_dir(cfg) / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
