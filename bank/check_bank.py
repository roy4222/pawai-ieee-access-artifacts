#!/usr/bin/env python3
"""句庫檢查：禁用子字串、短問候、chat 子集額外禁字、欄位完整。禁用字規則以 ast 讀自 make_bank.py（單一來源）。
用法：python3 -I bank/check_bank.py bank/bank.json   回傳 0＝通過，1＝有違規
"""
import ast
import json
import sys
from collections import Counter
from pathlib import Path

SUBSETS = {"chat", "skill", "status"}


def rules():
    tree = ast.parse((Path(__file__).with_name("make_bank.py")).read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("FORBID", "GREET", "CHAT_EXTRA"):
                out[name] = ast.literal_eval(node.value)
    missing = {"FORBID", "GREET", "CHAT_EXTRA"} - out.keys()
    if missing:
        sys.exit(f"make_bank.py 找不到 {missing}")
    return out


def check(item, R):
    text = item.get("text", "")
    low = text.lower()
    hits = [w for w in R["FORBID"] if w in low]
    if len(text) <= 6 and any(g in low for g in R["GREET"]):
        hits.append("短問候（長度 ≤ 6 且含問候詞）")
    if item.get("subset") == "chat":
        hits += [f"chat 禁字「{w}」" for w in R["CHAT_EXTRA"] if w in text]
    for k in ("id", "text", "subset", "mode", "gold_skill"):
        if k not in item:
            hits.append(f"缺欄位 {k}")
    if item.get("subset") not in SUBSETS:
        hits.append(f"subset 不合法：{item.get('subset')}")
    if "mode" in item and not (isinstance(item["mode"], str) and item["mode"]):
        hits.append("mode 必須是非空字串（classify_mode 的輸出）")
    if not isinstance(item.get("gold_skill", []), list):
        hits.append("gold_skill 必須是陣列")
    return hits


def main(path):
    R = rules()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["items"] if isinstance(data, dict) else data
    bad = 0
    dup = [i for i, n in Counter(x.get("id") for x in items).items() if n > 1]
    if dup:
        print(f"重複 id：{dup}")
        bad += 1
    for it in items:
        hits = check(it, R)
        if hits:
            bad += 1
            print(f"✗ {it.get('id')} 「{it.get('text')}」 命中：{'、'.join(hits)}")
    print(f"{'id':<5} {'subset':<7} {'mode':<20} gold_skill  text")
    for it in items:
        print(f"{it.get('id', ''):<5} {it.get('subset', ''):<7} {it.get('mode', ''):<20} "
              f"{','.join(it.get('gold_skill') or []) or '-':<11} {it.get('text', '')}")
    c = Counter(it.get("subset") for it in items)
    print(f"total={len(items)}  " + "  ".join(f"{k}={c.get(k, 0)}" for k in ("chat", "skill", "status")))
    if bad:
        print(f"FAIL：{bad} 筆違規")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
