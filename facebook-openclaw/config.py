import os
from urllib.parse import quote_plus

# ============================================================
# 数据库
# ============================================================
DB_HOST     = os.environ.get('DB_HOST',     '127.0.0.1')
DB_PORT     = int(os.environ.get('DB_PORT', '3306'))
DB_USER     = os.environ.get('DB_USER',     'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_NAME     = os.environ.get('DB_NAME',     'facebook_openclaw')

DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)

# ============================================================
# Flask
# ============================================================
FLASK_PORT   = int(os.environ.get('FLASK_PORT', '8080'))
FLASK_SECRET = os.environ.get('FLASK_SECRET', 'change-me-in-production')

# ============================================================
# OpenClaw Gateway 配置（单实例）
# 服务器上已运行 openclaw-gateway，监听 18789 端口
# ============================================================
# Gateway API 地址（本机访问）
OPENCLAW_API_URL     = os.environ.get('OPENCLAW_API_URL', 'http://127.0.0.1:18789')
# Gateway 鉴权 token（来自 openclaw.json gateway.auth.token）
OPENCLAW_AUTH_TOKEN  = os.environ.get('OPENCLAW_AUTH_TOKEN', '15386f2dfc54fc186314846c80f35922')
# 使用的 AI 模型（Qwen3，来自 openclaw.json agents.defaults.model.primary）
OPENCLAW_API_MODEL   = os.environ.get('OPENCLAW_API_MODEL', 'qwen3-max-2026-01-23')
# 心跳检测间隔（秒）
OPENCLAW_HEARTBEAT_INTERVAL = int(os.environ.get('OPENCLAW_HEARTBEAT_INTERVAL', '60'))

# AI 分析：3票表决，至少几票为"是"才判定为目标客户
AI_VOTE_THRESHOLD    = int(os.environ.get('AI_VOTE_THRESHOLD', '2'))
AI_VOTE_TOTAL        = int(os.environ.get('AI_VOTE_TOTAL', '3'))

# ============================================================
# Facebook 监控参数
# ============================================================
# 监控的页面列表（OpenClaw 浏览器会依次打开）
MONITOR_PAGES = [
    'https://www.facebook.com/',
    'https://www.facebook.com/groups/feed/',
]
# 搜索关键词（用于判断帖子是否进入 AI 分析流程前的快速过滤）
SEARCH_KEYWORDS = ['dropshipping', 'drop shipping', 'supplier', 'wholesale', 'sourcing']
# 每轮监控最多抓取帖子数
MAX_POSTS_PER_SCAN   = int(os.environ.get('MAX_POSTS_PER_SCAN', '50'))
# 两轮监控之间的等待时间（秒）
MONITOR_INTERVAL     = int(os.environ.get('MONITOR_INTERVAL', '30'))
# 每次滚动后等待时间范围（秒）
SCROLL_WAIT_MIN      = float(os.environ.get('SCROLL_WAIT_MIN', '1.5'))
SCROLL_WAIT_MAX      = float(os.environ.get('SCROLL_WAIT_MAX', '3.5'))

# ============================================================
# 发送任务频率控制
# ============================================================
# 每个发送账号每天最多执行的任务数
DAILY_SEND_LIMIT     = int(os.environ.get('DAILY_SEND_LIMIT', '4'))
# 同一账号两次任务之间的最短间隔（秒）
SEND_COOLDOWN        = int(os.environ.get('SEND_COOLDOWN', '600'))
# 任务失败后最多重试次数
TASK_MAX_RETRY       = int(os.environ.get('TASK_MAX_RETRY', '2'))

# ============================================================
# 互动概率（模拟真实用户行为）
# ============================================================
INTEREST_PROBABILITY     = float(os.environ.get('INTEREST_PROBABILITY',     '1.0'))   # 目标帖点"有兴趣"
NOT_INTEREST_PROBABILITY = float(os.environ.get('NOT_INTEREST_PROBABILITY', '0.15'))  # 非目标帖点"没兴趣"
LIKE_PROBABILITY         = float(os.environ.get('LIKE_PROBABILITY',         '0.002')) # 随机点赞（极低）
