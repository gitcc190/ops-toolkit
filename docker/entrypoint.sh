#!/usr/bin/env bash
# ============================================================
# entrypoint.sh —— 容器入口，把子命令分发给对应脚本
# 用法：
#   docker run --rm ops-toolkit:0.1.0 disk_alert -t 90
#   docker run --rm ops-toolkit:0.1.0 top_ip -f /logs/access.log
#   docker run --rm ops-toolkit:0.1.0 disk_monitor --json
# ============================================================

set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD="${1:-help}"
shift || true

case "$CMD" in
  disk_alert)
    exec "$BASE_DIR/scripts/disk_alert.sh" "$@"
    ;;
  top_ip)
    exec "$BASE_DIR/scripts/nginx_top_ip.sh" "$@"
    ;;
  status_code)
    exec "$BASE_DIR/scripts/nginx_status_code.sh" "$@"
    ;;
  backup)
    exec "$BASE_DIR/scripts/mysql_backup.sh" "$@"
    ;;
  restore)
    exec "$BASE_DIR/scripts/mysql_restore.sh" "$@"
    ;;
  disk_monitor)
    exec python3 "$BASE_DIR/python/disk_monitor.py" "$@"
    ;;
  log_analyzer)
    exec python3 "$BASE_DIR/python/log_analyzer.py" "$@"
    ;;
  proc_guard)
    exec python3 "$BASE_DIR/python/proc_guard.py" "$@"
    ;;
  batch_ssh)
    exec python3 "$BASE_DIR/python/batch_ssh.py" "$@"
    ;;
  bash)
    exec bash
    ;;
  help|--help|-h)
    cat <<'EOF'
ops-toolkit 容器入口

子命令：
  disk_alert [-t 阈值]          磁盘告警（Shell 版）
  top_ip -f 日志 [-n 数量]      Nginx Top IP
  status_code 日志              状态码分布
  backup [-c 配置]              MySQL 备份
  restore -f 备份 -d 库         MySQL 恢复
  disk_monitor [选项]           磁盘监控（Python 版）
  log_analyzer 日志 [选项]      日志分析（Python 版）
  proc_guard --name ... --check ... --start ...
  batch_ssh -H 清单 -c 命令 -u 用户
  bash                          进入容器 shell 调试

示例：
  docker run --rm -v /var/log/nginx:/logs:ro ops-toolkit:0.1.0 top_ip -f /logs/access.log -n 5
EOF
    ;;
  *)
    echo "未知子命令：$CMD，执行 help 查看用法" >&2
    exit 1
    ;;
esac
