#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  browser-use 官方 Web UI 一键部署脚本
#  https://github.com/browser-use/web-ui
# ═══════════════════════════════════════════════════════════
set -e

INSTALL_DIR="/root/browser-use-webui"
PORT=7788

echo "═══════════════════════════════════════════════════"
echo "  browser-use 官方 Web UI 部署"
echo "═══════════════════════════════════════════════════"

# ── 1. 停止旧服务 ──
echo ""
echo "[1/6] 停止旧服务..."
pkill -f "server.py" 2>/dev/null || true
pkill -f "webui.py" 2>/dev/null || true
sleep 1
echo "  ✅ 旧服务已停止"

# ── 2. 克隆仓库 ──
echo ""
echo "[2/6] 克隆 browser-use/web-ui..."
if [ -d "$INSTALL_DIR" ]; then
    echo "  ⚠️  目录已存在，更新代码..."
    cd "$INSTALL_DIR"
    git pull origin main 2>/dev/null || git pull 2>/dev/null || true
else
    # 尝试通过 ghfast 代理克隆（国内加速）
    git clone https://ghfast.top/https://github.com/browser-use/web-ui.git "$INSTALL_DIR" 2>/dev/null \
        || git clone https://github.com/browser-use/web-ui.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
echo "  ✅ 代码就绪: $INSTALL_DIR"

# ── 3. 安装依赖 ──
echo ""
echo "[3/6] 安装 Python 依赖..."
if command -v uv &>/dev/null; then
    echo "  使用 uv 安装..."
    uv pip install -r requirements.txt --python python3.11 2>/dev/null \
        || pip3.11 install -r requirements.txt
else
    echo "  使用 pip 安装..."
    pip3.11 install -r requirements.txt
fi
echo "  ✅ Python 依赖安装完成"

# ── 4. 安装 Playwright 浏览器 ──
echo ""
echo "[4/6] 安装 Playwright 浏览器..."
python3.11 -m playwright install chromium --with-deps 2>/dev/null \
    || python3.11 -m playwright install chromium \
    || echo "  ⚠️  Playwright 浏览器可能已安装"
echo "  ✅ 浏览器就绪"

# ── 5. 配置 .env ──
echo ""
echo "[5/6] 配置环境变量..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "  ✅ 已从 .env.example 创建 .env"
    else
        touch .env
        echo "  ✅ 已创建空 .env"
    fi
else
    echo "  ✅ .env 已存在，保留现有配置"
fi

# 添加/更新智谱 GLM 配置提示
echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║  配置 API Key:                                   ║"
echo "  ║  编辑 $INSTALL_DIR/.env                          ║"
echo "  ║                                                  ║"
echo "  ║  智谱 GLM (通过 OpenAI 兼容接口):                ║"
echo "  ║    OPENAI_API_KEY=你的智谱APIKey                 ║"
echo "  ║    OPENAI_ENDPOINT=https://open.bigmodel.cn/api/paas/v4  ║"
echo "  ║                                                  ║"
echo "  ║  或直接使用 OpenAI / Anthropic:                  ║"
echo "  ║    OPENAI_API_KEY=sk-proj-...                    ║"
echo "  ║    ANTHROPIC_API_KEY=sk-ant-...                  ║"
echo "  ╚══════════════════════════════════════════════════╝"

# ── 6. 启动服务 ──
echo ""
echo "[6/6] 启动 Web UI..."
cd "$INSTALL_DIR"
nohup python3.11 webui.py --ip 0.0.0.0 --port $PORT > webui.log 2>&1 &
sleep 3

if tail -5 webui.log 2>/dev/null | grep -q "Running on"; then
    echo "  ✅ 启动成功！"
else
    echo "  ⏳ 启动中，等待更多时间..."
    sleep 5
    tail -10 webui.log 2>/dev/null
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "  🚀 browser-use Web UI 已部署！"
echo ""
echo "  访问地址: http://$(hostname -I | awk '{print $1}'):${PORT}"
echo "  日志查看: tail -f $INSTALL_DIR/webui.log"
echo ""
echo "  ⚠️  记得编辑 $INSTALL_DIR/.env 填写 API Key！"
echo "═══════════════════════════════════════════════════"
