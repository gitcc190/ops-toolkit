# ops-toolkit

一套自己写的运维小工具集：Shell 与 Python 双实现，统一日志格式与退出码，能打成镜像随处跑。

日常运维里那些重复动作——查磁盘、扒日志、备份库、看进程、批量执行——每次都临时敲一遍命令，
下次还得翻历史记录。这个仓库把它们沉淀成脚本：**有参数、有日志、有退出码、有说明**。

## 功能列表

| 工具 | 语言 | 一句话说明 |
|---|---|---|
| `scripts/disk_alert.sh` | Shell | 磁盘使用率超阈值告警，退出码 3 表示触发 |
| `scripts/nginx_top_ip.sh` | Shell | access.log 访问量 Top N 的客户端 IP |
| `scripts/nginx_status_code.sh` | Shell | 按状态码统计分布，单独提示 5xx 占比 |
| `scripts/mysql_backup.sh` | Shell | mysqldump 一致性备份 + gzip + 自动清理 N 天前 |
| `scripts/mysql_restore.sh` | Shell | 恢复前校验文件与目标库，默认要求人工确认 |
| `python/disk_monitor.py` | Python | 磁盘监控，支持 JSON 输出与 webhook 告警 |
| `python/log_analyzer.py` | Python | 日志分析，支持 `--top` `--status` `--since` |
| `python/proc_guard.py` | Python | 进程守护，挂掉自动拉起并记录次数 |
| `python/batch_ssh.py` | Python | paramiko 并发批量执行命令 |

## 快速开始

三步，不需要任何配置，也不依赖数据库：

```bash
git clone https://github.com/gitcc190/ops-toolkit.git
cd ops-toolkit
chmod +x scripts/*.sh docker/entrypoint.sh

# 1. 磁盘告警（只依赖 df 和 awk）
./scripts/disk_alert.sh -t 80
echo "退出码：$?"          # 0 正常 / 3 有分区超阈值 / 1 参数错

# 2. 日志分析（用仓库自带的样例日志）
./scripts/nginx_top_ip.sh -n 5 -f samples/access.log
python3 python/log_analyzer.py samples/access.log --top 5

# 3. 打成镜像，换台机器也能跑
docker build -t ops-toolkit:0.1.0 -f docker/Dockerfile .
docker run --rm -v "$PWD/samples:/logs:ro" ops-toolkit:0.1.0 top_ip -f /logs/access.log -n 5
```

MySQL 备份需要先准备配置：

```bash
cp conf/backup.env.example conf/backup.env
chmod 600 conf/backup.env          # 脚本会检查，不是 600 直接拒绝执行
vi conf/backup.env                 # 填 DB_USER / DB_PASS / DB_NAME
./scripts/mysql_backup.sh -c conf/backup.env
```

批量 SSH 需要一份主机清单（真实的 `hosts.txt` 已被 `.gitignore` 排除）：

```bash
cp hosts.example.txt hosts.txt
vi hosts.txt                       # 每行一台：user@host 或 user@host:port
python3 python/batch_ssh.py -H hosts.txt -c "uptime" -k ~/.ssh/id_ed25519
```

## 目录结构

```
ops-toolkit/
├── README.md
├── LICENSE
├── .gitignore
├── .dockerignore                构建上下文排除清单（防密码进镜像）
├── hosts.example.txt            批量 SSH 主机清单示例
├── scripts/                     Shell 工具
│   ├── lib/common.sh            公共函数：日志、die、require_cmd
│   ├── disk_alert.sh
│   ├── nginx_top_ip.sh
│   ├── nginx_status_code.sh
│   ├── mysql_backup.sh
│   └── mysql_restore.sh
├── python/                      Python 工具
│   ├── disk_monitor.py
│   ├── log_analyzer.py
│   ├── proc_guard.py
│   └── batch_ssh.py
├── docker/
│   ├── Dockerfile               ubuntu:22.04 + 依赖，约 270MB
│   └── entrypoint.sh            容器入口，按子命令分发
├── conf/
│   ├── ops-toolkit.cron         crontab 示例
│   ├── ops-toolkit.service      systemd 服务单元
│   ├── ops-toolkit.timer        systemd 定时器
│   └── backup.env.example       配置模板（不含真实密码）
├── samples/
│   └── access.log               固定种子生成的假日志，240 条有效 + 1 条畸形
└── docs/
    ├── usage.md                 每个工具的参数说明
    └── troubleshooting.md       踩坑记录
```

## 设计取舍

### 1. 为什么同一个「磁盘告警」写了 Shell 和 Python 两个版本

不是为了凑数。两者适用场景不同：

