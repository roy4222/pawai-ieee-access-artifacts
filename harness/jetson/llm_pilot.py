#!/usr/bin/env python3
"""E2b：LLM 延遲（設計 §4.4；主腦 r14 pilot 擴充）。Jetson 上跑，只用 stdlib，每次呼叫寫一行 JSON。
用法：python3 llm_pilot.py --out llm.jsonl --bank bank.json --prompt-json prompt.json [--passes 2 --warmup 3
       --temperature 0.8 --max-tokens 2000 --num-ctx 10240 --cutoff 0 --limit N] backend model [backend model ...]
backend：ollama | vllm | openrouter。user message 照 conversation_graph_node._build_user_message 的模式注入規則。
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

CAP_MODES = ("capability_question", "action_request", "self_intro_request")


def user_msg(P, mode, text, env_line):
    parts = [f"[語音] 使用者說：「{text}」", env_line]
    if mode in CAP_MODES:
        parts.append("[能力描述]\n" + P["cap"])
    if mode == "identity":
        parts.append(P["identity_hint"])
    if mode == "self_intro_request":
        parts.append(P["intro_scaffold"])
    return "\n".join(parts)


def stream(url, body, headers, parse, cutoff):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.perf_counter()
    ttft, text, final = None, "", None
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            piece, fin = parse(line)
            if piece:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                text += piece
            if fin is not None:
                final = fin
            if cutoff and time.perf_counter() - t0 > cutoff:
                raise TimeoutError(f"client cutoff {cutoff:.0f}s after {len(text)} chars")
    return ttft, time.perf_counter() - t0, text, final


def p_ollama(line):
    o = json.loads(line)
    return (o.get("message") or {}).get("content", ""), (o if o.get("done") else None)


def p_openai(line):
    if not line.startswith("data:"):
        return "", None
    d = line[5:].strip()
    if d == "[DONE]":
        return "", {}
    o = json.loads(d)
    ch = (o.get("choices") or [{}])[0]
    return (ch.get("delta") or {}).get("content") or "", (o.get("usage") and o or None)


def call(a, P, backend, model, mode, text):
    msgs = [{"role": "system", "content": P["base"]}, {"role": "user", "content": user_msg(P, mode, text, a.env_line)}]
    if backend == "ollama":
        body = {"model": model, "messages": msgs, "stream": True, "think": False,
                "options": {"temperature": a.temperature, "num_predict": a.max_tokens, "num_ctx": a.num_ctx}}
        return stream("http://localhost:11434/api/chat", body, {"Content-Type": "application/json"}, p_ollama, a.cutoff)
    if backend == "vllm":
        body = {"model": model, "messages": msgs, "stream": True, "temperature": a.temperature, "max_tokens": a.max_tokens}
        return stream("http://localhost:8000/v1/chat/completions", body, {"Content-Type": "application/json"}, p_openai, a.cutoff)
    key = os.environ.get("OPENROUTER_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
    body = {"model": model, "messages": msgs, "stream": True, "temperature": a.temperature, "max_tokens": a.max_tokens,
            "usage": {"include": True}}
    return stream("https://openrouter.ai/api/v1/chat/completions", body,
                  {"Content-Type": "application/json", "Authorization": "Bearer " + key}, p_openai, a.cutoff)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--prompt-json", required=True)
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--warmup", type=int, default=3, help="第 1 遍前 N 句標 phase=warmup（分析時棄）")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--num-ctx", type=int, default=10240)
    ap.add_argument("--cutoff", type=float, default=0.0, help="單句用戶端截斷秒數，0＝不截")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--env-line", default="[環境] 台北 晚上 20:00")
    ap.add_argument("pairs", nargs="+", help="backend model [backend model ...]")
    a = ap.parse_args()
    if len(a.pairs) % 2:
        ap.error("backend 與 model 要成對")
    P = json.load(open(a.prompt_json, encoding="utf-8"))
    items = json.load(open(a.bank, encoding="utf-8"))["items"][:a.limit]
    out = open(a.out, "a", encoding="utf-8")
    for backend, model in zip(a.pairs[::2], a.pairs[1::2]):
        k = 0
        for ps in range(1, a.passes + 1):
            for it in items:
                phase = "warmup" if ps == 1 and k < a.warmup else "measure"
                k += 1
                rec = {"backend": backend, "model": model, "temperature": a.temperature, "max_tokens": a.max_tokens,
                       "num_ctx": a.num_ctx, "pass": ps, "bank_id": it["id"], "gold_skill": it.get("gold_skill"),
                       "phase": phase, "mode": it["mode"], "utterance": it["text"], "wall_start": time.time()}
                try:
                    ttft, total, reply, final = call(a, P, backend, model, it["mode"], it["text"])
                    rec.update(ok=True, ttft_s=ttft, total_s=total, reply=reply)
                    if backend == "ollama" and final:
                        for f in ("load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration"):
                            rec[f] = final.get(f)
                    elif final and final.get("usage"):
                        rec["usage"] = final["usage"]
                except urllib.error.HTTPError as e:
                    rec.update(ok=False, error=f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'ignore')}")
                except Exception as e:
                    rec.update(ok=False, error=f"{type(e).__name__}: {str(e)[:300]}")
                rec["wall_end"] = time.time()
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                print(backend, model, ps, it["id"], phase, rec["ok"], round(rec.get("total_s") or 0, 2), flush=True)
        if backend == "ollama":  # 卸載，下一個模型冷啟動、RAM 可比
            try:
                urllib.request.urlopen(urllib.request.Request(
                    "http://localhost:11434/api/generate", data=json.dumps({"model": model, "keep_alive": 0}).encode(),
                    headers={"Content-Type": "application/json"}), timeout=30).read()
            except Exception:
                pass
            time.sleep(5)


if __name__ == "__main__":
    main()
