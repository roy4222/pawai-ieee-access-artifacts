#!/usr/bin/env python3
"""E2a：ASR 三層（設計 §4.2）。直接匯入 stt_intent_node 的 provider 類別，參數照 speech_processor.yaml 與 DEMO。
用法（先 source ros_env.zsh）：python3 bench_asr_local.py --bank bank.json --wav-dir wav --tiers remote,sv_local,whisper_gpu --passes 2 --out asr.jsonl
每筆：{"tier","pass","id","hyp","latency_ms","audio_s","ok"[,"error"]}；import 失敗以 3 結束。
"""
import argparse
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, "/home/jetson/elder_and_dog/pawai-studio/gateway")
try:
    from text_normalization import to_traditional_tw  # 與 gateway 相同的 OpenCC s2twp
except ImportError:
    to_traditional_tw = None
SV_DIR = "/home/jetson/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"


def make(tier):
    from speech_processor.stt_intent_node import QwenASRProvider, SenseVoiceLocalProvider, WhisperLocalProvider
    if tier == "remote":
        return QwenASRProvider(base_url="http://127.0.0.1:8001/v1/audio/transcriptions", api_key="", timeout_sec=3.0,
                               model_name="sensevoice", response_text_field="text")
    if tier == "sv_local":
        return SenseVoiceLocalProvider(model_path=f"{SV_DIR}/model.int8.onnx", tokens_path=f"{SV_DIR}/tokens.txt",
                                       language="zh", num_threads=4)
    if tier == "whisper_gpu":
        return WhisperLocalProvider(model_name="tiny", timeout_sec=4.0, language="zh", device="cuda",
                                    compute_type="float16", cpu_threads=4)
    raise SystemExit(f"unknown tier {tier}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", required=True)
    ap.add_argument("--wav-dir", required=True)
    ap.add_argument("--tiers", default="remote,sv_local,whisper_gpu")
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    items = json.load(open(a.bank, encoding="utf-8"))["items"]
    audio = {}
    for it in items:
        b = (Path(a.wav_dir) / f"{it['id']}.wav").read_bytes()
        with wave.open(str(Path(a.wav_dir) / f"{it['id']}.wav")) as w:
            audio[it["id"]] = (b, w.getframerate(), w.getnframes() / w.getframerate())
    out = open(a.out, "a", encoding="utf-8")
    for tier in [t for t in a.tiers.split(",") if t]:
        try:
            p = make(tier)
        except ImportError as e:
            print(f"[bench_asr] import 失敗：{e}", flush=True)
            return 3
        for ps in range(1, a.passes + 1):
            for it in items:
                wav_bytes, sr, dur = audio[it["id"]]
                rec = {"tier": tier, "pass": ps, "id": it["id"], "ref": it["text"], "audio_s": dur}
                t0 = time.perf_counter()
                try:
                    r = p.transcribe(wav_bytes, sr, "zh")
                    rec.update(ok=True, hyp=r.text, hyp_tw=to_traditional_tw(r.text) if to_traditional_tw else None,
                               latency_ms=(time.perf_counter() - t0) * 1000,
                               provider_latency_ms=r.latency_ms)
                except Exception as e:
                    rec.update(ok=False, hyp="", latency_ms=(time.perf_counter() - t0) * 1000,
                               error=f"{type(e).__name__}: {str(e)[:200]}")
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                print(tier, ps, it["id"], rec["ok"], round(rec["latency_ms"]), rec["hyp"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
