#!/usr/bin/zsh
# demo:tts 視窗：主線參數（DEMO:207-216），可用 key:=value 覆寫（openrouter_gemini_model、edge_tts_voice）。
source /home/jetson/exp/ros_env.zsh
V=${EDGE_TTS_VOICE:-zh-CN-XiaoxiaoNeural}; EXTRA=()
for kv in "$@"; do
  case "$kv" in
    edge_tts_voice:=*) V=${kv#*:=} ;;
    openrouter_gemini_model:=*) EXTRA=(-p "$kv") ;;
    *) echo "unknown override $kv"; exit 2 ;;
  esac
done
echo "[pawexp] tts_window voice=$V extra=$EXTRA"
amixer -c 3 set PCM 147 >/dev/null 2>&1
ros2 run speech_processor tts_node --ros-args \
  -p provider:=${TTS_PROVIDER:-edge_tts} -p edge_tts_voice:=$V \
  -p piper_model_path:=/home/jetson/models/piper/zh_CN-huayan-medium.onnx \
  -p piper_config_path:=/home/jetson/models/piper/zh_CN-huayan-medium.onnx.json \
  -p local_playback:=${LOCAL_PLAYBACK:-true} -p local_output_device:=${LOCAL_OUTPUT_DEVICE:-plughw:CD002AUDIO,0} \
  -p playback_method:=datachannel $EXTRA
