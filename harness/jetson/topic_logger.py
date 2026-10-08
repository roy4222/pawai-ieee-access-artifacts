#!/usr/bin/env python3
"""把多個 topic 的每筆訊息寫成一行 JSON：{"t_recv", "topic", "data"}（設計 §4.1）。
型別不寫死：執行時查 topic 型別、動態載入；topic 還沒有發布者就每 5 s 重試。
用法：python3 topic_logger.py --out topics.jsonl --topics /a,/b,...   或   --selftest（不需 ROS）
"""
import argparse
import json
import sys
import time

# 只記到達時間與 kind，不存 payload（避免檔案過大）
META_ONLY = {"/event/gesture_detected", "/event/pose_detected", "/event/object_detected", "/state/perception/face", "/cmd_vel"}


def to_record(topic, data, t_recv=None):
    """data：String 的內容（str）或已轉成 dict 的訊息。能解析 JSON 的字串轉物件，否則保留原字串。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            pass
    if topic in META_ONLY:
        kind = None
        if isinstance(data, dict):
            kind = data.get("event_type") or data.get("kind") or data.get("gesture") or data.get("pose")
        data = {"kind": kind}
    return {"t_recv": time.time() if t_recv is None else t_recv, "topic": topic, "data": data}


def selftest():
    a = to_record("/brain/chat_candidate", '{"a":1}')
    b = to_record("/tts", "not json")
    for r in (a, b):
        print(json.dumps(r, ensure_ascii=False))
    ok = (a["data"] == {"a": 1} and b["data"] == "not json"
          and all(isinstance(r["t_recv"], float) and set(r) == {"t_recv", "topic", "data"} for r in (a, b)))
    return 0 if ok else 1


def run(out_path, topics):
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    from rosidl_runtime_py import message_to_ordereddict
    from rosidl_runtime_py.utilities import get_message

    out = open(out_path, "a", encoding="utf-8")

    class Logger(Node):
        def __init__(self):
            super().__init__("pawexp_topic_logger")
            self.pending = list(topics)
            self.subs = []
            self.t0, self.ready = time.time(), False
            self.create_timer(1.0, self.try_subscribe)

        def try_subscribe(self):
            self._subscribe()
            # DDS 探索約需數秒：啟動滿 3 s 後的那一輪訂閱完成才寫 .ready（record start／play 等它，第一句才不會漏記）
            if not self.ready and time.time() - self.t0 >= 3.0:
                self.ready = True
                with open(out_path + ".ready", "w") as f:
                    json.dump({"t": time.time(), "subscribed": len(self.subs), "pending": self.pending}, f)

        def _subscribe(self):
            if not self.pending:
                return
            types = dict(self.get_topic_names_and_types())
            for t in list(self.pending):
                if t not in types:
                    continue
                cls = get_message(types[t][0])
                pubs = self.get_publishers_info_by_topic(t)
                if not pubs:
                    continue  # 有型別但還沒有發布者：等下一輪，才知道該用哪種 reliability
                # 發布者全為 RELIABLE 就用 RELIABLE 訂閱（BEST_EFFORT 會掉訊息），否則 BEST_EFFORT 才相容
                rel = all(p.qos_profile.reliability == ReliabilityPolicy.RELIABLE for p in pubs)
                q = QoSProfile(depth=50, reliability=ReliabilityPolicy.RELIABLE if rel else ReliabilityPolicy.BEST_EFFORT,
                               durability=DurabilityPolicy.VOLATILE, history=HistoryPolicy.KEEP_LAST)
                self.subs.append(self.create_subscription(cls, t, lambda m, t=t: self.cb(t, m), q))
                self.pending.remove(t)
                self.get_logger().info(f"subscribed {t} [{types[t][0]}] {'RELIABLE' if rel else 'BEST_EFFORT'}")

        def cb(self, topic, msg):
            now = time.time()
            if topic == "/cmd_vel":
                data = None
            elif hasattr(msg, "data") and isinstance(msg.data, str):
                data = msg.data
            else:
                data = dict(message_to_ordereddict(msg))
            out.write(json.dumps(to_record(topic, data, now), ensure_ascii=False, default=str) + "\n")
            out.flush()

    rclpy.init()
    node = Logger()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        out.close()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--topics", default="")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if not a.out or not a.topics:
        ap.error("需要 --out 與 --topics")
    run(a.out, [t for t in a.topics.split(",") if t])
