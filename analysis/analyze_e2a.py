#!/usr/bin/env python3
"""E2a ASR（協定 §1.3 ASR）。用法：python3 -I analyze_e2a.py <run_dir> <out_dir> [--mode formal|acceptance]
讀 asr.jsonl（bench asr）與 play.jsonl（play --mode speech 的 gateway 回覆）。參考與假設都經 OpenCC s2twp（必要依賴，
缺就結束碼 2）、去標點空白、全形數字轉半形、英文小寫。意圖保留率用 gateway 同一個 IntentClassifier（paths.pawai_src）。
formal（預設）：每層第 1 遍前 3 句為暖機丟棄；acceptance：保留全部。
參考答案：bank/ref_overrides.json 列的句子改用實際講法（Q7，Roy 10/8 聽錄音確認）。
"""
import argparse
import importlib.util
import sys
from pathlib import Path

_s = importlib.util.spec_from_file_location("_common", Path(__file__).with_name("_common.py"))
C = importlib.util.module_from_spec(_s)
_s.loader.exec_module(C)


def classifier():
    src = Path(C.config()["paths"]["pawai_src"]) / "speech_processor" / "speech_processor"
    if not (src / "intent_classifier.py").exists():
        raise C.MissingDependency(f"找不到 IntentClassifier：{src}/intent_classifier.py（paths.pawai_src）")
    sys.path.insert(0, str(src))
    from intent_classifier import IntentClassifier
    clf = IntentClassifier()

    def intent(text):  # 與 gateway 相同：unknown 視為 chat
        m = clf.classify(C.s2twp(text or ""))
        return m.intent if m.intent != "unknown" else "chat"
    return intent


def score(rows, intent):
    """rows：[(ref, hyp, latency_s, audio_s)]；CER 微平均＝總編輯距離／總參考字數。"""
    dist = ref_len = exact = same_intent = 0
    per = []
    for ref, hyp, _, _ in rows:
        same_intent += intent(ref) == intent(hyp or "")
        r, h = C.normalize(ref), C.normalize(hyp or "")
        d = C.edit_distance(r, h)
        dist += d
        ref_len += len(r)
        exact += r == h
        per.append(d / len(r) if r else 0.0)
    lat = [x[2] for x in rows if x[2] is not None]
    rtf = [x[2] / x[3] for x in rows if x[2] is not None and x[3]]
    return {"n": len(rows), "cer_micro": dist / ref_len if ref_len else None,
            "cer_per_sentence_mean": sum(per) / len(per) if per else None,
            "sentence_acc": exact / len(rows) if rows else None,
            "intent_retention": same_intent / len(rows) if rows else None,
            "latency_s": {k: C.describe(lat)[k] for k in ("median", "p95")}, "rtf_median": C.describe(rtf)["median"]}


def main(run_dir, out_dir, mode="formal"):
    keep_warmup = mode == "acceptance"
    run = Path(run_dir)
    tiers = {}
    seen = {}
    ov = C.ref_overrides()
    for r in C.jsonl(run / "asr.jsonl"):
        k = seen.setdefault(r["tier"], 0)
        seen[r["tier"]] += 1
        if not keep_warmup and r.get("pass") == 1 and k < 3:
            continue
        tiers.setdefault(r["tier"], []).append(
            (ov.get(r.get("id"), r.get("ref", "")), (r.get("hyp_tw") or r.get("hyp")) if r.get("ok") else "",
             (r.get("latency_ms") or 0) / 1000 if r.get("ok") else None, r.get("audio_s")))
    fails = {t: sum(1 for r in C.jsonl(run / "asr.jsonl") if r["tier"] == t and not r.get("ok")) for t in seen}
    gw = [(ov.get(p.get("id"), p.get("text", "")), (p.get("reply") or {}).get("asr", ""),
           ((p.get("reply") or {}).get("latency_ms") or 0) / 1000 if (p.get("reply") or {}).get("published") else None, None)
          for p in C.jsonl(run / "play.jsonl")]
    asr, asr_problem = C.jsonl_checked(run / "asr.jsonl")
    plays, play_problem = C.jsonl_checked(run / "play.jsonl")
    if asr_problem and play_problem:
        return C.finish(out_dir, {"run": run.name, "mode": mode, "tiers": {}, "gateway_play": None},
                        {"asr.jsonl": asr_problem, "play.jsonl": play_problem})
    try:
        intent = classifier()
        summary = {"run": run.name, "mode": mode, "warmup_dropped": not keep_warmup,
                   "tiers": {t: {**score(rows, intent), "failures": fails.get(t, 0)} for t, rows in sorted(tiers.items())},
                   "gateway_play": score(gw, intent) if gw else None}
    except C.MissingDependency as e:
        print(f"FAIL：{e}", file=sys.stderr)
        sys.exit(2)
    C.finish(out_dir, summary, {})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--mode", choices=["formal", "acceptance"], default="formal")
    a = ap.parse_args()
    main(a.run_dir, a.out_dir, a.mode)
