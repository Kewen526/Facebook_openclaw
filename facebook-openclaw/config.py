import os

# ============================================================
# 数据库
# ============================================================
DB_HOST     = os.environ.get('DB_HOST',     '127.0.0.1')
DB_PORT     = int(os.environ.get('DB_PORT', '3306'))
DB_USER     = os.environ.get('DB_USER',     'root')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_NAME     = os.environ.get('DB_NAME',     'facebook_openclaw')

DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    "?charset=utf8mb4"
)

# ============================================================
# Flask
# ============================================================
FLASK_PORT   = int(os.environ.get('FLASK_PORT', '8080'))
FLASK_SECRET = os.environ.get('FLASK_SECRET', 'change-me-in-production')

# ============================================================
# OpenClaw 多实例配置
# ============================================================
# 第一个账号占用 BASE_PORT，第二个占 BASE_PORT+1，以此类推
OPENCLAW_BASE_PORT   = int(os.environ.get('OPENCLAW_BASE_PORT', '18789'))
# openclaw 可执行文件路径（安装后一般在 PATH 里）
OPENCLAW_BIN         = os.environ.get('OPENCLAW_BIN', 'openclaw')
# 各实例数据目录的根目录，每个账号会创建子目录 account_<id>
OPENCLAW_DATA_ROOT   = os.environ.get('OPENCLAW_DATA_ROOT', os.path.expanduser('~/.openclaw'))
# 实例启动最长等待时间（秒）
OPENCLAW_START_TIMEOUT = int(os.environ.get('OPENCLAW_START_TIMEOUT', '30'))
# 心跳检测间隔（秒）：Python 定期 ping 各实例
OPENCLAW_HEARTBEAT_INTERVAL = int(os.environ.get('OPENCLAW_HEARTBEAT_INTERVAL', '60'))

# ============================================================
# ZhipuAI（帖子分析 + 话术生成）
# ============================================================
ZHIPU_MODEL          = os.environ.get('ZHIPU_MODEL', 'glm-4-flash')
ZHIPU_KEY_SERVER_URL = os.environ.get('ZHIPU_KEY_SERVER_URL', 'http://47.95.157.46:8520/get_keys')
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
