# ============================================================
# Dockerfile —— 把整套工具打成镜像
# 目标：换台机器不用装依赖，docker run 就能用
# 构建：
#   cd ops-toolkit
#   docker build -t ops-toolkit:0.1.0 -f docker/Dockerfile .
# 运行：
#   docker run --rm ops-toolkit:0.1.0 disk_alert -t 90
# 注意：build 上下文必须是仓库根目录（最后的 .），
#       否则 COPY . 拿不到 scripts/ 与 python/
# ============================================================

FROM ubuntu:22.04

# 不交互安装，避免 apt 卡在时区选择
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    PATH=/opt/ops-toolkit/scripts:/opt/ops-toolkit/python:$PATH

# 一次装完再清 apt 缓存，镜像小一半
# gawk 提供 awk（Ubuntu 默认的 mawk 在某些正则上行为不同）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        coreutils \
        procps \
        gawk \
        grep \
        findutils \
        python3 \
        python3-paramiko \
        mysql-client \
        openssh-client \
        ca-certificates \
        iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ops-toolkit

# 先把依赖清单与代码分开拷贝，改代码时不会让 apt 层缓存失效
COPY . /opt/ops-toolkit

RUN chmod +x /opt/ops-toolkit/scripts/*.sh \
             /opt/ops-toolkit/docker/entrypoint.sh \
    && mkdir -p /var/log/ops-toolkit /var/backups/mysql

# 工具集没有常驻进程，入口脚本负责分发子命令
ENTRYPOINT ["/opt/ops-toolkit/docker/entrypoint.sh"]
CMD ["help"]
