#!/bin/bash
# facebook-openclaw 一键部署脚本
# 目标服务器: 47.93.145.191（已运行 openclaw-gateway）
# 用法: bash deploy.sh
#
# 前置条件：
#   - 本机能 SSH 免密登录到服务器（或在提示时输入密码）
#   - 服务器已安装 python3 (>=3.10)、pip3、mysql

set -e

# ====== 配置（按需修改） ======
SERVER="root@47.93.145.191"
REMOTE_DIR="/opt/facebook-openclaw"
SERVICE_NAME="fb-openclaw"
PYTHON="python3"

# 数据库（脚本会帮你在服务器上创建）
DB_NAME="facebook_openclaw"
DB_USER="fbapp"
DB_PASS="fbapp_pass_$(date +%s | tail -c4)"   # 首次运行随机生成；再次运行会覆盖 .env

# Flask 端口（避开 8080 searxng / 18789 openclaw）
FLASK_PORT=5000
FLASK_SECRET="$(openssl rand -hex 24 2>/dev/null || cat /dev/urandom | tr -dc 'a-f0-9' | head -c48)"

# ====================================
echo "========================================="
echo "  facebook-openclaw 一键部署"
echo "  目标: $SERVER"
echo "========================================="

# ====== 1. 打包项目 ======
echo ""
echo "[1/6] 打包项目文件..."
TMPFILE=$(mktemp /tmp/fb-openclaw-XXXXX.tar.gz)
tar czf "$TMPFILE" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='deploy.sh' \
    -C "$(dirname "$0")" .
echo "  -> 打包完成"

# ====== 2. 上传 ======
echo ""
echo "[2/6] 上传到服务器..."
ssh "$SERVER" "mkdir -p $REMOTE_DIR"
scp "$TMPFILE" "$SERVER:/tmp/fb-openclaw.tar.gz"
ssh "$SERVER" "cd $REMOTE_DIR && tar xzf /tmp/fb-openclaw.tar.gz && rm -f /tmp/fb-openclaw.tar.gz"
rm -f "$TMPFILE"
echo "  -> 上传完成"

# ====== 3. 安装 Python 依赖 ======
echo ""
echo "[3/6] 安装 Python 依赖..."
ssh "$SERVER" "
    set -e
    cd $REMOTE_DIR

    # 检查 python3 版本
    PY_VER=\$($PYTHON -c 'import sys; print(sys.version_info.minor)')
    if [ \"\$PY_VER\" -lt 10 ]; then
        echo '!! 需要 Python 3.10+，当前版本不满足' && exit 1
    fi

    # 创建虚拟环境（如不存在）
    if [ ! -d venv ]; then
        $PYTHON -m venv venv
    fi
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    echo '  -> Python 依赖安装完成'
"

# ====== 4. 初始化数据库 ======
echo ""
echo "[4/6] 初始化数据库..."
ssh "$SERVER" "
    set -e

    # 确保 MySQL 在运行
    systemctl is-active mysql || systemctl is-active mysqld || {
        echo '!! MySQL 未运行，尝试启动...'
        systemctl start mysql 2>/dev/null || systemctl start mysqld 2>/dev/null || true
    }

    # 创建库和用户（如已存在则跳过错误）
    mysql -u root -e \"
        CREATE DATABASE IF NOT EXISTS $DB_NAME
            CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
        CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
        GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'localhost';
        FLUSH PRIVILEGES;
    \" 2>/dev/null || mysql -u root --skip-password -e \"
        CREATE DATABASE IF NOT EXISTS $DB_NAME
            CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
        CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
        GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'localhost';
        FLUSH PRIVILEGES;
    \"
    echo '  -> 数据库就绪'
"

# ====== 5. 写入 .env 并建表 ======
echo ""
echo "[5/6] 写入配置并建表..."
ssh "$SERVER" "
    set -e
    cat > $REMOTE_DIR/.env << 'ENVEOF'
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_NAME=$DB_NAME
FLASK_PORT=$FLASK_PORT
FLASK_SECRET=$FLASK_SECRET
OPENCLAW_API_URL=http://127.0.0.1:18789
OPENCLAW_AUTH_TOKEN=15386f2dfc54fc186314846c80f35922
OPENCLAW_API_MODEL=qwen3-max-2026-01-23
ENVEOF

    # 加载 .env 并建表
    cd $REMOTE_DIR
    source venv/bin/activate
    export \$(grep -v '^#' .env | xargs)
    $PYTHON -c \"from models import init_db; init_db(); print('  -> 数据表已就绪')\"

    # 创建 admin 账号（如不存在）
    $PYTHON - << 'PYEOF'
import os, sys
from models import User, get_session
db = get_session()
if not db.query(User).filter(User.username == 'admin').first():
    u = User(username='admin', role='admin')
    u.set_password('admin888')
    db.add(u)
    db.commit()
    print('  -> 已创建默认管理员账号: admin / admin888  (请登录后修改密码)')
else:
    print('  -> admin 账号已存在，跳过创建')
db.close()
PYEOF
"

# ====== 6. systemd 服务 ======
echo ""
echo "[6/6] 配置并启动 systemd 服务..."
ssh "$SERVER" "
    cat > /etc/systemd/system/${SERVICE_NAME}.service << UNIT
[Unit]
Description=Facebook OpenClaw Management API
After=network.target mysqld.service mysql.service

[Service]
Type=simple
WorkingDirectory=${REMOTE_DIR}
EnvironmentFile=${REMOTE_DIR}/.env
ExecStart=${REMOTE_DIR}/venv/bin/python ${REMOTE_DIR}/app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    systemctl restart ${SERVICE_NAME}
    systemctl enable  ${SERVICE_NAME} 2>/dev/null
    sleep 2
    systemctl is-active ${SERVICE_NAME} && echo '  -> 服务运行中' || echo '!! 服务启动失败，请查看日志'
"

echo ""
echo "========================================="
echo "  部署完成!"
echo ""
echo "  管理后台 API: http://47.93.145.191:${FLASK_PORT}"
echo "  默认账号: admin / admin888"
echo "  (首次登录后请立即修改密码)"
echo ""
echo "  查看日志:  ssh $SERVER journalctl -u $SERVICE_NAME -f"
echo "  重启服务:  ssh $SERVER systemctl restart $SERVICE_NAME"
echo "  查看状态:  ssh $SERVER systemctl status $SERVICE_NAME"
echo ""
echo "  测试 OpenClaw API 连通性（在服务器上执行）:"
echo "  curl -s http://127.0.0.1:18789/v1/chat/completions \\"
echo "    -H 'Authorization: Bearer 15386f2dfc54fc186314846c80f35922' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\":\"qwen3-max-2026-01-23\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"max_tokens\":20}'"
echo "========================================="
