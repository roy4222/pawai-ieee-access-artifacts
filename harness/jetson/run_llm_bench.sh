#!/usr/bin/bash
# bench llm 的 Jetson 端流程（照主腦 run_pilot.sh）：idle 基線、tegrastats 全程、free/ollama ps 前後、每個模型一段 phases。
# 用法：bash run_llm_bench.sh <run_dir> <idle_s> "<llm_pilot 共用參數>" backend model [backend model ...]
set -uo pipefail
D=$1; IDLE=$2; COMMON=$3; shift 3
mkdir -p "$D"; cd "$D"
date -Iseconds > start.txt; nvpmodel -q > nvpmodel.txt 2>&1; free -m > free_before.txt; ollama ps > ollama_ps_before.txt 2>&1
tegrastats --interval 1000 --logfile "$D/tegrastats.log" & TS=$!
trap 'kill $TS 2>/dev/null' EXIT
echo "idle_start $(date +%s.%N)" > phases.txt
sleep "$IDLE"
echo "idle_end $(date +%s.%N)" >> phases.txt
set -a; . ~/elder_and_dog/.env >/dev/null 2>&1; set +a
RC=0
while [ $# -ge 2 ]; do
  b=$1; m=$2; shift 2
  echo "model_start $b:$m $(date +%s.%N)" >> phases.txt
  # shellcheck disable=SC2086
  python3 /home/jetson/exp/llm_pilot.py --out llm.jsonl --bank bank.json --prompt-json prompt.json $COMMON "$b" "$m" \
    > "log_${b}_${m//[:.\/]/_}.txt" 2>&1
  c=$?; [ $c -ne 0 ] && { echo "child_rc $b:$m $c" >> phases.txt; RC=$c; }
  [ "$b" = ollama ] && ollama ps >> ollama_ps_during.txt 2>&1
  echo "model_end $b:$m $(date +%s.%N)" >> phases.txt
done
[ -s llm.jsonl ] || { echo "llm.jsonl 不存在或為空" >&2; [ $RC -eq 0 ] && RC=3; }
free -m > free_after.txt; date -Iseconds > end.txt; echo "rc=$RC" > done.txt
exit $RC
