# 参数说明

按「我想干什么」组织，不是按文件名罗列。所有脚本都支持 `-h` 查看帮助。

## 约定

- 所有脚本退出码一致：`0` 正常 / `1` 参数或运行错误 / `2` 依赖缺失 / `3` 检查项触发 / `4` 放弃（仅 proc_guard）
- 日志格式统一：`YYYY-mm-dd HH:MM:SS [级别 ] 消息`
- 可以用 `LOG_LEVEL=DEBUG` 环境变量打开调试输出

---

## 我想看磁盘还剩多少

### `disk_alert.sh`（Shell，适合定时巡检）

| 参数 | 说明 | 默认 |
|---|---|---|
| `-t 阈值` | 使用率百分比，超过则告警 | 80 |
| `-m 挂载点` | 只检查指定挂载点 | 全部本地文件系统 |
| `-h` | 帮助 | — |

```bash
./scripts/disk_alert.sh                 # 默认 80%
./scripts/disk_alert.sh -t 90 -m /      # 只看根分区，阈值 90
echo $?                                 # 3 = 有分区超阈值
```

### `disk_monitor.py`（Python，适合接告警）

| 参数 | 说明 | 默认 |
|---|---|---|
| `-t/--threshold` | 阈值百分比，可以是小数 | 80 |
| `-m/--mount` | 只检查指定挂载点 | 全部 |
| `--webhook URL` | 超阈值时 POST 到这个地址 | 不发送 |
| `--json` | 以 JSON 输出，便于程序消费 | 关闭 |
| `-v/--verbose` | 调试日志 | 关闭 |

```bash
python3 python/disk_monitor.py --json | jq '.alert_count'
python3 python/disk_monitor.py -t 85 --webhook https://example.com/hook
```

---

## 我想分析 Nginx 日志

### `nginx_top_ip.sh`

| 参数 | 说明 | 默认 |
|---|---|---|
| `-n 数量` | 显示前几名 | 10 |
| `-f 文件` | 日志路径（也可以直接放第一个位置参数） | 必填 |

```bash
./scripts/nginx_top_ip.sh -n 10 -f /var/log/nginx/access.log
./scripts/nginx_top_ip.sh /var/log/nginx/access.log
```

### `nginx_status_code.sh`

```bash
./scripts/nginx_status_code.sh /var/log/nginx/access.log
```

输出状态码、次数、占比，并在末尾单独提示 5xx 总量与占比（值班时第一眼要看的东西）。

### `log_analyzer.py`（功能最全）

| 参数 | 说明 | 默认 |
|---|---|---|
| `logfile` | 日志路径 | 必填 |
| `-n/--top` | 前 N 名 | 10 |
| `-s/--status` | 只统计指定状态码 | 全部 |
| `--since` | 只看该时间之后，格式 `YYYY-mm-dd HH:MM:SS` 或 `YYYY-mm-dd` | 不限 |

```bash
python3 python/log_analyzer.py samples/access.log --top 5
python3 python/log_analyzer.py samples/access.log --status 500
python3 python/log_analyzer.py samples/access.log --since "2026-12-08 10:00:00"
```

一次输出四块：Top IP、状态码分布、Top 路径、响应体最大的请求。

---

## 我想备份 / 恢复 MySQL

### 准备配置

```bash
cp conf/backup.env.example conf/backup.env
chmod 600 conf/backup.env
vi conf/backup.env
```

| 变量 | 说明 |
|---|---|
| `DB_HOST` / `DB_PORT` | 连接地址，默认 127.0.0.1:3306 |
| `DB_USER` / `DB_PASS` | 备份账号，建议单独建只读+锁表权限的账号 |
| `DB_NAME` | 要备份的库，`ALL` 表示全部 |
| `BACKUP_DIR` | 备份目录，建议独立数据盘 |
| `KEEP_DAYS` | 保留天数，默认 7 |

### `mysql_backup.sh`

| 参数 | 说明 |
|---|---|
| `-c 配置文件` | 默认 `conf/backup.env` |
| `-d 库名` | 覆盖配置里的 `DB_NAME`；`ALL` 表示全部 |

```bash
./scripts/mysql_backup.sh -c conf/backup.env
./scripts/mysql_backup.sh -c conf/backup.env -d ops_demo
```

自动做三件事：`--single-transaction` 一致性备份 → gzip 压缩 → 删 N 天前文件。

### `mysql_restore.sh`

