#!/usr/bin/zsh
# exp session 的視窗。用法：zsh exp_window.sh tegra|free|ps|log|hz|bag <run_dir> [topics(逗號分隔)]
K=$1; D=$2; T=$3
mkdir -p $D && cd $D
case "$K" in
  tegra) exec tegrastats --interval 1000 --logfile $D/tegrastats.log ;;
  free) while true; do echo "@@ $(date +%s.%N) $(free -m | awk '/^Mem:/{print $7}')"; sleep 5; done >> $D/free.log ;;
  ps) while true; do echo "@@ $(date +%s.%N)"; ps -eo pid,pcpu,pmem,rss,etimes,comm,args --sort=-pcpu | head -40; sleep 5; done >> $D/ps.log ;;
  log) source /home/jetson/exp/ros_env.zsh; exec python3 /home/jetson/exp/topic_logger.py --out $D/topics.jsonl --topics $T ;;
  hz) source /home/jetson/exp/ros_env.zsh
      while true; do
        python3 /home/jetson/exp/topic_probe.py 30 ${(s:,:)T} 2>/dev/null | python3 -c 'import json,sys,time
try: d=json.load(sys.stdin)
except Exception as e: d={"error":repr(e)}
print(json.dumps({"t_end":time.time(),"probe":d}),flush=True)' >> $D/hz.jsonl
      done ;;
  bag) source /home/jetson/exp/ros_env.zsh; exec ros2 bag record -o $D/bag ${(s:,:)T} ;;
  *) echo "unknown kind $K"; exit 2 ;;
esac
