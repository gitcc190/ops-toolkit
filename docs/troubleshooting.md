# 踩坑记录

按「现象 → 原因 → 解法」组织。全部是在 Ubuntu 22.04 上真实遇到过的，不是抄来的。

---

## 1. `df -h` 的输出不能直接拿来比大小

**现象**

```bash
df -h | awk '$5 > 80 {print $6}'      # 99% 的分区没被选出来
```

**原因**

`df -h` 的容量列带单位：`19G`、`512M`。awk 遇到非纯数字会按字符串比较，
`"100%" > "9%"` 在字符串比较里是 false（`1` 小于 `9`）。而且 `-h` 为了对齐会插入空格，
按空白切分列会错位。

**解法**

用 `df -P`（POSIX 格式，每个文件系统保证一行），取纯数字列，再用 `gsub` 去掉百分号：

```bash
df -P -x tmpfs -x devtmpfs | awk 'NR>1 { u=$5; gsub("%","",u); if (u+0 >= 80) print $6, u }'
```

`u+0` 是让 awk 强制按数值比较的关键。

---

## 2. `mysqldump | gzip` 备份"成功"了，但文件只有 20 字节

**现象**

```bash
mysqldump -u root -p ops_demo | gzip > backup.sql.gz
echo $?          # 0
ls -lh backup.sql.gz   # 20 字节
```

**原因**

`$?` 拿到的是管道最后一个命令（`gzip`）的退出码，gzip 正常压缩了 0 字节输入，所以返回 0。
mysqldump 的报错（密码错、库不存在）被吞掉了。

**解法**

三件事一起做：

1. 脚本头写 `set -o pipefail`，管道中任一环失败即失败；
2. 备份后校验 `[ -s 文件 ]`（非空）；
3. 再跑 `gzip -t` 验证压缩流完整。

本仓库 `mysql_backup.sh` 三件都做了。实测把密码改错后，脚本会以退出码 1 结束并打印
`备份失败：Access denied for user ...`。

---

## 3. `mysqldump --single-transaction` 只对 InnoDB 有效

**现象**

备份过程中业务写入，恢复后数据有缺口。

**原因**

`--single-transaction` 依靠 InnoDB 的 MVCC 起一个一致性快照。
如果库里有 MyISAM 表，它依然会被锁住或读到不一致状态。

**解法**

```bash
# 先查有没有非 InnoDB 的表
mysql -e "SELECT table_schema, table_name, engine FROM information_schema.tables
          WHERE engine <> 'InnoDB' AND table_schema NOT IN ('mysql','sys','information_schema');"
```

有就单独处理（`--lock-tables` 会锁表，业务低峰做），没有就可以放心用 `--single-transaction`。

---

## 4. cron 里脚本报 `command not found`

**现象**

手动跑 `./scripts/mysql_backup.sh` 正常，加到 crontab 后日志里写 `mysqldump: command not found`。

**原因**

cron 的环境变量和登录 shell 完全不同，`PATH` 通常只有 `/usr/bin:/bin`。
MySQL 装在 `/usr/local/mysql/bin` 时自然找不到。

**解法**

两种任选：

```bash
# 方案 A：crontab 顶部声明 PATH
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/mysql/bin

# 方案 B：脚本里写绝对路径
/usr/bin/mysqldump ...
```

排查命令：`crontab -l` 确认写进去了，`grep CRON /var/log/syslog` 看有没有执行记录。

---

## 5. 脚本有执行权限，但仍然 `Permission denied`

**现象**

```bash
ls -l scripts/disk_alert.sh
# -rwxr-xr-x 1 ops ops ... disk_alert.sh
./scripts/disk_alert.sh
# bash: ./scripts/disk_alert.sh: Permission denied
```

**原因**

不是文件本身的问题，是**路径上某一级目录没有 `x` 权限**。
`cd` 进目录需要 `x`，读目录内容需要 `r`。如果仓库放在 `/root/` 或家目录权限是 `700`，
别人（或 sudo 后的其他用户）就进不去。

**解法**

```bash
namei -l /opt/ops-toolkit/scripts/disk_alert.sh    # 逐级列出权限，一眼看出哪级断了
```

把仓库放到 `/opt/` 这类所有用户可进入的目录，或者给路径每一级补 `x`。

---

## 6. `read` 在 `set -e` 下遇到 EOF 会让脚本静默退出

**现象**

交互确认的那句 `read -r answer` 在非交互环境（cron、Jenkins）里直接退出，脚本什么也没做。

**原因**

`read` 读到 EOF 返回非 0，`set -e` 立即终止脚本。

**解法**

```bash
read -r answer || answer=""        # 显式兜住失败
if [ "$answer" != "yes" ]; then
  die "已取消"
fi
```