| | `disk_alert.sh` | `disk_monitor.py` |
|---|---|---|
| 依赖 | 只需 `df` `awk`，裸机也能跑 | 需要 Python 3 |
| 启动开销 | 毫秒级，适合 cron 每 5 分钟跑 | 百毫秒级 |
| 告警方式 | 输出到 stdout/stderr，靠 cron 邮件或日志采集 | 可直接 POST webhook |
| 输出 | 人类可读文本 | 可 `--json` 给别的程序消费 |
| 跨平台 | 只能在 Linux | 思路可移植到 Windows |

结论：**定时巡检用 Shell 版，需要接入告警平台用 Python 版**。

### 2. 为什么统一退出码

| 码 | 含义 | 谁用 |
|---|---|---|
| 0 | 正常 | 全部 |
| 1 | 参数错误 / 运行错误 | 全部 |
| 2 | 依赖缺失（缺命令、缺 paramiko） | 全部 |
| 3 | 检查项触发（磁盘超阈值 / 本次拉起成功 / 批量执行有失败） | `disk_alert` `disk_monitor` `proc_guard` `batch_ssh` |
| 4 | 放弃（连续拉起失败达到上限） | `proc_guard` |

`3` 这个约定很关键：磁盘超阈值不是"脚本出错"，而是"检查出问题了"。
把它和真正的错误区分开，cron 或 Jenkins 才能正确判断——否则每次告警都会被当成脚本故障。

Python 脚本里还有个细节：`argparse` 遇到参数错误固定返回 2，与本仓库「参数错误 = 1」的约定冲突。
三个脚本都在 `main()` 里捕获 `SystemExit` 统一成了 1，调用方只需要记一套规则。

### 3. 为什么日志格式统一成 `时间 [级别] 消息`

```
2026-12-08 02:00:01 [INFO ] 开始备份：数据库 ops_demo -> /var/backups/mysql/ops_demo_20261208_020001.sql.gz
2026-12-08 02:00:03 [INFO ] 备份完成，文件大小 4.0K
```

好处很直接：

- `grep '\[ERROR\]' *.log` 就能捞出所有错误，不用记每个脚本各自的格式；
- 要接 ELK / Loki 时，正则写一次就够；
- 时间戳带秒且可排序，多脚本日志 `sort` 后就是一条时间线。

### 4. 为什么密码放 `backup.env` 且权限必须 600

脚本会显式检查配置文件权限，不是 600 直接 `die`。原因：备份配置一旦被组内其他用户读到，
等于把数据库密码交出去了。这条检查是在防"三个月后接手的人随手 `chmod 644`"。

密码通过 `MYSQL_PWD` 环境变量传给 `mysqldump`，而不是 `-p密码`，后者会出现在 `ps aux` 里。

### 5. 为什么 `mysqldump` 一定要带 `--no-defaults`

这是本项目最容易踩、也最容易被忽略的一个坑：

**`MYSQL_PWD` 的优先级低于配置文件。** 如果执行者的家目录下有 `~/.my.cnf`
（很多机器上 `/root/.my.cnf` 就写着 root 的密码），客户端会优先用 my.cnf 里的密码，
把 `MYSQL_PWD` 静默覆盖掉。表现是：

```
[ERROR] 备份失败：mysqldump: Got error: 1045: Access denied for user 'backup_user'@'localhost' (using password: YES)
```

明明配置里写着 `DB_PASS=YOUR_BACKUP_PASSWORD`，却报认证失败。加 `--no-defaults` 屏蔽掉所有配置文件后正常。
`mysql_restore.sh` 里的每个 `mysql` 调用同理。

## 踩坑记录

完整版见 [docs/troubleshooting.md](docs/troubleshooting.md)，这里挑五条最典型的：

1. **`df` 用 `-h` 解析会翻车**：`df -h` 的容量列带单位（`19G`），awk 比大小会按字符串比较，
   `100%` 反而小于 `9%`。必须用 `df -P` 拿纯数字列，再去掉 `%` 转数值。
2. **`mysqldump` 返回成功但备份是空的**：`| gzip` 时上游失败不一定能传出来。
   备份完必须 `gzip -t` 校验 + 检查文件非空，两个都做。
3. **`MYSQL_PWD` 被 `~/.my.cnf` 覆盖**：见上面第 5 条。
4. **`pgrep -f` 会匹配到调用它的进程**：`proc_guard.py --check "pgrep -f 'sleep 3601'"`
   里的那个 python 进程，命令行本身就含 `sleep 3601`，于是永远判定"进程在"，
   进程死了也不拉起——守护静默失效。**改用 `pgrep -x <进程名>` 或 `systemctl is-active`。**
5. **`User=ops` 让 systemd 单元起不来**：示例单元里写 `User=ops`，但系统里没这个用户，
   `systemctl start` 只报 `Failed to determine user credentials`，看不出是用户不存在。

## License

[MIT](LICENSE)
