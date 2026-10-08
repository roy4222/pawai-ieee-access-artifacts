# 照 scripts/start_full_demo_tmux.sh 的 ROS_SETUP（每個 pane 重新 source .env）；由其他腳本 source
WORKDIR=/home/jetson/elder_and_dog
source /opt/ros/humble/setup.zsh && source $WORKDIR/install/setup.zsh && export LD_LIBRARY_PATH=$HOME/.local/ctranslate2-cuda/lib:${LD_LIBRARY_PATH:-} && { [[ -f $WORKDIR/.env ]] && set -a && source $WORKDIR/.env && set +a; } || true
