#!/bin/bash
# facebook-openclaw 一键部署脚本
# 目标服务器: 47.93.145.191（已运行 openclaw-gateway）
# 用法: bash deploy.sh
#
# 前置条件：
#   - 本机能 SSH 免密登录到服务器（或在提示时输入密码）
#   - 服务器能访问 GitHub（或已配置代理）
#   - 服务器已安装 git、python3 (>=3.10)、pip3、mysql

set -e

# ====== 配置（按需修改） ======
SERVER="root@47.93.145.191"
GITHUB_REPO="https://github.com/Kewen526/Facebook_openclaw.git"
BRANCH="main"
REMOTE_DIR="/opt/facebook-openclaw"
APP_SUBDIR="facebook-openclaw"          # 仓库内的子目录
SERVICE_NAME="fb-openclaw"
PYTHON="python3"

# 数据库
DB_NAME="facebook_openclaw"
DB_USER="fbapp"
DB_PASS="Fbapp@2026"                     # ← 生产环境请修改

# Flask 端口（避开 8080 searxng / 18789 openclaw）
FLASK_PORT=5000
FLASK_SECRET="$(openssl rand -hex 24 2>/dev/null || echo 'please-change-this-secret-key')"

# ====================================
echo "========================================="
echo "  facebook-openclaw 一键部署"
echo "  目标: $SERVER"
echo "  仓库: $GITHUB_REPO ($BRANCH)"
echo "========================================="

# ====== 1. 拉取代码 ======
echo ""
echo "[1/6] 拉取 GitHub 代码..."
ssh "$SERVER" "
    set -e
    if [ -d '$REMOTE_DIR/.git' ]; then
        echo '  -> 仓库已存在，执行 git pull...'
        cd '$REMOTE_DIR'
        git fetch origin
        git checkout '$BRANCH'
        git reset --hard origin/'$BRANCH'
    else
        echo '  -> 首次部署，克隆仓库...'
        git clone --branch '$BRANCH' --depth 1 '$GITHUB_REPO' '$REMOTE_DIR'
    fi
    echo '  -> 代码已更新到最新 main 分支'
"

# ====== 2. 安装 Python 依赖 ======
echo ""
echo "[2/6] 安装 Python 依赖..."
ssh "$SERVER" "
    set -e
    cd '$REMOTE_DIR/$APP_SUBDIR'

    # 检查 python3 版本 ≥ 3.10
    PY_MINOR=\$($PYTHON -c 'import sys; print(sys.version_info.minor)')
    if [ \"\$PY_MINOR\" -lt 10 ]; then
        echo '!! 需要 Python 3.10+，当前版本不满足' && exit 1
    fi

    # 创建/更新虚拟环境
    [ -d venv ] || $PYTHON -m venv venv
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    echo '  -> Python 依赖安装完成'
"

# ====== 3. 初始化 MySQL 数据库 ======
echo ""
echo "[3/6] 初始化数据库..."
ssh "$SERVER" "
    set -e

    # 确保 MySQL 运行
    systemctl is-active --quiet mysql   2>/dev/null || \
    systemctl is-active --quiet mysqld  2>/dev/null || {
        systemctl start mysql  2>/dev/null || \
        systemctl start mysqld 2>/dev/null || true
    }

    # 创建库和用户
    mysql -u root 2>/dev/null <<SQL || \
    mysql -u root --skip-password 2>/dev/null <<SQL
CREATE DATABASE IF NOT EXISTS $DB_NAME
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON $DB_NAME.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
    echo '  -> 数据库就绪'
"

# ====== 4. 写 .env 配置 ======
echo ""
echo "[4/6] 写入 .env 配置..."
ssh "$SERVER" "
    cat > '$REMOTE_DIR/$APP_SUBDIR/.env' << 'ENVEOF'
# 数据库
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_NAME=$DB_NAME

# Flask
FLASK_PORT=$FLASK_PORT
FLASK_SECRET=$FLASK_SECRET

# OpenClaw Gateway（已在本机 18789 运行）
OPENCLAW_API_URL=http://127.0.0.1:18789
OPENCLAW_AUTH_TOKEN=15386f2dfc54fc186314846c80f35922
OPENCLAW_API_MODEL=qwen3-max-2026-01-23
ENVEOF
    chmod 600 '$REMOTE_DIR/$APP_SUBDIR/.env'
    echo '  -> .env 已写入'
"

# ====== 5. 建表 + 创建 admin 账号 ======
echo ""
echo "[5/6] 初始化数据库表和管理员账号..."
ssh "$SERVER" "
    set -e
    cd '$REMOTE_DIR/$APP_SUBDIR'
    source venv/bin/activate
    export \$(grep -v '^#' .env | xargs -d '\n')

    $PYTHON - << 'PYEOF'
from models import init_db, User, get_session
init_db()
print('  -> 数据表已就绪')

db = get_session()
try:
    if not db.query(User).filter(User.username == 'admin').first():
        u = User(username='admin', role='admin')
        u.set_password('admin888')
        db.add(u)
        db.commit()
        print('  -> 创建默认管理员: admin / admin888  (请登录后修改密码!)')
    else:
        print('  -> admin 账号已存在')
finally:
    db.close()
PYEOF
"

# ====== 6. 配置 systemd 服务 ======
echo ""
echo "[6/6] 配置并启动 systemd 服务..."
ssh "$SERVER" "
    cat > /etc/systemd/system/${SERVICE_NAME}.service << 'UNIT'
[Unit]
Description=Facebook OpenClaw Management API
After=network.target mysql.service mysqld.service

[Service]
Type=simple
WorkingDirectory=${REMOTE_DIR}/${APP_SUBDIR}
EnvironmentFile=${REMOTE_DIR}/${APP_SUBDIR}/.env
ExecStart=${REMOTE_DIR}/${APP_SUBDIR}/venv/bin/python ${REMOTE_DIR}/${APP_SUBDIR}/app.py
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
    systemctl is-active ${SERVICE_NAME} && echo '  -> 服务已启动' || echo '!! 服务启动失败'
"

echo ""
echo "========================================="
echo "  部署完成!"
echo ""
echo "  管理后台: http://47.93.145.191:${FLASK_PORT}"
echo "  默认账号: admin / admin888"
echo "  (首次登录后请立即修改密码)"
echo ""
echo "  常用命令（在服务器上执行）:"
echo "  查看日志: journalctl -u ${SERVICE_NAME} -f"
echo "  重启服务: systemctl restart ${SERVICE_NAME}"
echo "  服务状态: systemctl status ${SERVICE_NAME}"
echo ""
echo "  验证 OpenClaw API 连通（在服务器上）:"
echo "  curl -s http://127.0.0.1:18789/v1/chat/completions \\"
echo "    -H 'Authorization: Bearer 15386f2dfc54fc186314846c80f35922' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\":\"qwen3-max-2026-01-23\",\"messages\":[{\"role\":\"user\",\"content\":\"你好\"}],\"max_tokens\":20}'"
echo "========================================="
