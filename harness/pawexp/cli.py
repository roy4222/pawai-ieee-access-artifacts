"""pawexp：8 個子指令（設計 §3）。成功回 0；任何失敗印 [pawexp] FAIL 並回 2。"""
import argparse
import shlex
import sys

from .core import Fail, Runner, load_config, log_line

CONFIGS = ("A", "B")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="只印指令與寫狀態檔，不連線")
    common.add_argument("--limit", type=int, default=None, help="play／bench／inject 只取前 N 句或案")
    common.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    common.add_argument("--validate-only", action="store_true", help=argparse.SUPPRESS)

    ap = argparse.ArgumentParser(prog="pawexp", description="PawAI 實驗工具（r14）")
    sub = ap.add_subparsers(dest="cmd", metavar="{env-check,up,down,record,play,bench,inject,fault}")

    sub.add_parser("env-check", parents=[common], help="唯讀環境檢查（E0 十項）＋時鐘差")

    p = sub.add_parser("up", parents=[common], help="起 demo（config A／B）")
    p.add_argument("--config", required=True, help="A 或 B")
    p.add_argument("--kill", default="", help="逗號分隔：asr,llm,executive")
    p.add_argument("--clear-tts-cache", action="store_true")
    p.add_argument("--rpl-port", default=None)

    p = sub.add_parser("down", parents=[common], help="收掉 demo 並驗證 Jetson 無殘留")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("record", parents=[common], help="record start|mark|stop")
    p.add_argument("action", choices=["start", "mark", "stop"])
    p.add_argument("--run", required=True)
    p.add_argument("--label", default="")

    p = sub.add_parser("play", parents=[common], help="經 /ws/speech 或 /ws/text 重播句庫")
    p.add_argument("--run", required=True)
    p.add_argument("--subset", default="chat", help="chat|skill|status|all，可逗號分隔")
    p.add_argument("--passes", type=int, default=1)
    p.add_argument("--mode", choices=["speech", "text"], default="speech")
    p.add_argument("--wav-dir", default=None, help="WAV 資料夾（預設 paths.bank_wav）")
    p.add_argument("--record-mic", action="store_true")
    p.add_argument("--confirm-each", action="store_true")
    p.add_argument("--snapshot", action="store_true")
    p.add_argument("--keep-tts-cache", action="store_true", help="不在每遍開始前清 TTS 快取")

    p = sub.add_parser("bench", parents=[common], help="bench asr|llm|tts")
    p.add_argument("what", choices=["asr", "llm", "tts"])
    p.add_argument("--run", required=True)
    p.add_argument("--tiers", default="remote,sv_local,whisper_gpu")
    p.add_argument("--passes", type=int, default=1)
    p.add_argument("--wav-dir", default=None)
    p.add_argument("--backends", default="")
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--max-tokens", type=int, default=2000)
    p.add_argument("--num-ctx", type=int, default=10240)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--idle-s", type=int, default=60, help="bench llm 前的 idle 基線秒數")
    p.add_argument("--texts", default=None)

    p = sub.add_parser("inject", parents=[common], help="E5 仲裁注入")
    p.add_argument("--run", required=True)
    p.add_argument("--cases", required=True)
    p.add_argument("--only", default="")
    p.add_argument("--confirm-each", action="store_true")

    p = sub.add_parser("fault", parents=[common], help="fault apply|revert|status <name>")
    p.add_argument("action", choices=["apply", "revert", "status", "ack"])
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--no-sudo", action="store_true", help="強制用啟動參數版本（等同 jetson.sudo=false）")
    return ap


def dispatch(a):
    cfg = load_config()
    r = Runner(cfg, dry_run=a.dry_run)
    if a.cmd == "env-check":
        from . import ops
        return ops.env_check(r, cfg, a)
    if a.cmd == "up":
        if a.config not in CONFIGS:
            raise Fail("up", f"--config 必須是 A 或 B，收到 {a.config!r}")
        bad = [k for k in a.kill.split(",") if k and k not in ("asr", "llm", "executive")]
        if bad:
            raise Fail("up", f"--kill 只接受 asr,llm,executive，收到 {bad}")
        from . import ops
        return ops.up(r, cfg, a)
    if a.cmd == "down":
        from . import ops
        return ops.down(r, cfg, a)
    if a.cmd == "record":
        from . import ops
        return ops.record(r, cfg, a)
    if a.cmd == "play":
        from . import play
        return play.play(r, cfg, a)
    if a.cmd == "bench":
        from . import bench
        return bench.bench(r, cfg, a)
    if a.cmd == "inject":
        from . import inject
        return inject.inject(r, cfg, a)
    if a.cmd == "fault":
        from . import faults
        if a.no_sudo:
            cfg.setdefault("jetson", {})["sudo"] = False
        if a.action == "status":
            faults.status(cfg)
        elif not a.name:
            raise Fail("fault", "需要 fault 名稱")
        elif a.action == "apply":
            faults.apply(r, cfg, a.name)
        elif a.action == "ack":
            faults.ack(cfg, a.name, dry=a.dry_run)
        else:
            faults.revert(r, cfg, a.name)
        return 0
    raise Fail("pawexp", "需要子指令（--help 看清單）")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    rc = 0
    try:
        a = build_parser().parse_args(argv)
        rc = dispatch(a) or 0
    except Fail as e:
        print(f"[pawexp] FAIL {e.step}: {e.detail}", flush=True)
        rc = e.code
    except SystemExit as e:  # argparse --help（0）或用法錯誤
        rc = e.code if isinstance(e.code, int) else 2
        if rc != 0:
            print("[pawexp] FAIL args: 參數錯誤（見上方 usage）", flush=True)
            rc = 2
    except KeyboardInterrupt:
        print("[pawexp] FAIL interrupted: Ctrl-C", flush=True)
        rc = 2
    finally:
        try:
            log_line(f"pawexp {' '.join(shlex.quote(x) for x in argv)} rc={rc}")
        except OSError:
            pass
    return rc
