"""
Browser-Use Web UI v3
支持经过筛选的模型：必须同时满足 Tool Calling + OpenAI兼容API
"""
import asyncio, base64, json, os, threading, time
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_from_directory

app = Flask(__name__, static_folder="static")
CONFIG_FILE = Path("config.json")
task_store = {}

# ══════════════════════════════════════════════
#  模型注册表（只收录经过验证的模型）
#  筛选标准：
#   ✅ 支持 Tool Calling（Browser-Use 必须）
#   ✅ OpenAI 兼容 API（可统一接入）
#   ✅ 支持多轮对话
#   ⭐ 推荐：同时支持 Vision（能看截图，效果更好）
# ══════════════════════════════════════════════
PROVIDERS = {
    "anthropic": {
        "name": "Anthropic",
        "flag": "🇺🇸",
        "base_url": None,  # 原生SDK
        "key_url": "https://console.anthropic.com/",
        "key_placeholder": "sk-ant-api03-...",
        "models": [
            {
                "id": "claude-sonnet-4-6",
                "name": "Claude Sonnet 4.6",
                "tags": ["⚡ 快", "👁 视觉", "🏆 推荐"],
                "vision": True,
                "note": "速度与能力最佳平衡，Browser-Use首选"
            },
            {
                "id": "claude-opus-4-6",
                "name": "Claude Opus 4.6",
                "tags": ["🧠 最强"],
                "vision": True,
                "note": "最强推理，复杂任务"
            },
        ]
    },
    "openai": {
        "name": "OpenAI",
        "flag": "🇺🇸",
        "base_url": None,
        "key_url": "https://platform.openai.com/api-keys",
        "key_placeholder": "sk-proj-...",
        "models": [
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "tags": ["👁 视觉", "🏆 推荐"],
                "vision": True,
                "note": "OpenAI旗舰，支持视觉"
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "tags": ["⚡ 快", "💰 便宜"],
                "vision": True,
                "note": "速度快，成本低"
            },
        ]
    },
    "deepseek": {
        "name": "DeepSeek",
        "flag": "🇨🇳",
        "base_url": "https://api.deepseek.com/v1",
        "key_url": "https://platform.deepseek.com/api_keys",
        "key_placeholder": "sk-...",
        "models": [
            {
                "id": "deepseek-chat",
                "name": "DeepSeek V3",
                "tags": ["💰 极便宜", "✅ Tool Call"],
                "vision": False,
                "note": "性价比极高，Tool Calling稳定"
            },
        ]
    },
    "kimi": {
        "name": "Kimi (月之暗面)",
        "flag": "🇨🇳",
        "base_url": "https://api.moonshot.cn/v1",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
        "key_placeholder": "sk-...",
        "models": [
            {
                "id": "kimi-k2",
                "name": "Kimi K2",
                "tags": ["🧠 推理强", "✅ Tool Call"],
                "vision": False,
                "note": "万亿参数MoE，Agent能力极强"
            },
            {
                "id": "moonshot-v1-32k",
                "name": "Moonshot v1 32K",
                "tags": ["⚡ 快", "💰 便宜"],
                "vision": False,
                "note": "稳定快速，日常任务"
            },
        ]
    },
    "zhipu": {
        "name": "智谱 (GLM)",
        "flag": "🇨🇳",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "key_placeholder": "填写智谱 API Key",
        "models": [
            {
                "id": "glm-4-plus",
                "name": "GLM-4 Plus",
                "tags": ["🏆 旗舰", "✅ Tool Call"],
                "vision": False,
                "note": "智谱最强模型，Tool Calling完整支持"
            },
            {
                "id": "glm-4-flash",
                "name": "GLM-4 Flash",
                "tags": ["⚡ 极快", "🆓 免费"],
                "vision": False,
                "note": "速度最快，有免费额度"
            },
        ]
    },
    "qwen": {
        "name": "通义千问 (阿里云)",
        "flag": "🇨🇳",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_url": "https://bailian.console.aliyun.com/?apiKey=1",
        "key_placeholder": "sk-...",
        "models": [
            {
                "id": "qwen-plus",
                "name": "Qwen Plus",
                "tags": ["⚡ 快", "✅ Tool Call"],
                "vision": False,
                "note": "阿里云主力模型，稳定可靠"
            },
            {
                "id": "qwen-max",
                "name": "Qwen Max",
                "tags": ["🧠 最强", "✅ Tool Call"],
                "vision": False,
                "note": "通义千问最强，复杂推理"
            },
        ]
    },
}

# ── 配置管理 ────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {"provider": "anthropic", "model": "claude-sonnet-4-6", "api_keys": {}}

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

# ── 构建 LLM ────────────────────────────────────────────────────────