| 参数 | 说明 |
|---|---|
| `-f 备份文件` | 必填，支持 `.sql` 与 `.sql.gz` |
| `-d 目标库` | 必填（`--all-databases` 的备份可省略） |
| `-c 配置文件` | 默认 `conf/backup.env` |
| `-u 用户` | 覆盖配置里的 `DB_USER`，恢复通常需要 root |
| `-p 密码` | 覆盖配置里的 `DB_PASS` |
| `-y` | 跳过交互确认，仅限自动化 |

```bash
# 用备份账号恢复会失败：它只有 SELECT，没有 DROP/CREATE/INSERT
# 必须显式指定有写权限的账号
./scripts/mysql_restore.sh -f /var/backups/mysql/ops_demo_20261208_020001.sql.gz \
  -d ops_demo -u root -p 'YOUR_ROOT_PASSWORD'
```

恢复前会做四件事：文件存在且非空 → gzip 完整性校验 → 目标库存在性检查 →
要求输入 `yes` 确认。恢复失败时会把 `ERROR 1142 / 1044` 翻译成"当前账号没写权限，
请用 -u root"这类能直接照做的提示。

**为什么备份账号不能恢复**：这是最小权限原则的必然结果，不是配置错误。
备份账号只需要 `SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER` 就能导出；
恢复要 `DROP / CREATE / INSERT / ALTER`，给了就等于把生产库的写权限交给一个自动化脚本。
生产上建议单独建 `restore_user`，只授权需要恢复的那几个库。

---

## 我想守护进程

### `proc_guard.py`

| 参数 | 说明 | 默认 |
|---|---|---|
| `--name` | 进程名，用于日志与状态文件 | 必填 |
| `--check` | 检测命令，退出码 0 表示活着 | 必填 |
| `--start` | 拉起命令 | 必填 |
| `--interval` | 轮询间隔（秒） | 60 |
| `--max-retry` | 连续失败多少次后放弃 | 3 |
| `--state` | 状态文件路径 | `/tmp/proc_guard_<name>.json` |
| `--once` | 只检查一次 | 关闭 |

```bash
python3 python/proc_guard.py \
  --name nginx \
  --check "pgrep -x nginx" \
  --start "sudo systemctl start nginx" \
  --interval 30

# 查这个进程累计被拉起过几次
cat /tmp/proc_guard_nginx.json
```

---

## 我想批量在多台机器上执行命令

### `batch_ssh.py`

| 参数 | 说明 | 默认 |
|---|---|---|
| `-H/--hosts` | 主机清单文件 | 必填 |
| `-c/--command` | 要执行的命令 | 必填 |
| `-u/--user` | 全局 SSH 用户（清单里写了 `user@host` 则以清单为准） | 空 |
| `-k/--key` | 私钥路径 | 空（改走密码） |
| `-p/--password-env` | 存密码的环境变量名 | `SSH_PASS` |
| `-t/--timeout` | 单台超时秒数 | 15 |
| `-w/--workers` | 并发数 | 10 |
| `--json` | 输出 JSON 汇总 | 关闭 |

**主机清单支持三种写法**：

```
192.168.1.11                 用 -u 指定的全局用户名
192.168.1.11:2222            指定端口
lw@192.168.1.11:2222         逐台指定用户名
```

逐台写用户名这条很关键：真实环境里同一个网段经常混着不同账号
（Ubuntu 用 `lw`、Kali 用 `kali`、CentOS 用 `root`），
一个全局 `-u` 根本覆盖不了，只会一半成功一半 `Permission denied`。

```bash
# 同构环境：全局一个用户名就够
python3 python/batch_ssh.py -H hosts.txt -c "uptime" -u ops -k ~/.ssh/id_ed25519

# 异构环境：清单里逐台写 user@host，不用给 -u
python3 python/batch_ssh.py -H hosts.txt -c "uptime" -k ~/.ssh/id_ed25519

# 用密码（从环境变量读，不会进 shell history 和 ps）
read -rs SSH_PASS && export SSH_PASS
python3 python/batch_ssh.py -H hosts.txt -c "df -h" -u ops

# 机器多了想接监控系统，用 --json
python3 python/batch_ssh.py -H hosts.txt -c "nproc" --json -k ~/.ssh/id_ed25519
```

**为什么用 ed25519 而不是 RSA**：OpenSSH 8.8 之后默认禁用 `ssh-rsa`（SHA-1），
paramiko 2.9.3 对 `rsa-sha2-*` 支持不完整，用 RSA 密钥连新版 OpenSSH 会协商失败。
详见 `troubleshooting.md` 第 19 条。

有任意一台失败时退出码为 3，方便流水线判断。单台失败不会中断其他机器的执行。

