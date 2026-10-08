#!/usr/bin/env python3
"""play --snapshot：從 /face_identity/debug_image（sensor_msgs/Image）抓一幀存 JPEG，當在場證據。
用法（先 source ros_env.zsh）：python3 snapshot.py --out snap_c01.jpg [--topic /face_identity/debug_image] [--timeout 3]
結束碼：0 存檔成功；1 逾時沒收到影像；2 轉檔失敗。只訂閱，不發布任何訊息。
"""
import argparse
import sys
import time


def to_bgr(msg):
    import numpy as np
    buf = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    enc = msg.encoding.lower()
    if enc in ("bgr8", "rgb8"):
        img = buf.reshape(msg.height, msg.step // 3, 3)[:, :msg.width]
        return img[:, :, ::-1] if enc == "rgb8" else img
    if enc in ("mono8", "8uc1"):
        return buf.reshape(msg.height, msg.step)[:, :msg.width]
    raise ValueError(f"不支援的 encoding {msg.encoding}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--topic", default="/face_identity/debug_image")
    ap.add_argument("--timeout", type=float, default=3.0)
    a = ap.parse_args()
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image
    rclpy.init()
    node = rclpy.create_node("pawexp_snapshot")
    got = []
    node.create_subscription(Image, a.topic, lambda m: got.append(m), qos_profile_sensor_data)
    end = time.time() + a.timeout
    while not got and time.time() < end:
        rclpy.spin_once(node, timeout_sec=0.1)
    node.destroy_node()
    rclpy.try_shutdown()
    if not got:
        print(f"snapshot: {a.timeout}s 內沒收到 {a.topic}", file=sys.stderr)
        return 1
    try:
        import cv2
        if not cv2.imwrite(a.out, to_bgr(got[0])):
            raise OSError("cv2.imwrite 回傳 False")
    except Exception as e:
        print(f"snapshot: 轉檔失敗 {e}", file=sys.stderr)
        return 2
    print(f"snapshot: {a.out} {got[0].width}x{got[0].height} {got[0].encoding}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
