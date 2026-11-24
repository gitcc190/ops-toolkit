#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proc_guard.py —— 进程守护（挂掉自动拉起）

跟 systemd 的 Restart=always 有什么不同：
  - 可以守护那些不适合写成 systemd unit 的临时进程
  - 记录每次拉起的次数与时间，写进 JSON，方便事后看"这进程今天挂了几次"
  - 支持"连续拉起失败 N 次就放弃"，避免无脑重启把机器拖死

用法：
    python3 proc_guard.py --name nginx --check "pgrep -x nginx" --start "sudo systemctl start nginx"
    python3 proc_guard.py --name myapp --check "pgrep -f myapp.py" --start "nohup python3 myapp.py &" --interval 30
    python3 proc_guard.py --name nginx --check "pgrep -x nginx" --start "..." --once   # 只检查一次
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_RESTARTED = 3
EXIT_GAVE_UP = 4


class ShortLevelFormatter(logging.Formatter):
    """
    把 Python 默认的 WARNING/CRITICAL 缩成 WARN/CRIT，
    让日志里级别字段宽度一致，和 Shell 版 common.sh 的格式对齐。
    """

    LEVEL_MAP = {"WARNING": "WARN", "CRITICAL": "CRIT"}

    def format(self, record):
        record.shortlevel = self.LEVEL_MAP.get(record.levelname, record.levelname)
        return super().format(record)


