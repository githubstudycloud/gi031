# syntax=docker/dockerfile:1.6
#
# 支持自定义下载源（内网部署友好）：
#   PYTHON_IMAGE     基础镜像（例：python:3.12-slim / harbor.intra/python:3.12-slim）
#   APT_MIRROR       Debian APT 源（例：http://mirrors.tuna.tsinghua.edu.cn/debian）
#   PIP_INDEX_URL    pip 镜像（例：https://pypi.tuna.tsinghua.edu.cn/simple/）
#   PIP_TRUSTED_HOST 当 PIP_INDEX_URL 是 HTTPS 自签时填 host
#   NPM_REGISTRY     npm 镜像（本镜像无 npm 依赖；保留 ARG 以便后续前端构建）
#
# 公网构建：docker build .
# 内网构建：docker build \
#     --build-arg APT_MIRROR=http://nexus.intra/repository/debian-proxy \
#     --build-arg PIP_INDEX_URL=https://nexus.intra/repository/pypi-public/simple/ \
#     --build-arg PIP_TRUSTED_HOST=nexus.intra .

ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS base

ARG APT_MIRROR=""
ARG PIP_INDEX_URL=""
ARG PIP_TRUSTED_HOST=""
ARG NPM_REGISTRY=""

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# APT 源替换（仅在传入 APT_MIRROR 时生效）。Debian 12 用新的 deb822 格式 + 老格式兜底
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
        if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
            sed -i "s|http[s]\?://deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
            sed -i "s|http[s]\?://security.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources; \
        fi; \
        if [ -f /etc/apt/sources.list ]; then \
            sed -i "s|http[s]\?://deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list; \
            sed -i "s|http[s]\?://security.debian.org|$APT_MIRROR|g" /etc/apt/sources.list; \
        fi; \
        echo "[apt mirror] -> $APT_MIRROR"; \
    fi

# 装系统包（pymysql 是纯 Python 不需要 build-essential；mysqlclient 才需要）
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        curl ca-certificates tini \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# pip 源固化（持久化生效）
RUN set -eux; \
    if [ -n "$PIP_INDEX_URL" ]; then \
        pip config set global.index-url "$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi; \
        echo "[pip mirror] -> $PIP_INDEX_URL (trusted=$PIP_TRUSTED_HOST)"; \
    fi; \
    pip install --upgrade pip

# npm 源（占位，本镜像无 npm；保留以便后续合并前端构建到同镜像）
RUN if [ -n "$NPM_REGISTRY" ] && command -v npm >/dev/null 2>&1; then \
        npm config set registry "$NPM_REGISTRY"; \
        echo "[npm mirror] -> $NPM_REGISTRY"; \
    fi

WORKDIR /app

# 必须先把 app/ 也拷进来才能 pip install -e .
# （pyproject 的 setuptools.packages 引用了 ["app","app.api","..."], 否则解析失败）
COPY pyproject.toml /app/
COPY app /app/app
COPY prototype /app/prototype
COPY frontend /app/frontend

RUN pip install --no-cache-dir ".[mysql]" || pip install --no-cache-dir "."

EXPOSE 8001
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
