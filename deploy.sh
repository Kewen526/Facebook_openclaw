#!/bin/bash
# Facebook_openclaw 一键部署脚本
# 适用于 Ubuntu/Debian 服务器

set -e
cd "$(dirname "$0")/.."

echo "======================================"
echo "  Facebook_openclaw 部署"
echo "======================================"

echo ""
echo "[1/3] 安装 Python 依赖..."
pip install -r requirements.txt

echo ""
echo "[2/3] 安装 Playwright Chromium..."
playwright install chromium
playwright install-deps chromium 2>/dev/null || true

echo ""
echo "[3/3] 启动服务..."
pkill -f "server.py" 2>/dev/null || true
sleep 1

PORT=${PORT:-7788}
nohup python server.py > server.log 2>&1 &
sleep 2

PID=$(pgrep -f "server.py" || true)
if [ -n "$PID" ]; then
    IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || echo "your_server_ip")
    echo ""
    echo "✅ 启动成功！"
    echo ""
    echo "📌 访问地址: http://${IP}:${PORT}"
    echo "📋 查看日志: tail -f server.log"
    echo "🛑 停止服务: pkill -f server.py"
    echo ""
    echo "⚠️  请确认阿里云安全组已开放 ${PORT} 端口"
    echo "⚙️  首次使用请点右上角齿轮图标配置 API Key"
else
    echo "❌ 启动失败，查看日志："
    tail -20 server.log
fi
