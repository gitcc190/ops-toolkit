#!/usr/bin/env bash
# ============================================================
# mysql_restore.sh —— 从 .sql.gz 备份恢复数据库
# 设计原则：恢复是不可逆操作，所以每一步都先校验再执行
#   1. 文件存在、非空、gzip 完整
#   2. 目标库存在性检查
#   3. 除非加 -y，否则必须人工输入 yes 确认
# 用法：
#   ./mysql_restore.sh -f /var/backups/mysql/ops_demo_20261209_020000.sql.gz -d ops_demo
#   ./mysql_restore.sh -f xxx.sql.gz -d ops_demo -y     # 跳过确认（仅限自动化）
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

ENV_FILE=""
BACKUP_FILE=""
TARGET_DB=""
ASSUME_YES="no"
OVERRIDE_USER=""
OVERRIDE_PASS=""

usage() {
  cat <<'EOF'
用法: mysql_restore.sh -f 备份文件 -d 目标库 [-c 配置文件] [-u 用户] [-p 密码] [-y]

  -f 备份文件（.sql.gz 或 .sql）
  -d 目标数据库名（备份里若是 --all-databases，则不需要本参数）
  -c 配置文件路径（默认 conf/backup.env）
  -u 覆盖配置文件里的 DB_USER（恢复需要写权限，通常用 root）
  -p 覆盖配置文件里的 DB_PASS
  -y 跳过交互确认
  -h 显示本帮助

注意：备份账号通常只有 SELECT 权限，能导出但不能导入。
恢复时请用有写权限的账号（生产上建议单独建一个 restore 专用账号）。
EOF
}

while getopts ":f:d:c:u:p:yh" opt; do
  case "$opt" in
    f) BACKUP_FILE="$OPTARG" ;;
    d) TARGET_DB="$OPTARG" ;;
    c) ENV_FILE="$OPTARG" ;;
    u) OVERRIDE_USER="$OPTARG" ;;
    p) OVERRIDE_PASS="$OPTARG" ;;
    y) ASSUME_YES="yes" ;;
    h) usage; exit 0 ;;
    :) die "选项 -$OPTARG 需要参数值" 1 ;;
    \?) die "未知选项 -$OPTARG" 1 ;;
  esac
done

[ -n "$BACKUP_FILE" ] || die "未指定备份文件，用 -h 看用法" 1
[ -f "$BACKUP_FILE" ] || die "备份文件不存在：$BACKUP_FILE" 1
[ -s "$BACKUP_FILE" ] || die "备份文件为空：$BACKUP_FILE" 1

ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/../conf/backup.env}"
[ -f "$ENV_FILE" ] || die "配置文件不存在：$ENV_FILE" 1

perm="$(stat -c '%a' "$ENV_FILE")"
[ "$perm" = "600" ] || die "配置文件权限应为 600，当前为 $perm，请执行：chmod 600 $ENV_FILE" 1

# shellcheck disable=SC1090
source "$ENV_FILE"

: "${DB_HOST:=127.0.0.1}"
: "${DB_PORT:=3306}"
: "${DB_USER:?配置文件缺少 DB_USER}"
: "${DB_PASS:?配置文件缺少 DB_PASS}"

# 命令行 -u/-p 优先级最高，用来临时切到有写权限的账号
DB_USER="${OVERRIDE_USER:-$DB_USER}"
DB_PASS="${OVERRIDE_PASS:-$DB_PASS}"

require_cmd mysql gzip

# 所有 mysql 调用统一带 --no-defaults：
# MYSQL_PWD 的优先级低于配置文件，家目录下的 ~/.my.cnf 会把它静默覆盖掉，
# 表现为「配了密码却报 Access denied」。屏蔽配置文件后 MYSQL_PWD 才真正生效。
MYSQL=(mysql --no-defaults --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER")

# 根据扩展名决定解压方式；gzip 先做完整性校验，坏文件直接拦下
case "$BACKUP_FILE" in
  *.gz)
    gzip -t "$BACKUP_FILE" 2>/dev/null || die "gzip 校验失败，文件可能损坏：$BACKUP_FILE" 1
    UNZIP_CMD=(gzip -dc)
    ;;
  *.sql)
    UNZIP_CMD=(cat)
    ;;
  *)
    die "只支持 .sql 或 .sql.gz 文件" 1
    ;;
esac

# 目标库存在性检查：不存在就提示先建库，避免 mysql 报一个看不懂的错
if [ -n "$TARGET_DB" ]; then
  exists="$(
    MYSQL_PWD="$DB_PASS" "${MYSQL[@]}" \
      --batch --skip-column-names \
      -e "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='${TARGET_DB}'" \
      2>/dev/null || true
  )"
  if [ -z "$exists" ]; then
    die "目标库 ${TARGET_DB} 不存在，请先执行：CREATE DATABASE ${TARGET_DB} DEFAULT CHARSET utf8mb4;" 1
  fi
fi

log_warn "即将把 $BACKUP_FILE 导入到 ${DB_HOST}:${DB_PORT} ${TARGET_DB:-（备份内的全部库）}"
if [ "$ASSUME_YES" != "yes" ]; then
  printf '确认继续请输入 yes（其它任意输入将取消）：'
  read -r answer
  [ "$answer" = "yes" ] || die "已取消，未做任何改动" 1
fi

# 导入：解压后直接喂给 mysql
# --default-character-set=utf8mb4 防止中文变问号
#
# 这里必须自己判断退出码并给出人话提示。因为最常见的失败不是文件坏了，
# 而是「用备份账号去恢复」——备份账号只有 SELECT，DROP/CREATE/INSERT 全会被拒，
# mysql 只会扔一句 ERROR 1142 DROP command denied，看不出该怎么办。
if [ -n "$TARGET_DB" ]; then
  if ! MYSQL_PWD="$DB_PASS" "${UNZIP_CMD[@]}" "$BACKUP_FILE" \
       | MYSQL_PWD="$DB_PASS" "${MYSQL[@]}" --default-character-set=utf8mb4 "$TARGET_DB"; then
    die "恢复失败。当前账号是 ${DB_USER}：
  如果是权限错误（ERROR 1142 / 1044），说明该账号没有写权限。
  备份账号通常只有 SELECT，能导出不能导入。
  恢复请显式指定有写权限的账号，例如：
    ./mysql_restore.sh -f $BACKUP_FILE -d $TARGET_DB -u root -p 你的root密码" 1
  fi
else
  if ! MYSQL_PWD="$DB_PASS" "${UNZIP_CMD[@]}" "$BACKUP_FILE" \
       | MYSQL_PWD="$DB_PASS" "${MYSQL[@]}" --default-character-set=utf8mb4; then
    die "恢复失败。若为权限错误，请用 -u/-p 指定有写权限的账号" 1
  fi
fi

log_info "恢复完成，建议抽查数据：SELECT COUNT(*) FROM <表名>;"
exit 0
