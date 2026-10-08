#!/usr/bin/env python3
"""E1 共存資源（協定 §1.3 資源、§2）。用法：python3 -I analyze_e1.py <run_dir> <out_dir>
段落：meta.t_start（label＝meta.label）到第一個 mark，之後每個 mark 起一段，到 meta.t_end。每段去掉前 60 s 為穩態。
"""
import importlib.util
import re
import statistics as st
import sys
from pathlib import Path

_s = importlib.util.spec_from_file_location("_common", Path(__file__).with_name("_common.py"))
C = importlib.util.module_from_spec(_s)
_s.loader.exec_module(C)

WARMUP_S = 60


def segments(meta, marks):
    bounds = [(meta.get("label") or "run", meta["t_start"])] + [(m["label"], m["t"]) for m in marks]
    out = []
    for i, (label, t0) in enumerate(bounds):
        t1 = bounds[i + 1][1] if i + 1 < len(bounds) else meta["t_end"]
        out.append((label, t0, t1))
    return out


def resource(rows, t0, t1):
    w = [r for r in rows if t0 + WARMUP_S <= r["t"] < t1]
    d = lambda k: C.describe([r[k] for r in w])
    sat = [r["cpu_max_core"] for r in w if r["cpu_max_core"] is not None]
    return {"n_samples": len(w), "t0": t0, "t1": t1,
            "cpu": d("cpu"), "gpu": d("gpu"), "ram_mb": d("ram"),
            "core_saturation_frac": (sum(1 for x in sat if x >= 90) / len(sat)) if sat else None,
            "power_mw_mean": d("pin")["mean"], "tj_max": d("tj")["max"]}


def parse_ps(path, mapping):
    """ps.log：'@@ <t>' 起一個快照；每行 pid pcpu pmem rss etimes comm args…。回傳 [(t, {node: (pcpu, rss_kb)})]。"""
    snaps, cur, t = [], None, None
    p = Path(path)
    if not p.exists():
        return snaps
    for line in p.read_text(errors="ignore").splitlines():
        if line.startswith("@@"):
            if cur is not None:
                snaps.append((t, cur))
            t, cur = float(line.split()[1]), {}
            continue
        parts = line.split(None, 6)
        if cur is None or len(parts) < 7 or not re.match(r"^\d+$", parts[0]):
            continue
        args = parts[6]
        if "ros2 launch" in args:  # launch 父程序不算進節點
            continue
        for key, name in mapping.items():
            if key in args:
                pc, rss = cur.get(name, (0.0, 0))
                cur[name] = (pc + float(parts[1]), rss + int(parts[3]))
                break
    if cur is not None:
        snaps.append((t, cur))
    return snaps


def nodes(snaps, t0, t1):
    acc = {}
    for t, m in snaps:
        if t0 + WARMUP_S <= t < t1:
            for name, (pc, rss) in m.items():
                acc.setdefault(name, ([], []))
                acc[name][0].append(pc)
                acc[name][1].append(rss / 1024)
    return {k: {"cpu_pct_mean": st.mean(v[0]), "rss_mb_mean": st.mean(v[1]), "n": len(v[0])} for k, v in sorted(acc.items())}


def rates(hz, t0, t1):
    per = {}
    for rec in hz:
        for topic, r in (rec.get("probe") or {}).items():
            if not (isinstance(r, dict) and "count" in r):
                continue
            t_end = rec.get("t_end", 0)
            if not (t0 + WARMUP_S <= t_end - r.get("window_s", 30) and t_end <= t1 + 1):
                continue  # 整個 30 s 窗都要落在穩態內（用窗的起點判斷）
            per.setdefault(topic, []).append(r["count"] / r.get("window_s", 30))
    out = {}
    for topic, xs in sorted(per.items()):
        m = st.mean(xs)
        out[topic] = {"mean_hz": m, "cv": (st.stdev(xs) / m) if len(xs) > 1 and m > 0 else None,
                      "min_window_hz": min(xs), "n_windows": len(xs),
                      "min_ge_half_mean": (min(xs) >= 0.5 * m) if m > 0 else None}
    return out


def free_available(path, t0, t1):
    """free.log：'@@ <t> <available MB>'，每 5 s 一筆；回傳穩態內 available 的最小值與筆數。"""
    xs = []
    p = Path(path)
    if p.exists():
        for line in p.read_text(errors="ignore").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == "@@":
                try:
                    t, av = float(parts[1]), float(parts[2])
                except ValueError:
                    continue
                if t0 + WARMUP_S <= t < t1:
                    xs.append(av)
    return {"min": min(xs) if xs else None, "n": len(xs)}


