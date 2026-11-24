#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
log_analyzer.py —— Nginx access.log 分析器（Python 版）

比 Shell 版多做的事：
  - 一条命令同时给 Top IP、状态码分布、慢请求
  - 支持 --since 按时间过滤（例如只看最近 1 小时）
  - 支持 --top N、--status 精确过滤某个状态码

用法：
    python3 log_analyzer.py samples/access.log
    python3 log_analyzer.py samples/access.log --top 5 --since "2026-12-08 10:00:00"
    python3 log_analyzer.py samples/access.log --status 500
"""

import argparse
import re
import sys
from collections import Counter
from datetime import datetime

# Nginx combined 格式示例：
# 10.0.0.5 - - [08/Dec/2026:10:15:32 +0800] "GET /api/items HTTP/1.1" 200 512 "-" "curl/7.68.0"
LINE_RE = re.compile(
    r'^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<method>\S+)\s+(?P<path>\S+)\s*(?P<proto>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+(?P<size>\S+)'
)

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def parse_time(raw: str) -> datetime:
    """
    把 08/Dec/2026:10:15:32 +0800 转成 datetime。
    解析失败返回 None，由调用方决定是否跳过该行。
    """
    try:
        # 时区部分在这里不重要，统一按本地时间比较
        body = raw.split()[0]
        return datetime.strptime(body, "%d/%b/%Y:%H:%M:%S")
    except (ValueError, IndexError):
        return None


def parse_since(raw: str) -> datetime:
    """接受 'YYYY-mm-dd HH:MM:SS' 或 'YYYY-mm-dd'"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError("时间格式应为 'YYYY-mm-dd HH:MM:SS' 或 'YYYY-mm-dd'")


def analyze(path: str, since=None, status_filter=None):
    """逐行解析，返回统计结果字典"""
    ip_counter = Counter()
    status_counter = Counter()
    path_counter = Counter()
    slow_requests = []          # (耗时不可知，这里用响应体大小近似"重"请求)
    total = 0
    bad_lines = 0

    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            match = LINE_RE.match(line)
            if not match:
                bad_lines += 1
                continue

            if status_filter and match.group("status") != status_filter:
                continue

            if since is not None:
                ts = parse_time(match.group("time"))
                if ts is None or ts < since:
                    continue

            total += 1
            ip_counter[match.group("ip")] += 1
            status_counter[match.group("status")] += 1
            path_counter[match.group("path")] += 1

            size = match.group("size")
            if size.isdigit():
                slow_requests.append((int(size), match.group("path"), match.group("status")))

    return {
        "total": total,
        "bad_lines": bad_lines,
        "ips": ip_counter,
        "statuses": status_counter,
        "paths": path_counter,
        "slow": slow_requests,
    }


def print_report(result, top_n: int) -> None:
    total = result["total"]
    if total == 0:
        print("没有匹配到任何请求行，检查日志格式或过滤条件")
        return

    print("=" * 62)
    print(f"有效请求总数：{total}    无法解析的行：{result['bad_lines']}")
    print("=" * 62)

    print(f"\n【Top {top_n} 客户端 IP】")
    for idx, (ip, cnt) in enumerate(result["ips"].most_common(top_n), 1):
        print(f"  {idx:>2}. {ip:<18} {cnt:>8} 次  {cnt * 100 / total:>6.2f}%")

    print("\n【状态码分布】")
    for code, cnt in sorted(result["statuses"].items()):
        print(f"  {code}  {cnt:>8} 次  {cnt * 100 / total:>6.2f}%")

    err5 = sum(cnt for code, cnt in result["statuses"].items() if code.startswith("5"))
    if err5:
        print(f"  → 5xx 合计 {err5} 次，占 {err5 * 100 / total:.2f}%")

    print(f"\n【Top {top_n} 请求路径】")
    for idx, (path, cnt) in enumerate(result["paths"].most_common(top_n), 1):
        print(f"  {idx:>2}. {path:<40} {cnt:>8} 次")

    print(f"\n【Top {top_n} 响应体最大的请求（近似重请求）】")
    for size, path, code in sorted(result["slow"], reverse=True)[:top_n]:
        print(f"  {size:>10} 字节  {code}  {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nginx access.log 分析器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  %(prog)s access.log --top 5\n"
            "  %(prog)s access.log --since '2026-12-08 10:00:00'\n"
            "  %(prog)s access.log --status 500\n"
        ),
    )
    parser.add_argument("logfile", help="access.log 路径")
    parser.add_argument("-n", "--top", type=int, default=10, help="显示前 N 名，默认 10")
    parser.add_argument("-s", "--status", default="", help="只统计指定状态码，例如 500")
    parser.add_argument(
        "--since", type=parse_since, default=None,
        help="只看该时间之后的记录，格式 'YYYY-mm-dd HH:MM:SS'",
    )
    args = parser.parse_args()

    if args.top <= 0:
        parser.error("--top 必须大于 0")

    try:
        result = analyze(args.logfile, args.since, args.status or None)
    except FileNotFoundError:
        print(f"日志文件不存在：{args.logfile}", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"没有读取权限：{args.logfile}", file=sys.stderr)
        return 1

    print_report(result, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
