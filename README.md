# PawAI — IEEE Access evaluation artifacts

Evaluation artifacts for the manuscript

> P.-Y. Lu and W.-F. Tung, "A Multimodal Quadruped Robot with Hybrid Edge–Cloud Deployment and Deterministic LLM Skill Arbitration," *IEEE Access* [under review].

The robot software evaluated in the paper lives in a separate repository, **[roy4222/PawAI](https://github.com/roy4222/PawAI)**, at commit `81e50185d82caa52ccee8652ab97e0e4d7db6605` (tag `ieee-access-2026`). This repository holds only what is needed to check and regenerate the evaluation: the measurement harness, the utterance bank and its reference transcripts, the injected arbitration cases, the analysis scripts, the per-run summaries, and the plotting scripts behind the evaluation (Section VI: Tables VII–XIII, Figs. 6–14; appendices: Tables XIV–XVI, Fig. 15).

## Layout

| Folder | Contents |
|---|---|
| `harness/` | `pawexp`, the measurement harness run from a workstation (`python3 -m pawexp {env-check,up,down,record,play,bench,inject,fault}`), the helper scripts it copies to the Jetson (`harness/jetson/`), and offline tests (`harness/tests/test_offline.py`). Configuration: `harness/pawexp.toml` (placeholders) and `harness/pawexp.example.toml` (every field explained). |
| `bank/` | The 40-utterance Traditional Chinese bank (`bank.json`: text, dialogue mode, acceptable skills), `ref_overrides.json` (the corrected reference transcript of utterance c10, used for CER), the generator `make_bank.py`, and the rule checker `check_bank.py`. **No audio is included.** |
| `cases/` | The injected arbitration cases of Section VI-E (`e5_cases.json`) and their generator `make_cases.py`. |
| `analysis/` | `analyze_e1.py` … `analyze_e5.py` turn raw run directories into `summary.json`; `_common.py` holds shared parsing and statistics (standard library only; `analyze_e2a.py` additionally needs `opencc-python-reimplemented` and a PawAI checkout). `fixtures/` regenerates synthetic runs and checks the scripts against expected outputs. |
| `data/` | One `summary.json` per run (plus `meta.json` for the resource runs), exactly as produced by `analysis/`. |
| `figures/` | Plotting scripts for the data figures (Figs. 6–15) and the shared style `_style.py`; output goes to `figures/out/` (not tracked). |

## Paper ↔ files

Table and figure numbers follow the submitted manuscript.

| Paper item | Run(s) in `data/` | Script | Fields / notes |
|---|---|---|---|
| Table VI (experimental design) | — | — | design table; endpoints defined in Fig. 6 and `analysis/analyze_e3.py` |
| Table VII (steady-state resources), Figs. 8–9 | `E1-A-r1`, `E1-A-r2` (full and evaluated configurations), `E1-B-r1`, `E1-B-r2` (LiDAR configuration) | `analyze_e1.py` | `segments.{full,console,lidar}.{cpu,gpu,ram_mb,…}`, `nodes.*`, `rates.*`, `stability`, `asr_cost_full_minus_console` |
| Fig. 7 (CPU over time, first runs) | `E1-A-r1`, `E1-B-r1` (segment bounds from `meta.json`) | `analyze_e1.py` (parser) | needs the raw `tegrastats.log` (see *Raw data*) |
| Excluded runs: Section VI-A protocol deviations (a)–(b), Appendix A, Fig. 15 | `E1-A-r3` (robot driver idle), `E1-B-r3` (stopped after 152 s; see `meta.json`) | `analyze_e1.py` | kept for transparency, not averaged; Fig. 15 needs the raw `tegrastats.log` |
| Section VI-B, run with a person in view | `E1-person` | `analyze_e1.py` | `segments.*.nodes` |
| Table VIII, Fig. 10 (speech-recognition tiers) | `E2a-local` (on-board tiers, n = 77), `E2a-remote-r2` (remote tier, n = 77); gateway row: `E3-chat-r1` (n = 47) | `analyze_e2a.py`, `analyze_e3.py` | `tiers.*.{cer_micro,intent_retention,latency_s}`; the remote tier's CER and intent retention on the 35 returned runs come from the raw per-utterance log (constants in `figures/fig_e2a_asr.py`) |
| Table IX, Fig. 11, Table XIV (dialogue-model tiers) | `E2b-prelim` (two cloud models, six Ollama models, n = 40 each), `E2b-7b` (Qwen2.5-7B-Instruct on the remote server) | harness `bench llm` (`harness/jetson/llm_pilot.py`) | `summary[]`, `per_call[]`; subsets from `bank/bank.json` |
| Fig. 12 (timeline of a spoken turn, from t0) | `E3-chat-r1` | `analyze_e3.py` | `rows[]` (warm-up rows dropped, n = 47) |
| Table X, Fig. 13, Table XVI (failure scenarios S1–S7) | `E4` (baseline from `E3-chat-r1`) | `analyze_e4.py` | `baseline`, `scenarios.*` (S4, S5 from `run_override`) |
| Tables XI–XII, Fig. 14, Table XV (injected arbitration cases) | `E5-agent` (43 unattended cases, 53 events), `E5-attended` (17 attended cases, 17 events) | `analyze_e5.py` + `cases/e5_cases.json` | `categories`, `events[]`, `false_block`, `trace_completeness`; the attended C7 out-of-policy count is re-judged from the recorded depth flag (Table XI caption), so `out_of_policy_dispatch` of `E5-attended` is not read; the total arbitration overhead of Table XII is computed from the raw topic log |
| Table XIII (summary of findings) | all of the above | — | — |

### Figures ↔ scripts

Run from any directory, e.g. `python3 figures/fig_e1_resources.py` (needs matplotlib; tested with 3.10). Outputs SVG and PNG to `figures/out/`.

| Figure | Script | Input |
|---|---|---|
| Fig. 6 (measurement endpoints) | `fig_endpoints.py` | none (schematic) |
| Fig. 7 (CPU over time) | `fig_e1_cpu.py` | raw `tegrastats.log` of `E1-A-r1`, `E1-B-r1` under `$PAWAI_EXP_RUNS`, plus `data/*/meta.json` |
| Fig. 8 (steady-state resources) | `fig_e1_resources.py` | `data/E1-A-r{1,2,3}`, `data/E1-B-r{1,2}` |
| Fig. 9 (per-node CPU) | `fig_e1_nodes.py` | `data/E1-A-r{1,2}`, `data/E1-B-r{1,2}` |
| Fig. 10 (speech-recognition tiers) | `fig_e2a_asr.py` | `data/E2a-local`, `data/E2a-remote-r2` |
| Fig. 11 (dialogue-model tiers) | `fig_e2b_llm.py` | `data/E2b-prelim`, `data/E2b-7b`, `bank/bank.json` |
| Fig. 12 (timeline of a spoken turn) | `fig_e3_timeline.py` | `data/E3-chat-r1` |
| Fig. 13 (behavior under failures) | `fig_e4_failures.py` | `data/E4`, `data/E3-chat-r1` |
| Fig. 14 (injected arbitration cases) | `fig_e5_arbitration.py` | `data/E5-agent`, `data/E5-attended` |
| Fig. 15 (second and excluded runs) | `fig_e1_runs.py` | raw `tegrastats.log` of `E1-A-r{2,3}`, `E1-B-r{2,3}` under `$PAWAI_EXP_RUNS` |

Figs. 1–5 are architecture diagrams and a photograph, not generated from data.

Example — the LiDAR column of Table VII from the released summaries:

```bash
python3 -I -c "import json,statistics as s; v=[json.load(open(f'data/E1-B-r{i}/summary.json'))['segments']['lidar']['cpu'] for i in (1,2)]; m=[x['mean'] for x in v]; print(round(s.mean(m),1), round(s.stdev(m),1), round(s.mean([x['p95'] for x in v]),1))"
# 85.1 3.3 92.1
```

## What can be reproduced where

**Offline (any machine with Python ≥ 3.11):**
- Recompute the numbers in Tables VII–XVI from `data/*/summary.json`, except the few values noted above that come from raw logs.
- Redraw Figs. 6 and 8–14 with `figures/*.py` (Figs. 7 and 15 need the raw logs).
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