def build_llm(cfg: dict):
    provider = cfg.get("provider", "anthropic")
    model_id  = cfg.get("model", "claude-sonnet-4-6")
    api_keys  = cfg.get("api_keys", {})
    pinfo     = PROVIDERS.get(provider, {})
    base_url  = pinfo.get("base_url")

    key = api_keys.get(provider) or os.getenv(f"{provider.upper()}_API_KEY", "")

    if not key:
        raise ValueError(f"未配置 {pinfo.get('name',provider)} 的 API Key，请点击右上角 ⚙️ 填写")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_id, api_key=key, temperature=0)

    else:
        # 所有其他服务商统一用 OpenAI 兼容接口
        from langchain_openai import ChatOpenAI
        kwargs = dict(model=model_id, api_key=key, temperature=0)
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)

# ── Browser-Use 执行器 ──────────────────────────────────────────────

def run_browser_task(task_id: str, task_text: str, cfg: dict):
    store = task_store[task_id]

    def log(msg, t="info"):
        store["logs"].append({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "type": t})

    store["status"] = "running"
    pname = PROVIDERS.get(cfg.get("provider",""), {}).get("name", cfg.get("provider",""))
    log(f"🚀 启动 | {pname} / {cfg.get('model')}")

    async def _run():
        try:
            from browser_use import Agent
            from browser_use.browser import BrowserConfig, Browser

            llm     = build_llm(cfg)
            browser = Browser(config=BrowserConfig(headless=True))
            log("🌐 浏览器已启动...")

            # 截图后台任务
            screenshot_on = True
            async def capture():
                while screenshot_on:
                    try:
                        pb = browser.playwright_browser
                        if pb and pb.contexts:
                            pages = pb.contexts[0].pages
                            if pages:
                                img = await pages[-1].screenshot(type="jpeg", quality=55)
                                store["screenshot"] = base64.b64encode(img).decode()
                    except Exception:
                        pass
                    await asyncio.sleep(0.8)

            agent   = Agent(task=task_text, llm=llm, browser=browser)
            cap_t   = asyncio.create_task(capture())
            log("⚙️  AI 开始分析任务...")

            history = await agent.run(max_steps=50)

            screenshot_on = False
            cap_t.cancel()
            await browser.close()

            result = ""
            if history and hasattr(history, "final_result"):
                result = history.final_result() or "✅ 任务完成"
            else:
                result = "✅ 任务完成"

            store.update(status="done", result=result)
            log("✅ 任务完成", "success")

        except ImportError as e:
            store.update(status="error", result=f"缺少依赖: {e}\n请运行:\npip install browser-use langchain-anthropic langchain-openai")
            log(f"❌ 缺少依赖: {e}", "error")
        except Exception as e:
            store.update(status="error", result=str(e))
            log(f"❌ {e}", "error")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())
    loop.close()

# ── Routes ──────────────────────────────────────────────────────────

@app.route("/")
def index(): return send_from_directory("static", "index.html")

@app.route("/api/providers")
def get_providers(): return jsonify(PROVIDERS)

@app.route("/api/config", methods=["GET"])
def get_config():
    cfg  = load_config()
    safe = json.loads(json.dumps(cfg))
    for k, v in safe.get("api_keys", {}).items():
        if v and len(v) > 8:
            safe["api_keys"][k] = v[:4] + "•" * max(len(v)-8, 4) + v[-4:]
    return jsonify(safe)

@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.json
    cfg  = load_config()
    for f in ("provider", "model"):
        if f in data: cfg[f] = data[f]
    cfg.setdefault("api_keys", {})
    for pid, val in data.get("api_keys", {}).items():
        if val and "•" not in val:
            cfg["api_keys"][pid] = val.strip()
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/run", methods=["POST"])
def run_task():
    task = (request.json or {}).get("task", "").strip()
    if not task: return jsonify({"error": "任务不能为空"}), 400
    cfg = load_config()
    tid = f"t{int(time.time()*1000)}"
    task_store[tid] = {"status": "pending", "logs": [], "result": "", "screenshot": ""}
    threading.Thread(target=run_browser_task, args=(tid, task, cfg), daemon=True).start()
    return jsonify({"task_id": tid})

@app.route("/api/stream/<tid>")
def stream(tid):
    def gen():
        li, ls = 0, ""
        while True:
            if tid not in task_store:
                yield f"data: {json.dumps({'type':'error','msg':'任务不存在'})}\n\n"; break
            s = task_store[tid]
            while li < len(s["logs"]):
                yield f"data: {json.dumps({'type':'log', **s['logs'][li]})}\n\n"; li += 1
            shot = s.get("screenshot","")
            if shot and shot != ls:
                yield f"data: {json.dumps({'type':'screenshot','data':shot})}\n\n"; ls = shot
            if s["status"] in ("done","error"):
                while li < len(s["logs"]):
                    yield f"data: {json.dumps({'type':'log',**s['logs'][li]})}\n\n"; li+=1
                yield f"data: {json.dumps({'type':'end','status':s['status'],'result':s['result']})}\n\n"; break
            time.sleep(0.5)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 7788))
    print(f"\n🚀 Browser-Use Web UI v3 → http://0.0.0.0:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