或者提供 `-y` 参数在自动化里跳过确认（本仓库 `mysql_restore.sh` 就是这么做的）。

---

## 7. `paramiko` 连 CentOS 7 报 `No existing session`

**现象**

```python
paramiko.ssh_exception.SSHException: No existing session
```

**原因**

CentOS 7 的 OpenSSH 7.4 默认不支持 `ssh-rsa`（SHA-1）签名，
而新版 paramiko 也默认禁用了它。两边都不让步，握不上手。

**解法**

```python
client.connect(
    hostname=host,
    username=user,
    key_filename=key_path,
    disabled_algorithms={"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]},
)
```

或者干脆把靶机换成 Ubuntu 22.04（默认 `rsa-sha2-512`，没这个问题）。

---

## 8. Kali 默认不允许 root 直接 SSH 登录

**现象**

用 root 连 Kali 靶机，报 `Permission denied (publickey,password)`，密码明明是对的。

**原因**

Kali 的 `/etc/ssh/sshd_config` 默认 `PermitRootLogin prohibit-password`，
只允许密钥，不允许密码。

**解法**

```bash
# 方案 A（推荐）：建普通用户，用普通用户连
sudo useradd -m -s /bin/bash ops
sudo passwd ops

# 方案 B：允许 root 密码登录（只在自己实验环境做）
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

改完用 `ss -lntp | grep :22` 确认 sshd 真的在监听——Kali 装完默认可能没启动 ssh 服务。

---

## 9. 容器里跑 `disk_alert.sh` 看到的磁盘和宿主机不一样

**现象**

```bash
docker run --rm ops-toolkit:0.1.0 disk_alert -t 80
# 只看到一个 overlay 挂载点，使用率和宿主机对不上
```

**原因**

容器有自己的挂载命名空间。`df` 在容器里看到的是容器视角的文件系统，
宿主机的 `/` 被镜像层和 volume 挡住了。

**解法**

要么把宿主机根目录挂进去：

```bash
docker run --rm -v /:/host:ro ops-toolkit:0.1.0 disk_alert -m /host -t 80
```

要么接受这个事实——**容器化的磁盘巡检只能看容器自己**，宿主机巡检应该由 node-exporter
或宿主机上的 cron 来做。这个坑想清楚，比代码本身重要。

---

## 10. `chmod +x` 之后 git 里看不到权限变化

**现象**

```bash
chmod +x scripts/new_tool.sh
git status        # 没有变化
```

**原因**

Windows 上的 Git 默认 `core.filemode=false`，不跟踪文件权限位。
或者文件已经被 `git add` 过，权限变更是单独的一次索引更新。

**解法**

```bash
git config core.filemode true          # 仓库级开启
git update-index --chmod=+x scripts/new_tool.sh
git commit -m "chore: 给 new_tool.sh 增加可执行权限"
```

在 Linux 上 clone 后如果脚本没有 `x`，基本就是这个原因。

---

## 11. `MYSQL_PWD` 被 `~/.my.cnf` 静默覆盖（最坑的一条）

**现象**

配置文件里明明写了正确的密码，备份却报认证失败：

```bash
cat conf/backup.env
# DB_USER=backup_user
# DB_PASS=YOUR_BACKUP_PASSWORD

./scripts/mysql_backup.sh
# [ERROR] 备份失败：mysqldump: Got error: 1045: Access denied for user
#         'backup_user'@'localhost' (using password: YES)
```

但同样的密码用 `-p` 直接给就成功：

```bash
mysql -h127.0.0.1 -ubackup_user -p'YOUR_BACKUP_PASSWORD' -e "SELECT 1"   # 成功
```

**原因**

**`MYSQL_PWD` 环境变量的优先级低于配置文件。** MySQL 客户端启动时按顺序读
`/etc/my.cnf` → `/etc/mysql/my.cnf` → `~/.my.cnf`，其中任何一处写了 `password=`
都会覆盖 `MYSQL_PWD`。

很多机器上 `/root/.my.cnf` 就长这样：

```ini
[client]
user=root
password=YOUR_ROOT_PASSWORD
```

于是脚本以 root 身份运行时，客户端实际用的是 `root/YOUR_ROOT_PASSWORD`，
`-ubackup_user` 覆盖了用户名却没覆盖密码，认证自然失败。

**验证方法**（三个实验，一看就明白）

```bash
# A. 直接用 MYSQL_PWD —— 失败
MYSQL_PWD="YOUR_BACKUP_PASSWORD" mysql -h127.0.0.1 -ubackup_user -e "SELECT 1"

# B. 加 --no-defaults 屏蔽配置文件 —— 成功
MYSQL_PWD="YOUR_BACKUP_PASSWORD" mysql --no-defaults -h127.0.0.1 -ubackup_user -e "SELECT 1"