def oom_count(path):
    try:
        return int(Path(path).read_text().strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return None


WAIVABLE = {"free": "free.log"}   # 可放寬的輸入（舊版工具沒有收集的）；其他覆蓋檢查照舊嚴格


def main(run_dir, out_dir, allow_missing=()):
    waived = [WAIVABLE[k] for k in allow_missing]
    run = Path(run_dir)
    meta = C.jload(run / "meta.json", {})
    marks = C.jload(run / "marks.json", [])
    rows = C.tegrastats(run / "tegrastats.log")
    snaps = parse_ps(run / "ps.log", C.config().get("nodes", {}))
    hz, hz_problem = C.jsonl_checked(run / "hz.jsonl")
    problems = {"meta.json": None if meta.get("t_start") and meta.get("t_end") else "missing t_start/t_end",
                "tegrastats.log": None if rows else (C.file_problem(run / "tegrastats.log") or "unparsable"),
                "ps.log": None if snaps else (C.file_problem(run / "ps.log") or "unparsable"),
                "hz.jsonl": hz_problem, "panes_end.txt": C.file_problem(run / "panes_end.txt"),
                "dmesg_before.txt": C.file_problem(run / "dmesg_before.txt"),
                "dmesg_after.txt": C.file_problem(run / "dmesg_after.txt"),
                "free.log": None if "free.log" in waived else C.file_problem(run / "free.log")}
    if problems["meta.json"]:
        return C.finish(out_dir, {"run": run.name, "segments": {}, "stability": {"stable": None}}, problems)
    panes = (run / "panes_end.txt").read_text(errors="ignore").split() if (run / "panes_end.txt").exists() else []
    dead = [panes[i] for i in range(0, len(panes) - 1, 2) if panes[i + 1] != "0"]
    ob, oa = oom_count(run / "dmesg_before.txt"), oom_count(run / "dmesg_after.txt")
    segs = {}
    for label, t0, t1 in segments(meta, marks):
        r = resource(rows, t0, t1)
        f = rates(hz, t0, t1)
        segs[label] = {**r, "nodes": nodes(snaps, t0, t1), "rates": f,
                       "free_available_mb": None if "free.log" in waived else free_available(run / "free.log", t0, t1)}
    # 覆蓋檢查：每段穩態都要有 tegrastats 樣本、ps 快照、頻率窗；任一段 n=0 → invalid
    gaps = [f"{label}: {what} n=0" for label, s in segs.items()
            for what, n in (("tegrastats", s["n_samples"]), ("ps", sum(v["n"] for v in s["nodes"].values())),
                            ("hz", sum(v["n_windows"] for v in s["rates"].values())),
                            ("free", None if "free.log" in waived else s["free_available_mb"]["n"]))
            if n == 0]
    problems["coverage"] = "；".join(gaps) or None
    tj_max = max((s["tj_max"] for s in segs.values() if s["tj_max"] is not None), default=None)
    rate_ok = [v["min_ge_half_mean"] for s in segs.values() for v in s["rates"].values()]
    stable = {"no_new_oom": (oa - ob == 0) if ob is not None and oa is not None else None,
              "panes_alive": (not dead) if not problems["panes_end.txt"] and panes else None, "dead_panes": dead,
              "rates_min_ge_half_mean": (all(x is not False for x in rate_ok) if rate_ok else None),
              "tj_below_85": (tj_max < 85) if tj_max is not None else None}
    keys = ("no_new_oom", "panes_alive", "rates_min_ge_half_mean", "tj_below_85")
    # 任一項不可判定 → stable 為 null（不可判定），不是 true
    stable["stable"] = None if any(stable[k] is None for k in keys) or problems["coverage"] else all(stable[k] for k in keys)
    diff = None
    if "full" in segs and "console" in segs:
        diff = {k: (segs["full"][k]["mean"] - segs["console"][k]["mean"])
                if segs["full"][k]["mean"] is not None and segs["console"][k]["mean"] is not None else None
                for k in ("cpu", "gpu", "ram_mb")}
        a, b = segs["full"]["power_mw_mean"], segs["console"]["power_mw_mean"]
        diff["power_mw"] = a - b if a is not None and b is not None else None
    summary = {"run": meta.get("run", run.name), "config": meta.get("config"), "warmup_s": WARMUP_S,
               "segments": segs, "stability": stable, "asr_cost_full_minus_console": diff, "waived": waived}
    C.finish(out_dir, summary, problems)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--allow-missing", action="append", default=[], choices=sorted(WAIVABLE),
                    help="明確放寬某項輸入（free＝舊版工具沒有 free.log）；summary 會記 waived，該項輸出 null")
    a = ap.parse_args()
    main(a.run_dir, a.out_dir, a.allow_missing)
