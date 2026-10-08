#!/usr/bin/bash
# pawexp down 的 Jetson 端：收 tmux session、pkill 清單、驗證無殘留。用法：bash cleanup_exp.sh [--verify-only] [--force]
# 清單＝cleanup.sh:54-73 的 12 個＋G1 漏網 7 個＋實驗工具自己的程序（設計 §3.3）。不碰 ollama serve、往 RTX 8000 的 ssh 通道。
SESSIONS="exp lidarmon act1react demo pawai_brain studio_gw llm-e2e"
PATS=(conversation_graph_node tts_node studio_gateway stt_intent_node llm_bridge_node interaction_executive
      go2_driver robot_state pointcloud joy_node teleop twist_mux
      face_identity_node vision_perception_node object_perception_node realsense2_camera_node depth_safety_node
      foxglove_bridge static_transform_publisher
      sllidar_node reactive_stop_node topic_logger.py topic_probe.py tegrastats "ros2 bag" brain_node
      inject_arbitration.py bench_asr_local.py bench_tts.py exp_window.sh llm_window.sh tts_window.sh reactive_window.sh
      "ros2 launch" "ros2 run" _ros2_daemon)
pat() { echo "[${1:0:1}]${1:1}"; }   # pkill -f 用 [x]yz 樣式，指令列含有這串字的程序（例如 ssh 的 zsh -c）不會被比對到
residual() {
  # 排除本腳本、ssh 的 zsh -c 外殼
  ps -eo pid=,args= | grep -v -E "cleanup_exp.sh|grep|zsh -c" | while read -r pid args; do
    for p in "${PATS[@]}"; do [[ "$args" == *"$p"* ]] && { echo "$pid $args"; break; }; done
  done
}
if [[ "$1" != "--verify-only" ]]; then
  for s in $SESSIONS; do tmux kill-session -t "$s" 2>/dev/null; done
  sleep 2
  # ros2 CLI（health 用的 ros2 topic info）會留下 _ros2_daemon
  ( source /opt/ros/humble/setup.bash >/dev/null 2>&1 && ros2 daemon stop >/dev/null 2>&1 ) || true
  for p in "${PATS[@]}"; do pkill -9 -f "$(pat "$p")" 2>/dev/null; done
  sleep 2
  if [[ "$1" == "--force" || "$2" == "--force" ]]; then
    for p in "${PATS[@]}"; do pkill -9 -f "$(pat "$p")" 2>/dev/null; done; sleep 2
  fi
fi
FAIL=0
R=$(residual)
if [[ -n "$R" ]]; then echo "RESIDUAL_PROCS:"; echo "$R"; FAIL=1; fi
LEFT=$(tmux ls -F '#{session_name}' 2>/dev/null | grep -x -E "$(echo $SESSIONS | tr ' ' '|')")
if [[ -n "$LEFT" ]]; then echo "RESIDUAL_SESSIONS: $LEFT"; FAIL=1; fi
echo "FREE_USED_MB $(free -m | awk '/^Mem:/{print $3}')"
exit $FAIL
