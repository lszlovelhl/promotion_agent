# 推广申请处理 Agent - Docker 镜像
# 构建: docker build -t promotion_agent .
# 运行: docker compose up -d
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# 数据卷：飞书配置 / 处理记录 / 幂等指纹持久化
VOLUME ["/app/data"]

EXPOSE 8889

# 启动时自动连接飞书长连接并启动 Web 监管后台
CMD ["python", "app.py"]