def setup_logging(verbose: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ShortLevelFormatter(
        fmt="%(asctime)s [%(shortlevel)-5s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def run(cmd: str, timeout: int = 10):
    """
    执行 shell 命令，返回 (returncode, stdout, stderr)。

    为什么不用 shell=True 直接跑：
      坑一：shell=True 会先起 /bin/sh -c，这个 sh 的命令行里包含了我们传的命令文本。
            `pgrep -f "myapp.py"` 会把这个 sh 自己匹配上，永远返回 0。
      坑二：就算用 exec 消除了 sh，`pgrep -f` 还会匹配到**调用它的上层进程**——
            比如你敲 `proc_guard.py --check "pgrep -f 'sleep 3601'"`，
            这个 python 进程自己的命令行里就含 "sleep 3601"，照样被匹配。

      所以：代码层面用 exec 去掉最直接的一层假阳性，
      但根本结论是——**--check 不要用 pgrep -f 匹配长字符串**。
      可靠写法：pgrep -x <进程名> / ps -C <进程名> / systemctl is-active <服务>。

    解法：用 `sh -c "exec <命令>"`，exec 让 sh 把自己替换成目标进程。

    限制：exec 只接受单个命令。带管道/重定向/分号/逻辑运算符的写法
    没法用 exec，这类退回 shell=True。所以 --check 命令别写复杂管道。
    """
    logging.debug("执行：%s", cmd)
    # shell 元字符：出现任意一个就说明不是单命令，exec 用不了
    needs_shell = any(ch in cmd for ch in "|&;<>()`$")

    if needs_shell:
        argv = cmd
        use_shell = True
    else:
        argv = ["/bin/sh", "-c", "exec " + cmd]
        use_shell = False

    try:
        proc = subprocess.run(
            argv,
            shell=use_shell,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # 显式指定编码与容错：进程输出可能不是 UTF-8（例如 Windows GBK），
            # 不指定时 text=True 会用系统编码并在解码失败时直接抛异常
            encoding="utf-8",
            errors="replace",
        )
        # stdout/stderr 在极端情况下可能为 None，统一兜底成空串
        out = proc.stdout or ""
        err = proc.stderr or ""
        return proc.returncode, out.strip(), err.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "命令超时"
    except OSError as exc:
        return 127, "", str(exc)


class StateStore:
    """
    把拉起次数落到 JSON 文件。
    用一个文件而不是数据库：这是个单机小工具，没必要上依赖。
    """

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logging.warning("状态文件损坏，按空状态处理：%s", self.path)
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # 先写临时文件再改名，避免写一半断电导致文件损坏
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def check_once(name: str, check_cmd: str, start_cmd: str,
               store: StateStore, max_retry: int) -> int:
    """检查一次，必要则拉起。返回退出码"""
    code, _, _ = run(check_cmd)

    data = store.load()
    record = data.setdefault(name, {
        "restarts": 0,
        "last_restart": None,
        "consecutive_fail": 0,
    })

    if code == 0:
        logging.info("%s 运行正常", name)
        if record["consecutive_fail"]:
            logging.info("%s 已恢复，连续失败计数清零", name)
            record["consecutive_fail"] = 0
            store.save(data)
        return EXIT_OK

    logging.warning("%s 未运行（检查命令退出码 %s），准备拉起", name, code)

    if record["consecutive_fail"] >= max_retry:
        logging.error(
            "%s 已连续失败 %d 次，达到上限 %d，放弃拉起。请人工介入",
            name, record["consecutive_fail"], max_retry,
        )
        return EXIT_GAVE_UP

    rc, _, err = run(start_cmd, timeout=30)
    if rc != 0:
        record["consecutive_fail"] += 1
        store.save(data)
        logging.error("拉起失败（退出码 %s）：%s", rc, err or "无输出")
        return EXIT_GAVE_UP

    # 拉起后等 1 秒再复查，确认真的起来了，而不是"命令返回 0 但进程立刻退出"
    time.sleep(1)
    code2, _, _ = run(check_cmd)
    if code2 != 0:
        record["consecutive_fail"] += 1
        store.save(data)
        logging.error("启动命令已执行，但 %s 仍未在运行，连续失败 +1", name)
        return EXIT_GAVE_UP

    record["restarts"] += 1
    record["consecutive_fail"] = 0
    record["last_restart"] = datetime.now().isoformat(timespec="seconds")
    store.save(data)
    logging.info("%s 已拉起，累计拉起 %d 次", name, record["restarts"])
    return EXIT_RESTARTED


def main() -> int:
    parser = argparse.ArgumentParser(
        description="进程守护：检测不到进程就拉起",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 正常 / 3 本次执行了拉起 / 4 拉起失败或达上限",
    )
    parser.add_argument("--name", required=True, help="进程名，用于日志与状态文件")
    parser.add_argument("--check", required=True,
                        help="检测命令，退出码 0 表示进程在。"
                             "推荐 pgrep -x <进程名> 或 systemctl is-active <服务名>；"
                             "用 pgrep -f 匹配长字符串时容易误伤其他进程")
    parser.add_argument("--start", required=True, help="拉起命令")
    parser.add_argument("--interval", type=int, default=60, help="轮询间隔秒数，默认 60")
    parser.add_argument("--max-retry", type=int, default=3, help="连续失败多少次后放弃，默认 3")
    parser.add_argument("--state", default="", help="状态文件路径，默认 /tmp/proc_guard_<name>.json")
    parser.add_argument("--once", action="store_true", help="只检查一次就退出")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    # argparse 参数错误固定返回 2，与本仓库「用法错误 = 1」的约定不一致，统一掉
    try:
        args = parser.parse_args()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return EXIT_OK if code == 0 else EXIT_USAGE

    setup_logging(args.verbose)

    state_path = Path(args.state) if args.state else Path(f"/tmp/proc_guard_{args.name}.json")
    store = StateStore(state_path)

    if args.once:
        return check_once(args.name, args.check, args.start, store, args.max_retry)

    logging.info("开始守护 %s，间隔 %d 秒，Ctrl+C 退出", args.name, args.interval)
    last_code = EXIT_OK
    try:
        while True:
            last_code = check_once(args.name, args.check, args.start, store, args.max_retry)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.info("收到中断信号，退出守护")
        return last_code


if __name__ == "__main__":
    sys.exit(main())
