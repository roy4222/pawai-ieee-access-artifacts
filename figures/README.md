# figures/

Plotting scripts for the data figures of the paper. Each script reads `data/` (and `bank/bank.json` for Fig. 11) and writes `figures/out/<name>.svg` and `.png`; `_style.py` holds the shared style. Requires matplotlib (tested with 3.10).

| Figure | Script | Input |
|---|---|---|
| Fig. 6 | `fig_endpoints.py` | none (schematic of endpoints t0–t8) |
| Fig. 7 | `fig_e1_cpu.py` | raw `tegrastats.log` (set `PAWAI_EXP_RUNS`) + `data/E1-*/meta.json` |
| Fig. 8 | `fig_e1_resources.py` | `data/E1-A-r1..r3`, `data/E1-B-r1..r2` |
| Fig. 9 | `fig_e1_nodes.py` | `data/E1-A-r1..r2`, `data/E1-B-r1..r2` |
| Fig. 10 | `fig_e2a_asr.py` | `data/E2a-local`, `data/E2a-remote-r2` |
| Fig. 11 | `fig_e2b_llm.py` | `data/E2b-prelim`, `data/E2b-7b`, `bank/bank.json` |
| Fig. 12 | `fig_e3_timeline.py` | `data/E3-chat-r1` |
| Fig. 13 | `fig_e4_failures.py` | `data/E4`, `data/E3-chat-r1` |
| Fig. 14 | `fig_e5_arbitration.py` | `data/E5-agent`, `data/E5-attended` |
| Fig. 15 | `fig_e1_runs.py` | raw `tegrastats.log` (set `PAWAI_EXP_RUNS`) + `data/E1-*/meta.json` |

Figs. 7 and 15 need the raw resource logs, which are not distributed (available on request); all other figures run from the released files.
