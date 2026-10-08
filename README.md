# PawAI — IEEE Access evaluation artifacts

Evaluation artifacts for the manuscript

> P.-Y. Lu and W.-F. Tung, "A Multimodal Quadruped Robot with Hybrid Edge–Cloud Deployment and Deterministic LLM Skill Arbitration," *IEEE Access* [under review].

The robot software evaluated in the paper lives in a separate repository, **[roy4222/PawAI](https://github.com/roy4222/PawAI)**, at commit `81e50185d82caa52ccee8652ab97e0e4d7db6605` (tag `ieee-access-2026`). This repository holds only what is needed to check and regenerate the evaluation: the measurement harness, the utterance bank and its reference transcripts, the injected arbitration cases, the analysis scripts, and the per-run summaries behind Tables VI–IX.

## Layout

| Folder | Contents |
|---|---|
| `harness/` | `pawexp`, the measurement harness run from a workstation (`python3 -m pawexp {env-check,up,down,record,play,bench,inject,fault}`), the helper scripts it copies to the Jetson (`harness/jetson/`), and offline tests (`harness/tests/test_offline.py`). Configuration: `harness/pawexp.toml` (placeholders) and `harness/pawexp.example.toml` (every field explained). |
| `bank/` | The 40-utterance Traditional Chinese bank (`bank.json`: text, dialogue mode, acceptable skills), `ref_overrides.json` (the corrected reference transcript of utterance c10, used for CER), the generator `make_bank.py`, and the rule checker `check_bank.py`. **No audio is included.** |
| `cases/` | The injected arbitration cases of Section VI-E (`e5_cases.json`) and their generator `make_cases.py`. |
| `analysis/` | `analyze_e1.py` … `analyze_e5.py` turn raw run directories into `summary.json`; `_common.py` holds shared parsing and statistics (standard library only; `analyze_e2a.py` additionally needs `opencc-python-reimplemented` and a PawAI checkout). `fixtures/` regenerates synthetic runs and checks the scripts against expected outputs. |
| `data/` | One `summary.json` per run (plus `meta.json` for the resource runs), exactly as produced by `analysis/`. |
| `figures/` | Placeholder; plotting scripts will be added when the paper figures are final. |

## Paper ↔ files

| Paper item | Run(s) in `data/` | Script | Fields |
|---|---|---|---|
| Table VI (steady-state resources) | `E1-A-r1`, `E1-A-r2` (full and evaluated configurations), `E1-B-r1`, `E1-B-r2` (LiDAR configuration) | `analyze_e1.py` | `segments.{full,console,lidar}.{cpu,gpu,ram_mb,…}`, `nodes.*`, `rates.*`, `stability` |
| Excluded runs (Section VI-A) | `E1-A-r3` (robot driver idle), `E1-B-r3` (stopped after 152 s; see `meta.json`) | `analyze_e1.py` | kept for transparency, not averaged |
| Fig. 6 (CPU over time) | `E1-A-r1`, `E1-B-r1` | — | needs the raw `tegrastats.log` (see *Raw data*) |
| Section VII-C, person in view | `E1-person` | `analyze_e1.py` | `segments.*.nodes` |
| Table VII, ASR rows | `E2a-local` (on-board tiers, n = 77), `E2a-remote-r2` (remote tier via the tunnel, n = 77), `E3-chat-r1` (remote tier via the gateway, n = 47) | `analyze_e2a.py`, `analyze_e3.py` | `tiers.*.{cer_micro,sentence_acc,intent_retention,latency_s}` |
| Table VII, LLM rows | `E2b-prelim` (two cloud models and six Ollama models, n = 40 each), `E2b-7b` (Qwen2.5-7B-Instruct on the remote server) | harness `bench llm` (`harness/jetson/llm_pilot.py`) | `summary[]` |
| Fig. 7 (latency per segment of a spoken turn) | `E3-chat-r1` | `analyze_e3.py` | `rows`, `policy_gate_src_ms`, `first_audio_approx_t6_t1`, `end_to_end_upper_t7_t1` |
| Table VIII (failure scenarios S1–S7) | `E4` | `analyze_e4.py` | `baseline`, `scenarios` |
| Table IX (injected arbitration cases) | `E5-agent` (43 unattended cases, 53 events), `E5-roy` (17 attended cases) | `analyze_e5.py` + `cases/e5_cases.json` | `categories`, `out_of_policy_dispatch`, `false_block`, `trace_completeness` |

Example — the LiDAR column of Table VI from the released summaries:

```bash
python3 -I -c "import json,statistics as s; v=[json.load(open(f'data/E1-B-r{i}/summary.json'))['segments']['lidar']['cpu'] for i in (1,2)]; m=[x['mean'] for x in v]; print(round(s.mean(m),1), round(s.stdev(m),1), round(s.mean([x['p95'] for x in v]),1))"
# 85.1 3.3 92.1
```

## What can be reproduced where

**Offline (any machine with Python ≥ 3.11):**
- Recompute every number in Tables VI–IX from `data/*/summary.json`.
- Check the analysis code: `python3 -I analysis/fixtures/run_fixtures.py` (regenerates synthetic runs and compares with `analysis/fixtures/*/expected*.json`).
- Run the harness tests: `python3 harness/tests/test_offline.py`. Two tests need `paths.pawai_src` in `harness/pawexp.toml` to point at a PawAI checkout at `81e5018`; the end-to-end CER test also needs a Python with `opencc-python-reimplemented` (set `PAWEXP_OPENCC_PYTHON`).
- Validate the bank: `python3 -I bank/check_bank.py bank/bank.json`.

**On the robot (re-running the experiments):** requires a Unitree Go2 Pro (firmware v1.1.7), an NVIDIA Jetson Orin Nano Super with the sensors of Table IV, PawAI at `81e5018` built on the Jetson, a GPU server running PawAI's `scripts/sensevoice_server.py`, accounts for the cloud services of Table IV, and your own recordings of the bank (one 16-kHz mono WAV per utterance id in `bank/wav/`). Fill in `harness/pawexp.toml` from `harness/pawexp.example.toml`, then use `python3 -m pawexp` from `harness/`. The harness starts PawAI through its own scripts (`scripts/start_full_demo_tmux.sh`, `scripts/start_lidar_monitor_tmux.sh`) and is therefore tied to that revision.

### Known caveats

- Paths on the Jetson side (`harness/jetson/*.sh`, `bench_*.py`) assume the default `jetson` account (`/home/jetson/exp`, `/home/jetson/elder_and_dog`, `/home/jetson/models`), matching PawAI's own scripts.
- `paths.tts_cache` must be the directory that `tts_node` actually writes to, which depends on its working directory. During the paper runs the configured path was not the live cache; the speech-synthesis cache was cleared by moving it aside before the scenarios that required it (Section VI-D).

## Not included

- **Cloud services and models** (OpenRouter GPT-5.4 mini, Gemini 3 Flash, Gemini 3.1 Flash TTS, Microsoft Edge TTS): need your own accounts, may be billed, and preview models may be withdrawn.
- **Third-party model weights** (YuNet, SFace, MediaPipe, YOLO26n, SenseVoiceSmall, Whisper tiny, Piper zh_CN-huayan-medium, Qwen2.5-7B-Instruct, Ollama models): download under their own licenses.
- **Robot firmware** and the Unitree Sport API.

## Raw data

The bank recordings (one author's voice) and the raw per-run logs (tegrastats, process snapshots, ROS 2 topic logs, decision traces, transcripts) are not distributed; they are available from the corresponding author on reasonable request.

## License

Code (`harness/`, `analysis/`, `cases/*.py`, `bank/*.py`): BSD 2-Clause, see `LICENSE`.
Data (`data/`, `bank/*.json`, `cases/*.json`): CC BY 4.0, see `LICENSE-DATA`.

## Citation

See `CITATION.cff`. Please also cite the PawAI software release.
