#!/usr/bin/env bash
# ============================================================
# disk_alert.sh —— 磁盘使用率告警
# 原理：读 df -P 的输出，第 5 列是使用百分比，第 6 列是挂载点
# 超阈值：打印告警并以退出码 3 结束（便于 cron / Jenkins 判断）
# 用法：
#   ./disk_alert.sh                 # 默认阈值 80%，检查全部本地文件系统
#   ./disk_alert.sh -t 90           # 阈值改成 90%
#   ./disk_alert.sh -m /            # 只看根分区
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

THRESHOLD=80
MOUNT_FILTER=""

usage() {
  cat <<'EOF'
用法: disk_alert.sh [-t 阈值] [-m 挂载点] [-h]

  -t 使用率阈值，默认 80
  -m 只检查指定挂载点，例如 /
  -h 显示本帮助

退出码: 0 正常 / 1 参数错误 / 2 依赖缺失 / 3 有分区超阈值
EOF
}

# getopts 逐字符解析短选项，冒号表示该选项需要一个值
while getopts ":t:m:h" opt; do
  case "$opt" in
    t) THRESHOLD="$OPTARG" ;;
    m) MOUNT_FILTER="$OPTARG" ;;
    h) usage; exit 0 ;;
    :) die "选项 -$OPTARG 需要参数值，用 -h 看用法" 1 ;;
    \?) die "未知选项 -$OPTARG，用 -h 看用法" 1 ;;
  esac
done

# 阈值必须是纯数字，否则后面 awk 比较会得到意外结果
case "$THRESHOLD" in
  ''|*[!0-9]*) die "阈值必须是整数，收到：$THRESHOLD" 1 ;;
esac
if [ "$THRESHOLD" -lt 1 ] || [ "$THRESHOLD" -gt 100 ]; then
  die "阈值必须在 1-100 之间，收到：$THRESHOLD" 1
fi

require_cmd df awk

log_info "开始检查磁盘，阈值 ${THRESHOLD}%"

# df -P 保证每个文件系统输出一行（避免长设备名折行）
# -x 排除内存文件系统，它们的使用率没有意义
df_output="$(df -P -x tmpfs -x devtmpfs -x squashfs 2>/dev/null)"

# 第 1 行是表头，从第 2 行开始处理
# gsub 去掉百分号，$5+0 转成数字比较
alerts="$(
  printf '%s\n' "$df_output" | awk -v t="$THRESHOLD" -v m="$MOUNT_FILTER" '
    NR > 1 {
      use = $5
      gsub("%", "", use)
      mount = $6
      if (m != "" && mount != m) next
      if (use + 0 >= t) printf "%s\t%s\t%s\n", mount, use, $2
    }
  '
)"

if [ -z "$alerts" ]; then
  log_info "全部挂载点使用率低于 ${THRESHOLD}%，状态正常"
  exit 0
fi

# 逐行输出告警；IFS 用制表符切分
log_warn "发现 $(printf '%s\n' "$alerts" | wc -l | tr -d ' ') 个挂载点超过阈值"
while IFS=$'\t' read -r mount use total; do
  log_warn "告警：挂载点 ${mount} 使用率 ${use}%（总容量 ${total} 块）"
done <<< "$alerts"

log_warn "建议排查：du -x -h --max-depth=1 <挂载点> | sort -rh | head -10"
exit 3
