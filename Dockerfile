# ---------- 前端构建（规范指定 SDK 镜像：node:24-alpine） ----------
FROM node:24-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=https://mirrors.cloud.tencent.com/npm/
COPY frontend/ ./
RUN npm run build

# ---------- 后端依赖（规范指定 SDK 镜像：python:3.12） ----------
FROM python:3.12 AS backend-deps
WORKDIR /build/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    -r requirements.txt

# ---------- 运行（python:3.12，配置全部通过环境变量注入） ----------
FROM python:3.12
ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8720

WORKDIR /app

COPY --from=backend-deps /install /usr/local
COPY backend/ ./backend/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

# SQLite 数据与上传文件目录（可挂载卷持久化：-v hh-data:/app/backend/data）
RUN mkdir -p /app/backend/data

EXPOSE 8720

# 单 API 进程 + 数据库持久队列 worker，避免多 uvicorn worker 各自抢后台任务。
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8720", "--workers", "1", "--app-dir", "/app/backend"]
