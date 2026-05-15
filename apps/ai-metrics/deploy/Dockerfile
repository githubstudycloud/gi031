FROM python:3.12-slim

WORKDIR /app

# 系统依赖（pymysql 走纯 Python；如改 mysqlclient 需要 build-essential + libmariadb-dev）
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml /app/
COPY app /app/app
COPY prototype /app/prototype

RUN pip install --upgrade pip && pip install -e ".[mysql]"

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
