#!/usr/bin/env python3
"""E2c：TTS 三層合成時間（設計 §4.5）。匯入 tts_node 的 provider 與 TTSConfig（參數照 DEMO:86-89 與 .env），不播放。
用法（先 source ros_env.zsh）：python3 bench_tts.py --texts texts.json --tiers gemini,edge,piper --out tts.jsonl
texts.json：[{"id","text"}] 或 [{"id","reply"}]；Gemini 保留 audio tag，其餘兩層照 tts_node 去 tag。
每筆：{"tier","id","ok","synth_ms","audio_s","bytes"[,"via","error"]}
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import wave

PIPER_MODEL = "/home/jetson/models/piper/zh_CN-huayan-medium.onnx"


def audio_seconds(data):
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        pass
    with tempfile.NamedTemporaryFile(suffix=".mp3") as f:  # edge-tts 回 MP3
        f.write(data)
        f.flush()
        p = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", f.name],
                           capture_output=True, text=True)
        try:
            return float(p.stdout.strip())
        except ValueError:
            return None


def providers(tiers):
    import speech_processor.tts_node as T
    try:
        from speech_processor.audio_tag import strip_audio_tags
    except ImportError:
        def strip_audio_tags(t):
            return re.sub(r"\[[a-z_]+\]", "", t).strip()
    cfg = T.TTSConfig(api_key="", edge_tts_voice=os.environ.get("EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
                      piper_model_path=PIPER_MODEL, piper_config_path=PIPER_MODEL + ".json", use_cache=False,
                      openrouter_gemini_voice=os.environ.get("OPENROUTER_GEMINI_VOICE", "Despina"),
                      openrouter_gemini_model=os.environ.get("OPENROUTER_GEMINI_MODEL", "google/gemini-3.1-flash-tts-preview"),
                      openrouter_gemini_timeout_s=float(os.environ.get("OPENROUTER_GEMINI_TIMEOUT_S", "6.0")))
    out = {}
    for t in tiers:
        try:
            if t == "gemini":
                out[t] = (T.TTSProvider_OpenRouterGemini(cfg), lambda s: s, "class")
            elif t == "edge":
                out[t] = (T.TTSProvider_EdgeTTS(cfg), strip_audio_tags, "class")
            elif t == "piper":
                out[t] = (T.TTSProvider_Piper(cfg), strip_audio_tags, "class")
        except Exception as e:
            print(f"[bench_tts] {t} 建構失敗：{e}", flush=True)
            if t == "piper":  # B 案：piper Python 模組直呼
                from piper import PiperVoice
                voice = PiperVoice.load(PIPER_MODEL)

                class _Mod:
                    def synthesize(self, text):
                        buf = io.BytesIO()
                        with wave.open(buf, "wb") as w:
                            voice.synthesize_wav(text, w)
                        return buf.getvalue()
                out[t] = (_Mod(), strip_audio_tags, "module")
            else:
                out[t] = (None, None, f"init_failed: {e}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--texts", required=True)
    ap.add_argument("--tiers", default="gemini,edge,piper")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    texts = json.load(open(a.texts, encoding="utf-8"))
    provs = providers([t for t in a.tiers.split(",") if t])
    out = open(a.out, "a", encoding="utf-8")
    for tier, (p, prep, via) in provs.items():
        for i, it in enumerate(texts):
            tid = it.get("id", f"t{i:02d}")
            text = it.get("text") or it.get("reply") or ""
            rec = {"tier": tier, "id": tid, "text": text, "via": via}
            if p is None:
                rec.update(ok=False, error=via)
            else:
                t0 = time.perf_counter()
                try:
                    data = p.synthesize(prep(text))
                    ms = (time.perf_counter() - t0) * 1000
                    rec.update(ok=bool(data), synth_ms=ms, bytes=len(data or b""),
                               audio_s=audio_seconds(data) if data else None)
                except Exception as e:
                    rec.update(ok=False, synth_ms=(time.perf_counter() - t0) * 1000, error=f"{type(e).__name__}: {str(e)[:200]}")
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(tier, tid, rec["ok"], round(rec.get("synth_ms") or 0), rec.get("audio_s"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
