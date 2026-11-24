#!/usr/bin/env bash
# ============================================================
# nginx_top_ip.sh —— 统计 Nginx access.log 访问量最高的 IP
# 原理：combined 格式第一列就是客户端 IP
#   1. awk 取出第一列
#   2. sort 排序（让相同 IP 相邻）
#   3. uniq -c 数每行出现次数
#   4. sort -rn 按次数倒序
#   5. head -n 取前 N
# 用法：
#   ./nginx_top_ip.sh /var/log/nginx/access.log
#   ./nginx_top_ip.sh -n 20 -f access.log
# ============================================================

# 用纯 bash 内建算脚本目录（cd / pwd 都是 builtin），不依赖外部 dirname。
# 场景：cron 或 env -i 下 PATH 异常时 dirname 找不到，SCRIPT_DIR 会变成空串，
# 后面 source common.sh 就会失败，而 set -euo pipefail 恰恰是 common.sh 提供的。
SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
[ "$SCRIPT_DIR" = "${BASH_SOURCE[0]}" ] && SCRIPT_DIR="."
SCRIPT_DIR="$(cd "$SCRIPT_DIR" && pwd)"
# shellcheck source=lib/common.sh
# 加载失败必须立刻退出，否则脚本会在没有 set -euo pipefail 保护的情况下继续跑，
# 最后返回 0 —— 看起来成功，实际什么都没有做。
if ! . "$SCRIPT_DIR/lib/common.sh"; then
  echo "无法加载 $SCRIPT_DIR/lib/common.sh，请确认脚本目录完整" >&2
  exit 2
fi

TOP=10
LOG_FILE=""

usage() {
  cat <<'EOF'
用法: nginx_top_ip.sh [-n 数量] [-f 日志文件]

  -n 显示前几名，默认 10
  -f Nginx access.log 路径（也可作为第一个位置参数给出）
  -h 显示本帮助
EOF
}

while getopts ":n:f:h" opt; do
  case "$opt" in
    n) TOP="$OPTARG" ;;
    f) LOG_FILE="$OPTARG" ;;
    h) usage; exit 0 ;;
    :) die "选项 -$OPTARG 需要参数值" 1 ;;
    \?) die "未知选项 -$OPTARG" 1 ;;
  esac
done
shift $((OPTIND - 1))
# 位置参数优先被当作日志文件，兼容 ./nginx_top_ip.sh access.log 这种写法
LOG_FILE="${LOG_FILE:-${1:-}}"

case "$TOP" in
  ''|*[!0-9]*) die "数量必须是整数，收到：$TOP" 1 ;;
esac

[ -n "$LOG_FILE" ] || die "未指定日志文件，用 -h 看用法" 1
[ -r "$LOG_FILE" ] || die "日志文件不存在或不可读：$LOG_FILE" 1

require_cmd awk sort uniq head

log_info "分析文件：$LOG_FILE"

# 只认结构完整的 combined 行：字段数 >= 9 且第 9 字段是三位状态码。
# 为什么不用 NF >= 1：日志里可能混进空行、注释行、被截断的畸形行，
# 它们的第一个字段也会被当成"IP"统计，最后出现在 Top 榜里（实测踩过）。
top_result="$(awk 'NF >= 9 && $9 ~ /^[0-9][0-9][0-9]$/ { print $1 }' "$LOG_FILE" \
  | sort \
  | uniq -c \
  | sort -rn \
  | head -n "$TOP")"

# 空结果要单独处理，否则 printf 会输出一个空行，
# 被后面的 awk 渲染成「1.  次」这种莫名其妙的一行
if [ -z "$top_result" ]; then
  log_warn "没有解析到任何有效请求行，确认日志格式是否为 combined"
  exit 0
fi

printf '%s\n' "$top_result" | awk '{ printf "%2d. %-16s %s 次\n", NR, $2, $1 }'

exit 0
