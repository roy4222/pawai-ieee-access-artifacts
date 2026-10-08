"""bench asr|llm|tts（設計 §3.6）：檔案推到 Jetson run 目錄，在 tmux exp 的 bench 視窗跑（ssh 斷線也不中斷），輪詢完成後 rsync 回來。"""
import ast
import hashlib
import json
import re
import shlex
import time
from pathlib import Path

from . import ops
from .core import REPO, Fail, run_dir, say

PERSONA_BASE = ["IDENTITY.md", "MISSION.md", "STYLE.md", "OUTPUT.md", "EXAMPLES.md"]


def prompt_json(cfg):
    """照 conversation_graph_node._load_persona（目錄模式）：base＝5 檔以空行串接；另附 CAPABILITIES 與兩段 mode 提示。"""
    src = Path(cfg["paths"]["pawai_src"])
    pdir = src / "pawai_brain" / "personas" / "v1"
    if not pdir.is_dir():
        raise Fail("bench llm", f"找不到 persona 目錄 {pdir}（paths.pawai_src）")
    base = "\n\n".join((pdir / f).read_text(encoding="utf-8") for f in PERSONA_BASE)
    node = (src / "pawai_brain" / "pawai_brain" / "conversation_graph_node.py").read_text(encoding="utf-8")
    scaffold = next(ast.literal_eval(n.value) for n in ast.parse(node).body
                    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_INTRO_SCAFFOLD")
    m = re.search(r'"(\[mode_hint\][^"]*)"\s*"([^"]*)"', node)
    if not m:
        raise Fail("bench llm", "conversation_graph_node.py 找不到 [mode_hint] 字串")
    return {"base": base, "cap": (pdir / "CAPABILITIES.md").read_text(encoding="utf-8"),
            "identity_hint": m.group(1) + m.group(2), "intro_scaffold": scaffold,
            "base_sha": hashlib.sha256(base.encode()).hexdigest()[:12], "source": str(pdir)}


def bank_subset(cfg, limit):
    data = json.loads((REPO / cfg["paths"]["bank"]).read_text(encoding="utf-8"))
    data["items"] = data["items"][:limit] if limit else data["items"]
    return data


def launch(r, cmd, jd, step, poll_s=10, limit_s=4 * 3600):
    """在 exp session 開 bench 視窗跑 cmd；結束碼寫到 bench.rc，Mac 端輪詢。"""
    wrapped = f"{cmd}; echo $? > {jd}/bench.rc"
    script = f"{jd}/bench_cmd.sh"
    r.ssh(f"rm -f {jd}/bench.rc; printf '%s\\n' {shlex.quote(wrapped)} > {script}", f"{step}: 寫指令檔")
    if "exp" in r.sessions():
        r.ssh(f"tmux new-window -t exp -n bench 'zsh {script}'", f"{step}: 開 exp:bench")
    else:
        r.ssh(f"tmux new-session -d -s exp -n bench 'zsh {script}'", f"{step}: 開 exp:bench")
    if r.dry:
        return 0
    t0 = time.time()
    while True:
        p = r.ssh(f"cat {jd}/bench.rc 2>/dev/null; tail -1 {jd}/bench.log 2>/dev/null", f"{step}: poll",
                  check=False, quiet=True, timeout=30)
        lines = p.stdout.strip().splitlines()
        if lines and lines[0].strip().isdigit() and not lines[0].startswith(" "):
            rc = int(lines[0].strip())
            break
        if time.time() - t0 > limit_s:
            raise Fail(step, f"{limit_s} s 內沒跑完（exp:bench 仍在 Jetson 上）")
        if lines:
            print(f"  … {lines[-1][:120]}", flush=True)
        time.sleep(poll_s)
    r.ssh("tmux kill-window -t exp:bench 2>/dev/null; tmux list-windows -t exp >/dev/null 2>&1 || true", f"{step}: 收視窗",
          check=False, quiet=True)
    return rc


def bench(r, cfg, a):
    jd = ops.jrun(r, a.run)
    local = run_dir(cfg, a.run)
    ops.sync_scripts(r)
    r.ssh(f"mkdir -p {jd}", "bench: mkdir")
    fn = {"llm": bench_llm, "asr": bench_asr, "tts": bench_tts}[a.what]
    keep = ("tiers", "passes", "limit", "backends", "temperature", "max_tokens", "num_ctx", "warmup", "idle_s", "texts", "wav_dir")
    with ops.run_meta(r, cfg, a.run, f"bench {a.what}", {k: getattr(a, k, None) for k in keep}, lambda: 0):
        return fn(r, cfg, a, jd, local)


def push_json(r, obj, local_path, remote_path, step):
    Path(local_path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    r.scp(local_path, remote_path, step)


def bench_llm(r, cfg, a, jd, local):
    pairs = []
    for spec in [s for s in a.backends.split(",") if s]:
        backend, _, model = spec.partition(":")
        if backend not in ("ollama", "vllm", "openrouter") or not model:
            raise Fail("bench llm", f"--backends 格式是 backend:model，收到 {spec!r}")
        pairs += [backend, model]
    if not pairs:
        raise Fail("bench llm", "需要 --backends，例如 openrouter:openai/gpt-5.4-mini,ollama:qwen2.5:1.5b")
    pj = prompt_json(cfg)
    push_json(r, pj, local / "prompt.json", f"{jd}/prompt.json", "bench llm: prompt.json")
    push_json(r, bank_subset(cfg, a.limit), local / "bank.json", f"{jd}/bank.json", "bench llm: bank.json")
    common = (f"--passes {a.passes} --warmup {a.warmup} --temperature {a.temperature} --max-tokens {a.max_tokens} "
              f"--num-ctx {a.num_ctx}")
    say(f"bench llm：{len(pairs) // 2} 層 × {a.passes} 遍；persona base_sha={pj['base_sha']}；取樣 {a.temperature}/{a.max_tokens}")
    cmd = (f"bash {r.exp}/run_llm_bench.sh {jd} {a.idle_s} {shlex.quote(common)} "
           + " ".join(shlex.quote(x) for x in pairs) + f" > {jd}/bench.log 2>&1")
    rc = launch(r, cmd, jd, "bench llm")
    r.rsync_back(jd, local, "bench llm: rsync", timeout=600)
    ops.unload_ollama(r)
    if rc != 0:
        raise Fail("bench llm", f"Jetson 端結束碼 {rc}（看 {local}/bench.log）")
    n = sum(1 for _ in open(local / "llm.jsonl", encoding="utf-8")) if (local / "llm.jsonl").exists() else 0
    want = len(pairs) // 2 * a.passes * len(bank_subset(cfg, a.limit)["items"])
    if n != want:
        raise Fail("bench llm", f"llm.jsonl {n} 筆，預期 {want}（層 × 遍 × 句）")
    say(f"bench llm 完成：llm.jsonl {n} 筆 → {local}")
    return 0


def wav_dir(cfg, a):
    return Path(a.wav_dir or (REPO / cfg["paths"].get("bank_wav", "bank/wav")))


def bench_asr(r, cfg, a, jd, local):
    items = bank_subset(cfg, a.limit)["items"]
    wd = wav_dir(cfg, a)
    missing = [it["id"] for it in items if not (wd / f"{it['id']}.wav").exists()]
    if missing:
        raise Fail("bench asr", f"{wd} 缺 WAV：{missing[:10]}")
    r.ssh(f"mkdir -p {r.exp}/bank/wav", "bench asr: mkdir bank")
    r.run(["rsync", "-a"] + [str(wd / f"{it['id']}.wav") for it in items] + [f"{r.jetson}:{r.exp}/bank/wav/"],
          "bench asr: rsync wav")
    push_json(r, {"items": items}, local / "bank.json", f"{jd}/bank.json", "bench asr: bank.json")
    cmd = (f"source {r.exp}/ros_env.zsh; python3 {r.exp}/bench_asr_local.py --bank {jd}/bank.json --wav-dir {r.exp}/bank/wav "
           f"--tiers {shlex.quote(a.tiers)} --passes {a.passes} --out {jd}/asr.jsonl > {jd}/bench.log 2>&1")
    rc = launch(r, cmd, jd, "bench asr", poll_s=5)
    r.rsync_back(jd, local, "bench asr: rsync")
    if rc != 0:
        raise Fail("bench asr", f"Jetson 端結束碼 {rc}（看 {local}/bench.log）")
    say(f"bench asr 完成 → {local}/asr.jsonl")
    return 0


def bench_tts(r, cfg, a, jd, local):
    if not a.texts:
        raise Fail("bench tts", "需要 --texts <json>（[{id,text}] 或含 reply 的 llm.jsonl）")
    src = Path(a.texts).expanduser()
    if not src.exists():
        raise Fail("bench tts", f"找不到 {src}")
    texts = json.loads(src.read_text(encoding="utf-8"))
    texts = texts[:a.limit] if a.limit else texts
    push_json(r, texts, local / "tts_texts.json", f"{jd}/tts_texts.json", "bench tts: texts")
    tiers = a.tiers if a.tiers != "remote,sv_local,whisper_gpu" else "gemini,edge,piper"
    cmd = (f"source {r.exp}/ros_env.zsh; python3 {r.exp}/bench_tts.py --texts {jd}/tts_texts.json "
           f"--tiers {shlex.quote(tiers)} --out {jd}/tts.jsonl > {jd}/bench.log 2>&1")
    rc = launch(r, cmd, jd, "bench tts", poll_s=5)
    r.rsync_back(jd, local, "bench tts: rsync")
    if rc != 0:
        raise Fail("bench tts", f"Jetson 端結束碼 {rc}（看 {local}/bench.log）")
    say(f"bench tts 完成 → {local}/tts.jsonl")
    return 0