# C. 把 ~/.my.cnf 临时移走 —— 成功，证明元凶确实是它
mv /root/.my.cnf /root/.my.cnf.bak
MYSQL_PWD="YOUR_BACKUP_PASSWORD" mysql -h127.0.0.1 -ubackup_user -e "SELECT 1"
mv /root/.my.cnf.bak /root/.my.cnf
```

**解法**

所有 `mysqldump` / `mysql` 调用都加 `--no-defaults`，并且**必须放在第一个参数位置**：

```bash
MYSQL_PWD="$DB_PASS" mysqldump --no-defaults \
  --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" ... 
```

项目里 `mysql_backup.sh` 与 `mysql_restore.sh` 都已经这么写了。

---

## 12. `pgrep -f` 会匹配到"正在执行它的进程"，守护静默失效

**现象**

进程守护脚本判定"运行正常"，但实际进程根本不存在：

```bash
python3 python/proc_guard.py --name demo \
  --check "pgrep -f 'sleep 3601'" \
  --start "nohup sleep 3601 >/dev/null 2>&1 &" --once
# [INFO ] demo 运行正常        ← 假的
pgrep -af "sleep 3601"
# （什么都没输出，进程真的不在）
```

**原因**

`pgrep -f` 匹配的是**完整命令行**。而你执行这条命令时，
`python3 python/proc_guard.py --check "pgrep -f 'sleep 3601'"` 这个 python 进程，
它的命令行里就含有 `sleep 3601` 这串字符——于是 `pgrep` 匹配到了调用它的父进程。

同理，`subprocess.run(cmd, shell=True)` 会先起一个 `/bin/sh -c "<你的命令>"`，
这个 sh 的 cmdline 也含同样的字符串，是第二层假阳性。

**这是 `pgrep -f` 的固有行为，不是脚本 bug，改代码修不干净。**

**解法**

`--check` 改用能精确匹配进程名或服务状态的命令：

```bash
# ✅ 按进程名精确匹配（最推荐）
--check "pgrep -x nginx"

# ✅ 按服务状态
--check "systemctl is-active nginx"

# ✅ 按进程名查（不用 pgrep）
--check "ps -C nginx"

# ❌ 不要用这个
--check "pgrep -f 'python app.py'"
```

项目里的 `run()` 已经用 `sh -c "exec <命令>"` 去掉了最直接的那层 sh 壳，
但调用者自己的命令行仍然会被 `pgrep -f` 匹配到，所以写法才是根本。

---

## 13. systemd 单元里写 `User=ops`，服务起不来

**现象**

```bash
sudo systemctl start ops-toolkit.service
sudo systemctl status ops-toolkit.service
# Active: failed
# ... Failed to determine user credentials: No such process
```

**原因**

单元文件里写了 `User=ops` / `Group=ops`，但系统里没有 `ops` 这个用户。
systemd 的报错只说"无法确定用户凭据"，完全看不出是"用户不存在"。

**解法**

先建用户，或者干脆不写（默认用 root）：

```bash
# 方案 A：建一个不可登录的系统用户
useradd -r -s /usr/sbin/nologin ops

