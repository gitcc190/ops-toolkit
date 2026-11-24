#!/usr/bin/env bash
# ============================================================
# mysql_backup.sh —— MySQL 逻辑备份 + 自动清理
# 要点：
#   1. 用 --single-transaction 保证 InnoDB 表备份时的一致性快照，
#      同时不锁表（不需要 --lock-tables，也不影响线上写入）
#   2. 密码通过 MYSQL_PWD 环境变量传递，不出现在 ps 的命令行里
#   3. 备份完立刻校验文件非空，避免"备份成功但文件是 0 字节"这种最坑的情况
#   4. 按 KEEP_DAYS 清理旧文件
# 用法：
#   ./mysql_backup.sh -c conf/backup.env
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ENV_FILE=""
FORCE_DB=""

usage() {
  cat <<'EOF'
用法: mysql_backup.sh [-c 配置文件] [-d 数据库名]

  -c 配置文件路径（默认 conf/backup.env）
  -d 只备份指定数据库，覆盖配置文件里的 DB_NAME（ALL 表示全部库）
  -h 显示本帮助
EOF
}

while getopts ":c:d:h" opt; do
  case "$opt" in
    c) ENV_FILE="$OPTARG" ;;
    d) FORCE_DB="$OPTARG" ;;
    h) usage; exit 0 ;;
    :) die "选项 -$OPTARG 需要参数值" 1 ;;
    \?) die "未知选项 -$OPTARG" 1 ;;
  esac
done

ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/../conf/backup.env}"

[ -f "$ENV_FILE" ] || die "配置文件不存在：$ENV_FILE（可从 conf/backup.env.example 复制一份）" 1

# 用 source 读取配置；文件权限必须是 600，否则拒绝执行（防止密码泄露）
perm="$(stat -c '%a' "$ENV_FILE")"
if [ "$perm" != "600" ]; then
  die "配置文件权限应为 600，当前为 $perm，请执行：chmod 600 $ENV_FILE" 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

: "${DB_HOST:=127.0.0.1}"
: "${DB_PORT:=3306}"
: "${DB_USER:?配置文件缺少 DB_USER}"
: "${DB_PASS:?配置文件缺少 DB_PASS}"
: "${DB_NAME:=ALL}"
: "${BACKUP_DIR:=/var/backups/mysql}"
: "${KEEP_DAYS:=7}"

# 命令行 -d 优先级最高
DB_NAME="${FORCE_DB:-$DB_NAME}"

require_cmd mysqldump gzip find

# umask 077：新建的备份文件默认 600、目录默认 700。
# 不加的话默认 umask 022 会让备份文件变成 644，
# 同组用户和其他用户都能读——备份里是全量业务数据，这是明确的安全问题。
umask 077

mkdir -p "$BACKUP_DIR" || die "无法创建备份目录：$BACKUP_DIR" 1

# 权限 700：备份文件里有全量数据，不能让其他用户读
chmod 700 "$BACKUP_DIR"

STAMP="$(date '+%Y%m%d_%H%M%S')"
if [ "$DB_NAME" = "ALL" ]; then
  OUT_FILE="$BACKUP_DIR/all_${STAMP}.sql.gz"
  TARGET_DESC="所有数据库"
  DUMP_ARGS=(--all-databases)
else
  OUT_FILE="$BACKUP_DIR/${DB_NAME}_${STAMP}.sql.gz"
  TARGET_DESC="数据库 $DB_NAME"
  DUMP_ARGS=("$DB_NAME")
fi

log_info "开始备份：$TARGET_DESC -> $OUT_FILE"

# MYSQL_PWD 只在本次命令的环境里可见，不会出现在 ps 输出中
# --single-transaction 仅对 InnoDB 有效；--quick 边读边写不占内存
#
# ⚠ --no-defaults 必须放在最前面，且不能省。
#   原因：MYSQL_PWD 的优先级低于配置文件。如果执行者的家目录下有 ~/.my.cnf
#   （比如 /root/.my.cnf 里写着 root 的密码），客户端会优先用 my.cnf 里的密码，
#   把 MYSQL_PWD 静默覆盖掉，结果就是「明明配了 DB_PASS 却报 Access denied」。
#   --no-defaults 屏蔽掉所有配置文件，让 MYSQL_PWD 真正生效。
if ! MYSQL_PWD="$DB_PASS" mysqldump \
      --no-defaults \
      --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" \
      --single-transaction --quick --routines --triggers --events \
      --default-character-set=utf8mb4 \
      "${DUMP_ARGS[@]}" 2>/tmp/mysql_backup_err.$$ | gzip -c > "$OUT_FILE"; then
  err="$(cat /tmp/mysql_backup_err.$$ 2>/dev/null || true)"
  rm -f "/tmp/mysql_backup_err.$$" "$OUT_FILE"
  die "备份失败：$err" 1
fi
rm -f "/tmp/mysql_backup_err.$$"

# 关键校验：文件必须存在、非空、且 gzip 结构完整
[ -s "$OUT_FILE" ] || die "备份文件为空：$OUT_FILE" 1
if ! gzip -t "$OUT_FILE" 2>/dev/null; then
  die "备份文件 gzip 校验失败，可能写入中断：$OUT_FILE" 1
fi

size_h="$(du -h "$OUT_FILE" | awk '{print $1}')"
log_info "备份完成，文件大小 $size_h"

# 清理超期文件；只删本脚本命名规则下的 .sql.gz，避免误删别的文件
deleted="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.sql.gz' -mtime "+$KEEP_DAYS" -print -delete | wc -l | tr -d ' ')"
log_info "清理 ${KEEP_DAYS} 天前的备份：删除 $deleted 个文件"

log_info "当前备份目录占用：$(du -sh "$BACKUP_DIR" | awk '{print $1}')"
exit 0
