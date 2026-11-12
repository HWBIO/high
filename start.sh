#!/bin/bash
# 一键启动猎头匹配 Agent（后端 + 前端，默认仅本机可访问）
# 用法：双击或在终端运行 ./start.sh，Ctrl+C 停止
cd "$(dirname "$0")"

# 后端
if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
  ./backend/.venv/bin/pip install -r backend/requirements.txt
fi
HOST="${HOST:-127.0.0.1}"

(cd backend && ./.venv/bin/uvicorn app.main:app --host "$HOST" --port 8720) &
BACKEND_PID=$!

# 前端默认绑定 127.0.0.1。若确需内网演示，显式运行 HOST=内网IP ./start.sh。
if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi
(cd frontend && npm run dev -- --host "$HOST") &
FRONTEND_PID=$!

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "本机IP")
echo ""
echo "=============================================="
echo "  本机访问:   http://localhost:5274"
echo "  绑定地址:   ${HOST}"
echo "  同事访问:   HOST=${IP} ./start.sh 后访问 http://${IP}:5274"
echo "  按 Ctrl+C 停止服务"
echo "=============================================="

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
