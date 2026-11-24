#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
disk_monitor.py —— 磁盘使用率监控（Python 版）

与 Shell 版 disk_alert.sh 的区别：
  - 能直接发 HTTP 告警（钉钉 / 企业微信 / Slack 的 webhook 都吃 JSON）
  - 输出结构化 JSON，方便被其它程序调用
  - 不依赖 df 的文本格式，跨平台（Windows 也能跑）

用法：
    python3 disk_monitor.py
    python3 disk_monitor.py --threshold 90 --json
    python3 disk_monitor.py --webhook https://example.com/hook --threshold 80
"""

import argparse
import json
import logging
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime

# 退出码与 Shell 版保持一致：0 正常 / 3 超阈值
EXIT_OK = 0
EXIT_ALERT = 3
EXIT_ERROR = 1

# 默认不检查这些伪文件系统，它们的容量对运维没有意义
SKIP_FSTYPES = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs"}


class ShortLevelFormatter(logging.Formatter):
    """把 WARNING/CRITICAL 缩成 WARN/CRIT，与 Shell 版日志格式对齐"""

    LEVEL_MAP = {"WARNING": "WARN", "CRITICAL": "CRIT"}

    def format(self, record):
        record.shortlevel = self.LEVEL_MAP.get(record.levelname, record.levelname)
        return super().format(record)


def setup_logging(verbose: bool) -> None:
    """统一日志格式，与 Shell 版保持同样的观感"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ShortLevelFormatter(
        fmt="%(asctime)s [%(shortlevel)-5s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def read_mounts(path: str = "/proc/mounts") -> list:
    """
    从 /proc/mounts 读出真实挂载点。
    为什么不直接用 psutil.disk_partitions()：那是第三方库，
    这里刻意只用标准库，保证在最小化系统上也能跑。
    """
    mounts = []
    try:
        with open(path, "r", encoding="utf-8") as fp:
            for line in fp:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mount_point, fstype = parts[0], parts[1], parts[2]
                if fstype in SKIP_FSTYPES:
                    continue
                # 只统计真实块设备，排除 bind mount 与伪设备
                if not device.startswith("/dev/"):
                    continue
                mounts.append((device, mount_point, fstype))
    except FileNotFoundError:
        # 非 Linux 环境退化为根路径检查
        logging.debug("/proc/mounts 不存在，退化为根路径检查")
        mounts = [("/", "/", "unknown")]
    return mounts


def check_disks(threshold: float, mount_filter: str = "") -> list:
    """返回超过阈值的记录列表"""
    results = []
    for device, mount_point, fstype in read_mounts():
        if mount_filter and mount_point != mount_filter:
            continue
        try:
            usage = shutil.disk_usage(mount_point)
        except PermissionError:
            logging.debug("无权限读取挂载点 %s，跳过", mount_point)
            continue
        if usage.total == 0:
            continue
        percent = usage.used * 100.0 / usage.total
        record = {
            "device": device,
            "mount": mount_point,
            "fstype": fstype,
            "total_gb": round(usage.total / 1024 ** 3, 2),
            "used_gb": round(usage.used / 1024 ** 3, 2),
            "free_gb": round(usage.free / 1024 ** 3, 2),
            "percent": round(percent, 2),
            "over": percent >= threshold,
        }
        results.append(record)
    return results


def send_webhook(url: str, text: str, timeout: int = 5) -> bool:
    """
    发一条纯文本告警。
    钉钉/企业微信机器人接受 {"msgtype":"text","text":{"content": "..."}}，
    Slack 接受 {"text": "..."}，这里同时带上两个字段，兼容大多数场景。
    """
    payload = json.dumps(
        {"msgtype": "text", "text": {"content": text}, "content": text},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            logging.info("webhook 返回状态码 %s", resp.status)
            return 200 <= resp.status < 300
    except urllib.error.URLError as exc:
        # 告警发不出去不应该让监控本身崩掉，只记录
        logging.error("webhook 发送失败：%s", exc)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="磁盘使用率监控，超过阈值时告警",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 正常 / 3 超阈值 / 1 运行错误",
    )
    parser.add_argument("-t", "--threshold", type=float, default=80.0, help="阈值百分比，默认 80")
    parser.add_argument("-m", "--mount", default="", help="只检查指定挂载点，例如 /")
    parser.add_argument("--webhook", default="", help="告警 webhook 地址")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    # argparse 遇到参数错误固定返回 2（比如 -t abc），但本仓库约定
    # 「参数错误 = 1」，Shell 版也返回 1。这里把 2 统一成 1，
    # 免得调用方（cron / 流水线）要按两套规则判断。
    try:
        args = parser.parse_args()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return EXIT_OK if code == 0 else EXIT_ERROR

    if not 0 < args.threshold <= 100:
        logging.error("阈值必须在 0-100 之间")
        return EXIT_ERROR

    setup_logging(args.verbose)
    logging.info("开始检查磁盘，阈值 %.0f%%", args.threshold)

    records = check_disks(args.threshold, args.mount)
    if not records:
        logging.error("没有读到任何可用挂载点")
        return EXIT_ERROR

    over = [r for r in records if r["over"]]

    if args.json:
        print(json.dumps(
            {
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "threshold": args.threshold,
                "disks": records,
                "alert_count": len(over),
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(f"{'挂载点':<20}{'总量GB':>10}{'已用GB':>10}{'使用率':>10}  状态")
        for r in sorted(records, key=lambda x: x["percent"], reverse=True):
            flag = "告警" if r["over"] else "正常"
            print(
                f"{r['mount']:<20}{r['total_gb']:>10.2f}{r['used_gb']:>10.2f}"
                f"{r['percent']:>9.2f}%  {flag}"
            )

    if not over:
        logging.info("全部挂载点使用率低于阈值，状态正常")
        return EXIT_OK

    for r in over:
        logging.warning(
            "告警：挂载点 %s 使用率 %.2f%%（已用 %.2f GB / 共 %.2f GB）",
            r["mount"], r["percent"], r["used_gb"], r["total_gb"],
        )
    logging.warning("建议排查：du -x -h --max-depth=1 <挂载点> | sort -rh | head -10")

    if args.webhook:
        lines = [
            "【磁盘告警】%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ]
        for r in over:
            lines.append(
                "- %s 使用率 %.2f%%（%.2f/%.2f GB）"
                % (r["mount"], r["percent"], r["used_gb"], r["total_gb"])
            )
        send_webhook(args.webhook, "\n".join(lines))

    return EXIT_ALERT


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logging.warning("用户中断")
        sys.exit(130)
