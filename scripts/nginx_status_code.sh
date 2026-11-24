#!/usr/bin/env bash
# ============================================================
# nginx_status_code.sh —— 按状态码统计请求分布
# 原理：combined 格式第 9 列是状态码
# 额外输出：每类状态码占比，以及 5xx 的总占比（排障时最关心这个）
# 用法：
#   ./nginx_status_code.sh /var/log/nginx/access.log
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

LOG_FILE="${1:-}"

usage() {
  cat <<'EOF'
用法: nginx_status_code.sh <日志文件>
EOF
}

if [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

[ -n "$LOG_FILE" ] || die "未指定日志文件，用 -h 看用法" 1
[ -r "$LOG_FILE" ] || die "日志文件不存在或不可读：$LOG_FILE" 1

require_cmd awk sort uniq

log_info "分析文件：$LOG_FILE"

# 第 9 列必须是三位数字才认为是合法状态码，避免把畸形日志算进去
total="$(awk 'NF >= 9 && $9 ~ /^[0-9][0-9][0-9]$/ { c++ } END { print c + 0 }' "$LOG_FILE")"
[ "$total" -gt 0 ] || die "没有解析到任何有效请求行，确认日志格式是否为 combined" 1

log_info "有效请求总数：$total"

# 统计每个状态码出现次数，并算出百分比
# 表头单独打印，数据行交给 sort 按状态码升序排列
printf '%-8s %10s %10s\n' "状态码" "次数" "占比"
awk -v total="$total" '
  NF >= 9 && $9 ~ /^[0-9][0-9][0-9]$/ { cnt[$9]++ }
  END {
    for (code in cnt) {
      printf "%-8s %10d %9.2f%%\n", code, cnt[code], cnt[code] * 100 / total
    }
  }
' "$LOG_FILE" | sort -k1,1

# 单独把 5xx 拎出来，这是值班时第一个要看的东西
err5="$(awk 'NF >= 9 && $9 ~ /^5[0-9][0-9]$/ { c++ } END { print c + 0 }' "$LOG_FILE")"
if [ "$err5" -gt 0 ]; then
  pct="$(awk -v a="$err5" -v b="$total" 'BEGIN { printf "%.2f", a * 100 / b }')"
  log_warn "5xx 共 ${err5} 次，占 ${pct}%，建议先看 Nginx error.log 与后端健康状态"
else
  log_info "未出现 5xx"
fi

exit 0
