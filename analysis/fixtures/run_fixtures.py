#!/usr/bin/env python3
"""H07 測試入口：在暫存目錄重新生成 fixtures、跑分析，與 repo 內的 expected*.json 比對（不改 repo）。
用法：python3 -I analysis/fixtures/run_fixtures.py   全部相同回 0，否則印差異回 1。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANALYSIS = HERE.parent
CASES = [("E1", "e1", [], "expected.json"), ("E3", "e3", [], "expected.json"),
         ("E3", "e3", ["--mode", "acceptance"], "expected_acceptance.json"), ("E5", "e5", [], "expected.json")]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pawexp-fixtures-"))
    env = dict(os.environ, TZ="Asia/Taipei")
    subprocess.run([sys.executable, "-I", str(HERE / "make_fixtures.py"), str(tmp)], check=True, env=env,
                   capture_output=True)
    bad = 0
    for d, exp, extra, expected in CASES:
        out = tmp / f"out-{exp}-{expected}"
        p = subprocess.run([sys.executable, "-I", str(ANALYSIS / f"analyze_{exp}.py"), str(tmp / d), str(out), *extra],
                           capture_output=True, text=True, env=env)
        got = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        want = json.loads((HERE / d / expected).read_text(encoding="utf-8"))
        ok = p.returncode == 0 and got == want
        bad += not ok
        print(f"{'OK ' if ok else 'DIFF'} {d} {' '.join(extra) or '(formal)'} → {expected} rc={p.returncode}")
        if not ok:
            for k in sorted(set(got) | set(want)):
                if got.get(k) != want.get(k):
                    print(f"     {k}: got={json.dumps(got.get(k), ensure_ascii=False)[:200]} "
                          f"want={json.dumps(want.get(k), ensure_ascii=False)[:200]}")
    print(f"tmp={tmp}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
