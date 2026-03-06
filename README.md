# Facebook_openclaw

基于 [Browser-Use](https://github.com/browser-use/browser-use) 的 AI 浏览器自动化 Web UI。

支持通过网页界面输入任务，由 AI 自动操控浏览器执行，实时显示浏览器画面和日志。

## 功能

- 🌐 实时浏览器画面推流
- 📋 任务日志实时显示
- 🤖 支持多个 AI 服务商（Anthropic / OpenAI / DeepSeek / Kimi / 智谱 / 通义千问）
- ⚙️ 前端直接配置 API Key，无需改代码
- 📦 快捷任务：Facebook 找代发货客户、1688 选品、汇率查询等

## 快速部署

### 1. 克隆仓库

```bash
git clone https://github.com/your_username/Facebook_openclaw.git
cd Facebook_openclaw
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
```

### 3. 启动服务

```bash
python server.py
```

默认端口 `7788`，可通过环境变量修改：

```bash
PORT=8080 python server.py
```

### 4. 后台运行（服务器推荐）

```bash
nohup python server.py > server.log 2>&1 &
tail -f server.log      # 查看日志
pkill -f server.py      # 停止服务
```

### 5. 阿里云安全组

开放 `7788` 端口（入方向）。

## 配置 API Key

启动后访问 `http://你的IP:7788`，点击右上角 **⚙️** 按钮：

1. 选择服务商（Anthropic / OpenAI / DeepSeek / Kimi / 智谱 / 通义千问）
2. 选择模型
3. 填写对应 API Key（点击「申请 Key →」直达官方控制台）
4. 点击保存

Key 保存在服务器本地 `config.json`，不会上传到任何第三方。

## 支持的模型

所有收录模型均经过筛选，必须同时满足：
- ✅ 支持 Tool Calling（Browser-Use 必须）
- ✅ OpenAI 兼容 API

| 服务商 | 模型 | 特点 |
|--------|------|------|
| Anthropic | Claude Sonnet 4.6 | 视觉 + Tool Call，首选 |
| Anthropic | Claude Opus 4.6 | 最强推理 |
| OpenAI | GPT-4o | 视觉 + Tool Call |
| OpenAI | GPT-4o Mini | 快速便宜 |
| DeepSeek | DeepSeek V3 | 极低价格 |
| Kimi | Kimi K2 | 万亿参数，Agent 能力强 |
| Kimi | Moonshot v1 32K | 稳定快速 |
| 智谱 | GLM-4 Plus | 旗舰模型 |
| 智谱 | GLM-4 Flash | 极快，有免费额度 |
| 通义千问 | Qwen Plus | 阿里云主力 |
| 通义千问 | Qwen Max | 最强推理 |

## 文件结构

```
Facebook_openclaw/
├── server.py          # Flask 后端（SSE推流 + Browser-Use）
├── static/
│   └── index.html     # 前端界面
├── requirements.txt   # Python 依赖
├── config.json        # API Key 配置（自动生成，勿提交）
├── scripts/
│   └── deploy.sh      # 一键部署脚本
└── README.md
```

## 注意事项

- 首次运行 Facebook 等需要登录的网站，可能触发验证码，建议先在本地用 `headless=False` 模式完成登录，保存 Session 后再上服务器
- `config.json` 包含 API Key，已加入 `.gitignore`，请勿提交到公开仓库

## License

MIT
