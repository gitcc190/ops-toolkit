#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_ssh.py —— 批量 SSH 执行命令

为什么用 paramiko 而不是直接调 ssh 命令：
  - 不用逐台配置免密也能跑（密码从环境变量读）
  - 并发执行，10 台机器耗时接近 1 台
  - 结果是结构化的，能直接汇总成表格或 JSON

前置：
    pip3 install paramiko

用法：
    # 用密钥（推荐）
    python3 batch_ssh.py -H hosts.txt -c "uptime" -u wuyanzu -k ~/.ssh/id_rsa

    # 用密码（从环境变量读，不出现在命令行）
    export SSH_PASS='your-password'
    python3 batch_ssh.py -H hosts.txt -c "uptime" -u wuyanzu

hosts.txt 每行一台：192.168.1.11 或 192.168.1.11:2222
"""

import argparse
import concurrent.futures
import os
import sys
import time

try:
    import paramiko
except ImportError:
    print("缺少 paramiko，请先执行：pip3 install paramiko", file=sys.stderr)
    sys.exit(2)


def load_hosts(path: str) -> list:
    """读主机清单，跳过空行和 # 注释行。

    支持三种写法（用户名可选，省略时用 -u 给的全局值）：
        192.168.1.11
        192.168.1.11:2222
        lw@192.168.1.11:2222

    为什么允许逐行写用户名：真实环境里同一个网段经常混着不同账号
    （比如 Ubuntu 用 lw、Kali 用 kali、CentOS 用 root），
    一个全局 -u 根本覆盖不了，逐台写才跑得通。
    """
    hosts = []
    with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            user = ""
            if "@" in line:
                user, line = line.split("@", 1)
            if ":" in line:
                host, port = line.rsplit(":", 1)
                port = int(port)
            else:
                host, port = line, 22
            hosts.append((user, host, port))
    return hosts


def run_on_one(host: str, port: int, command: str, user: str,
               password: str, key_file: str, timeout: int) -> dict:
    """
    在单台机器上执行命令，返回结果字典。
    无论成功失败都返回字典，不让异常冒泡打断整个批量任务。
    """
    started = time.time()
    client = paramiko.SSHClient()
    # 自动接受未知主机指纹。生产环境应改为加载 known_hosts
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    result = {
        "user": user,
        "host": host,
        "port": port,
        "ok": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "elapsed": 0.0,
    }

    try:
        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": timeout,
            "banner_timeout": timeout,
            "auth_timeout": timeout,
        }
        if key_file:
            connect_kwargs["key_filename"] = os.path.expanduser(key_file)
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False
        elif password:
            connect_kwargs["password"] = password
            connect_kwargs["look_for_keys"] = False
            connect_kwargs["allow_agent"] = False

        client.connect(**connect_kwargs)
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        result["stdout"] = stdout.read().decode("utf-8", errors="replace").strip()
        result["stderr"] = stderr.read().decode("utf-8", errors="replace").strip()
        result["exit_code"] = stdout.channel.recv_exit_status()
        result["ok"] = result["exit_code"] == 0
    except paramiko.AuthenticationException:
        result["stderr"] = "认证失败：用户名/密码/密钥不正确"
    except paramiko.SSHException as exc:
        result["stderr"] = f"SSH 协议错误：{exc}"
    except OSError as exc:
        result["stderr"] = f"网络不可达或端口不通：{exc}"
    finally:
        client.close()
        result["elapsed"] = round(time.time() - started, 2)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="批量 SSH 执行命令（并发）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  %(prog)s -H hosts.txt -c 'uptime' -u wuyanzu -k ~/.ssh/id_rsa\n"
            "  SSH_PASS=xxx %(prog)s -H hosts.txt -c 'df -h' -u wuyanzu\n"
        ),
    )
    parser.add_argument("-H", "--hosts", required=True, help="主机清单文件，每行一台")
    parser.add_argument("-c", "--command", required=True, help="要执行的命令")
    parser.add_argument("-u", "--user", default="",
                        help="全局 SSH 用户名；主机清单里写了 user@host 时以清单为准")
    parser.add_argument("-k", "--key", default="", help="私钥文件路径")
    parser.add_argument("-p", "--password-env", default="SSH_PASS",
                        help="存放密码的环境变量名，默认 SSH_PASS")
    parser.add_argument("-t", "--timeout", type=int, default=15, help="单台超时秒数，默认 15")
    parser.add_argument("-w", "--workers", type=int, default=10, help="并发数，默认 10")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出汇总结果")
    # argparse 参数错误固定返回 2，与本仓库「用法错误 = 1」的约定不一致，统一掉
    try:
        args = parser.parse_args()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        return 0 if code == 0 else 1

    try:
        hosts = load_hosts(args.hosts)
    except OSError as exc:
        print(f"读取主机清单失败：{exc}", file=sys.stderr)
        return 1

    if not hosts:
        print("主机清单为空", file=sys.stderr)
        return 1

    # 密码从环境变量读，避免出现在 ps 与 shell history 里
    password = os.environ.get(args.password_env, "")
    if not args.key and not password:
        print(
            f"未提供密钥，且环境变量 {args.password_env} 为空。\n"
            f"可以 export {args.password_env}='密码' 或使用 -k 指定私钥",
            file=sys.stderr,
        )
        return 1

    # 逐台确定最终用户名：清单里的 user@host 优先，否则用全局 -u
    resolved = []
    no_user = []
    for user, host, port in hosts:
        final_user = user or args.user
        if not final_user:
            no_user.append("%s:%d" % (host, port))
        resolved.append((final_user, host, port))
    if no_user:
        print(
            "以下主机没有用户名：%s\n"
            "请在清单里写成 user@host，或用 -u 指定全局用户名" % ", ".join(no_user),
            file=sys.stderr,
        )
        return 1

    print(f"目标 {len(hosts)} 台，并发 {args.workers}，命令：{args.command}")
    print("-" * 66)

    results = []
    # ThreadPoolExecutor 是 IO 密集场景的正确选择：SSH 等待期间线程会让出
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_on_one, host, port, args.command, user,
                password, args.key, args.timeout,
            ): (user, host, port)
            for user, host, port in resolved
        }
        for fut in concurrent.futures.as_completed(futures):
            user, host, port = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # 兜底，防止单台异常让整个任务崩掉
                res = {
                    "user": user, "host": host, "port": port, "ok": False,
                    "exit_code": None, "stdout": "", "stderr": f"未预期异常：{exc}",
                    "elapsed": 0.0,
                }
            results.append(res)

            status = "成功" if res["ok"] else "失败"
            print(f"[{status}] {user}@{host}:{port}  退出码={res['exit_code']}  耗时={res['elapsed']}s")
            if res["stdout"]:
                for line in res["stdout"].splitlines():
                    print(f"    {line}")
            if res["stderr"]:
                print(f"    错误：{res['stderr']}")

    # 按主机名排序后再汇总，保证输出稳定（as_completed 的顺序是不定的）
    results.sort(key=lambda r: (r["host"], r["port"]))
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count

    print("-" * 66)
    print(f"汇总：成功 {ok_count} 台，失败 {fail_count} 台，共 {len(results)} 台")

    if args.json:
        import json
        print(json.dumps(results, ensure_ascii=False, indent=2))

    # 有失败就返回非 0，方便被 Jenkins / cron 当作失败处理
    return 0 if fail_count == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
