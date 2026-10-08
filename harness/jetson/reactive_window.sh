#!/usr/bin/zsh
# config B 的 lidarmon:reactive 視窗：只抄 start_reactive_forward_demo.sh:70 那一行（enable:=false，不發 /cmd_vel）。
# 不啟動 act1_voice_trigger、不殺 twist_mux／joy。
source /opt/ros/humble/setup.zsh && source ~/rplidar_ws/install/setup.zsh && source ~/elder_and_dog/install/setup.zsh
ros2 run go2_robot_sdk reactive_stop_node --ros-args -p cmd_vel_topic:=/cmd_vel -p front_offset_rad:=3.14159 \
  -p front_arc_deg:=18.0 -p danger_distance_m:=1.5 -p slow_distance_m:=1.8 -p slow_speed:=0.6 -p normal_speed:=0.6 -p enable:=false