# 方案 B：删掉 User= / Group= 两行，用 root 跑
```

项目里的 `conf/ops-toolkit.service` 默认不写 `User=`，注释里说明了原因。

---

## 14. 备份账号能导出，但**不能**恢复

**现象**

```bash
./scripts/mysql_restore.sh -f backup.sql.gz -d ops_demo -y
# ERROR 1142 (42000) at line 22: DROP command denied to user 'backup_user'@'localhost'
```

**原因**

这是设计使然，不是配置错误。备份账号按最小权限原则只给了：

```sql
GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER ON ops_demo.* TO 'backup_user'@'127.0.0.1';
```

`SELECT` 足够 `mysqldump` 导出，但恢复需要 `DROP` / `CREATE` / `INSERT`，全没有。

**解法**

恢复时显式指定有写权限的账号（脚本支持 `-u` / `-p` 覆盖配置）：

```bash
./scripts/mysql_restore.sh -f backup.sql.gz -d ops_demo -u root -p '你的root密码' -y
```

生产环境建议单独建一个 `restore_user`，只给需要恢复的那几个库的
`SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES`，
不要直接用 root。

---

## 15. 备份文件权限是 644，同组用户都能读到全量数据

**现象**

```bash
ls -l /var/backups/mysql/
# -rw-r--r-- 1 root root 1815 ... ops_demo_20261124_094652.sql.gz
```

目录是 700，但文件本身 644。

**原因**

脚本里 `chmod 700 "$BACKUP_DIR"` 只保护了目录，
新建文件用的是当前 shell 的 `umask`，默认 022 会导致 644。

**解法**

在备份脚本里显式设置 `umask 077`，让新建文件和目录都是"仅属主可读写"：

```bash
umask 077
mkdir -p "$BACKUP_DIR"
```

项目里 `mysql_backup.sh` 已经加了这一行。

---

## 16. 容器里验证文件是否进镜像，被 ENTRYPOINT 挡住了

**现象**

```bash
docker run --rm ops-toolkit:0.1.0 ls -a /opt/ops-toolkit
# 未知子命令：ls，执行 help 查看用法
```

**原因**

Dockerfile 里设了 `ENTRYPOINT [".../entrypoint.sh"]`，
`docker run <镜像> <参数>` 里的参数会全部交给 entrypoint，`ls` 被当成子命令。

**解法**

用 `--entrypoint` 覆盖：

```bash
docker run --rm --entrypoint ls ops-toolkit:0.1.0 -a /opt/ops-toolkit
docker run --rm --entrypoint test ops-toolkit:0.1.0 -e /opt/ops-toolkit/.git && echo 存在
```

**顺带说**：验证 `.dockerignore` 是否生效，就该这么查，
不要看 `docker build` 的日志里有没有报错——它不会报错，只是静默不生效。

---

## 17. 管道后面的 `$?` 不是脚本的退出码

**现象**

```bash
./scripts/disk_alert.sh -t 50 | head -5
echo "退出码：$?"        # 0
```

明明触发了告警（应该返回 3），却显示 0。

**原因**

`$?` 取的是**管道最后一个命令**（这里是 `head`）的退出码，不是 `disk_alert.sh` 的。

**解法**

```bash
# 方式 1：不接管道
./scripts/disk_alert.sh -t 50 >/dev/null; echo $?

# 方式 2：用 PIPESTATUS（bash 专有）
./scripts/disk_alert.sh -t 50 | head -5
echo "${PIPESTATUS[0]}"

# 方式 3：set -o pipefail 后再取 $?
set -o pipefail
./scripts/disk_alert.sh -t 50 | head -5
echo $?
```

验证退出码时一律用方式 1，最不容易出错。

---

## 18. `set -e` 下测试告警退出码，脚本中途就退出了

**现象**

想在一个脚本里连续测试"正常"和"告警"两种情况：

```bash
set -e
./scripts/disk_alert.sh -t 95      # 返回 0，继续
./scripts/disk_alert.sh -t 50      # 返回 3，脚本直接退出
echo "接下来要清理临时文件"          # 永远执行不到
```

结果临时挂载点没卸载、测试镜像没删除，留下一堆垃圾。

**原因**

`set -e` 遇到任何非 0 退出码立即终止。而"告警退出码 3"本身就是**预期结果**，
但在 `set -e` 眼里它就是失败。

**解法**

对预期会返回非 0 的命令显式加保护：

```bash
set -e
./scripts/disk_alert.sh -t 95

# 方式 1：|| true 吞掉退出码
./scripts/disk_alert.sh -t 50 || true

# 方式 2：放 if 里
if ./scripts/disk_alert.sh -t 50; then echo "未告警"; else echo "告警了，退出码 $?"; fi

# 方式 3：临时关掉
set +e
./scripts/disk_alert.sh -t 50
rc=$?
set -e
```

涉及挂载、临时文件、容器的测试脚本，一定要保证清理逻辑能执行到，
或者用 `trap 'cleanup' EXIT` 兜底。

---

## 19. paramiko 2.9.3 连新版 OpenSSH 失败

**现象**

Kali 2026 的 OpenSSH 是 10.3，paramiko 2.9.3 连接报错：

```
paramiko.ssh_exception.SSHException: No existing session
```
或
```
Unable to agree on a key exchange / 认证失败
```

**原因**

OpenSSH 8.8 起默认禁用 `ssh-rsa`（SHA-1 签名），只接受 `rsa-sha2-256/512` 和 `ed25519`。
paramiko 2.9.3 对 `rsa-sha2-*` 的支持不完整，用 RSA 密钥会协商失败。

**解法**

**用 ed25519 密钥，不要用 RSA**：

```bash
ssh-keygen -t ed25519 -N "" -C "ops-toolkit@ubuntu" -f ~/.ssh/id_ed25519
ssh-copy-id -i ~/.ssh/id_ed25519.pub lw@192.0.2.11
ssh-copy-id -i ~/.ssh/id_ed25519.pub kali@192.0.2.12
```

然后 `batch_ssh.py -k ~/.ssh/id_ed25519`。

实测：Ubuntu(OpenSSH 8.9) → CentOS 7.9(7.4)、Kali(10.3) 用 ed25519 均正常，
不需要升级 paramiko。

