"""分析腳本共用（只用 stdlib）。各 analyze_*.py 以 importlib 載入本檔（python3 -I 不會把腳本目錄放進 sys.path）。"""
import datetime as dt
import json
import math
import re
import statistics as st
import sys
import tomllib
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOML = HERE.parent / "harness" / "pawexp.toml"
TZ = dt.timezone(dt.timedelta(hours=8))   # Jetson 與 Mac 都是 Asia/Taipei；tegrastats 寫本地時間


def config():
    return tomllib.loads(TOML.read_text(encoding="utf-8"))


def jsonl(path):
    out = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def jsonl_checked(path):
    """回傳 (rows, problem)。problem：None｜'missing'｜'empty'｜'corrupt:N'（只容許最後一行被截斷）。"""
    p = Path(path)
    if not p.exists():
        return [], "missing"
    lines = [l for l in p.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
    if not lines:
        return [], "empty"
    rows, bad = [], 0
    for k, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except ValueError:
            if k != len(lines) - 1:
                bad += 1
    return rows, (f"corrupt:{bad}" if bad else None)


def file_problem(path):
    p = Path(path)
    if not p.exists():
        return "missing"
    return "empty" if p.stat().st_size == 0 else None


def finish(out_dir, summary, problems):
    """problems：{輸入名: 問題}；有問題就 valid=false、結束碼 1（指標已由呼叫端設成 null）。"""
    problems = {k: v for k, v in problems.items() if v}
    summary["valid"] = not problems
    summary["input_problems"] = problems
    print(write_summary(out_dir, summary))
    if problems:
        print(f"INVALID：輸入缺失或損壞 {problems}", file=sys.stderr)
        sys.exit(1)


def jload(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def pct(xs, q):
    """線性內插百分位（同 numpy 預設）。"""
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def describe(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "sd": None, "median": None, "p95": None, "min": None, "max": None}
    return {"n": len(xs), "mean": st.mean(xs), "sd": st.stdev(xs) if len(xs) > 1 else 0.0,
            "median": st.median(xs), "p95": pct(xs, 0.95), "min": min(xs), "max": max(xs)}


def rounded(obj, nd=3):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: rounded(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rounded(v, nd) for v in obj]
    return obj


def write_summary(out_dir, summary):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(rounded(summary), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                      encoding="utf-8")
    return out / "summary.json"


# ── tegrastats ──
_TS = re.compile(r"^(\d\d-\d\d-\d{4} \d\d:\d\d:\d\d)")


def tegrastats(path):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(errors="ignore").splitlines():
        m = _TS.match(line)
        if not m:
            continue
        t = dt.datetime.strptime(m.group(1), "%m-%d-%Y %H:%M:%S").replace(tzinfo=TZ).timestamp()
        ram = re.search(r"RAM (\d+)/(\d+)MB", line)
        gpu = re.search(r"GR3D_FREQ (\d+)%", line)
        cpu = re.search(r"CPU \[([^\]]+)\]", line)
        pin = re.search(r"VDD_IN (\d+)mW", line)
        tj = re.search(r"tj@([\d.]+)C", line)
        cores = [int(x.split("%")[0]) for x in cpu.group(1).split(",") if "%" in x] if cpu else []
        rows.append({"t": t, "ram": int(ram.group(1)) if ram else None, "gpu": int(gpu.group(1)) if gpu else None,
                     "cpu": st.mean(cores) if cores else None, "cpu_max_core": max(cores) if cores else None,
                     "pin": int(pin.group(1)) if pin else None, "tj": float(tj.group(1)) if tj else None})
    return rows


# ── ROS log 時間前綴：[INFO] [1791365281.586148] [tts_node]: ... ──
_ROS = re.compile(r"\[(?:INFO|WARN|ERROR|DEBUG)\] \[(\d+\.\d+)\]")


def ros_log_events(path, needle):
    out = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(errors="ignore").splitlines():
        if needle in line:
            m = _ROS.search(line)
            if m:
                out.append((float(m.group(1)), line))
    return out


# ── 文字正規化與 CER（協定 §1.3 ASR） ──
_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


_CC = None


class MissingDependency(RuntimeError):
    pass


def ref_overrides():
    """ASR 參考答案改正（bank/ref_overrides.json）：{id: 實際講法}；以 _ 開頭的鍵是說明。"""
    path = Path(__file__).resolve().parents[1] / "bank" / "ref_overrides.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def s2twp(text):
    """協定 §1.3 (a)：OpenCC s2twp（與 gateway 同套件 opencc-python-reimplemented）。缺套件就明確失敗，不靜默跳過。"""
    global _CC
    if _CC is None:
        try:
            import opencc
            _CC = opencc.OpenCC("s2twp")
        except ImportError as e:
            raise MissingDependency("缺 OpenCC：請安裝 opencc-python-reimplemented（Jetson gateway 用的同一套件），"
                                    "協定 §1.3 要求假設與參考都經 s2twp 正規化") from e
    return _CC.convert(text)


def normalize(text):
    text = s2twp(text)
    text = "".join(chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c for c in text)
    return _PUNCT.sub("", text).lower()


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ── Mac 麥克風首音（t9） ──
def mic_onsets(wav_path, mic_t0, t_sends, factor=4.0, sustain_ms=100, frame_ms=10, bounds=None):
    """每個 t_send 之後、bounds[i]（通常是下一句送出）之前，第一個 RMS 超過基線×factor 且持續 sustain_ms 的時間（Mac 時鐘）；
    窗內找不到就是 None（不會借用下一句的首音）。"""
    p = Path(wav_path)
    if not p.exists() or mic_t0 is None:
        return [None] * len(t_sends)
    with wave.open(str(p)) as w:
        sr, n, width = w.getframerate(), w.getnframes(), w.getsampwidth()
        raw = w.readframes(n)
    if width != 2:
        return [None] * len(t_sends)
    import array
    a = array.array("h", raw)
    hop = int(sr * frame_ms / 1000)
    rms = [math.sqrt(sum(x * x for x in a[i:i + hop]) / hop) for i in range(0, len(a) - hop, hop)]
    if not rms:
        return [None] * len(t_sends)
    base = st.median(rms[:max(1, int(1000 / frame_ms))]) or 1.0
    need = max(1, int(sustain_ms / frame_ms))
    out = []
    bounds = bounds or [None] * len(t_sends)
    for ts, tb in zip(t_sends, bounds):
        start = int((ts - mic_t0) * 1000 / frame_ms)
        stop = len(rms) if tb is None else min(len(rms), int((tb - mic_t0) * 1000 / frame_ms))
        hit, run = None, 0
        for k in range(max(0, start), stop):
            run = run + 1 if rms[k] > base * factor else 0
            if run >= need:
                hit = mic_t0 + (k - need + 1) * frame_ms / 1000
                break
        out.append(hit)
    return out
