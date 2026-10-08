#!/usr/bin/zsh
# demo:llm 視窗：主線參數（DEMO:229-235，launch 檔、其餘 launch 預設），可用 key:=value 覆寫。
# 用法：zsh llm_window.sh [openrouter_gemini_model:=X] [openrouter_deepseek_model:=Y] [openrouter_request_timeout_s:=Z]
source /home/jetson/exp/ros_env.zsh
P=${PAWAI_LLM_MODEL:-openai/gpt-5.4-mini}; F=${PAWAI_LLM_FALLBACK_MODEL:-google/gemini-3-flash-preview}; T=4.0
for kv in "$@"; do
  case "$kv" in
    openrouter_gemini_model:=*) P=${kv#*:=} ;;
    openrouter_deepseek_model:=*) F=${kv#*:=} ;;
    openrouter_request_timeout_s:=*) T=${kv#*:=} ;;
    *) echo "unknown override $kv"; exit 2 ;;
  esac
done
echo "[pawexp] llm_window primary=$P fallback=$F timeout=$T"
ros2 launch pawai_brain pawai_conversation_graph.launch.py \
  openrouter_gemini_model:=$P openrouter_deepseek_model:=$F \
  openrouter_request_timeout_s:=$T openrouter_overall_budget_s:=5.0 \
  llm_max_tokens:=2000 chat_history_max_turns:=5
