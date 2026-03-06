"""
Browser-Use Web UI v4
支持经过筛选的模型：必须同时满足 Tool Calling + OpenAI兼容API
新增：任务取消、超时控制、Cookie/Session 持久化、.env 支持
"""
import asyncio, base64, json, os, signal, threading, time
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_from_directory

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="static")
CONFIG_FILE = Path("config.json")
SESSION_DIR = Path("sessions")
SESSION_DIR.mkdir(exist_ok=True)

task_store = {}
# 存储每个任务的取消事件和线程引用
task_cancel = {}

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
        "flag": "\U0001f1fa\U0001f1f8",
        "base_url": None,
        "key_url": "https://console.anthropic.com/",
        "key_placeholder": "sk-ant-api03-...",
        "models": [
            {
                "id": "claude-sonnet-4-6",
                "name": "Claude Sonnet 4.6",
                "tags": ["\u26a1 \u5feb", "\U0001f441 \u89c6\u89c9", "\U0001f3c6 \u63a8\u8350"],
                "vision": True,
                "note": "\u901f\u5ea6\u4e0e\u80fd\u529b\u6700\u4f73\u5e73\u8861\uff0cBrowser-Use\u9996\u9009"
            },
            {
                "id": "claude-opus-4-6",
                "name": "Claude Opus 4.6",
                "tags": ["\U0001f9e0 \u6700\u5f3a"],
                "vision": True,
                "note": "\u6700\u5f3a\u63a8\u7406\uff0c\u590d\u6742\u4efb\u52a1"
            },
        ]
    },
    "openai": {
        "name": "OpenAI",
        "flag": "\U0001f1fa\U0001f1f8",
        "base_url": None,
        "key_url": "https://platform.openai.com/api-keys",
        "key_placeholder": "sk-proj-...",
        "models": [
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "tags": ["\U0001f441 \u89c6\u89c9", "\U0001f3c6 \u63a8\u8350"],
                "vision": True,
                "note": "OpenAI\u65d7\u8230\uff0c\u652f\u6301\u89c6\u89c9"
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "tags": ["\u26a1 \u5feb", "\U0001f4b0 \u4fbf\u5b9c"],
                "vision": True,
                "note": "\u901f\u5ea6\u5feb\uff0c\u6210\u672c\u4f4e"
            },
        ]
    },
    "deepseek": {
        "name": "DeepSeek",
        "flag": "\U0001f1e8\U0001f1f3",
        "base_url": "https://api.deepseek.com/v1",
        "key_url": "https://platform.deepseek.com/api_keys",
        "key_placeholder": "sk-...",
        "models": [
            {
                "id": "deepseek-chat",
                "name": "DeepSeek V3",
                "tags": ["\U0001f4b0 \u6781\u4fbf\u5b9c", "\u2705 Tool Call"],
                "vision": False,
                "note": "\u6027\u4ef7\u6bd4\u6781\u9ad8\uff0cTool Calling\u7a33\u5b9a"
            },
        ]
    },
    "kimi": {
        "name": "Kimi (\u6708\u4e4b\u6697\u9762)",
        "flag": "\U0001f1e8\U0001f1f3",
        "base_url": "https://api.moonshot.cn/v1",
        "key_url": "https://platform.moonshot.cn/console/api-keys",
        "key_placeholder": "sk-...",
        "models": [
            {
                "id": "kimi-k2",
                "name": "Kimi K2",
                "tags": ["\U0001f9e0 \u63a8\u7406\u5f3a", "\u2705 Tool Call"],
                "vision": False,
                "note": "\u4e07\u4ebf\u53c2\u6570MoE\uff0cAgent\u80fd\u529b\u6781\u5f3a"
            },
            {
                "id": "moonshot-v1-32k",
                "name": "Moonshot v1 32K",
                "tags": ["\u26a1 \u5feb", "\U0001f4b0 \u4fbf\u5b9c"],
                "vision": False,
                "note": "\u7a33\u5b9a\u5feb\u901f\uff0c\u65e5\u5e38\u4efb\u52a1"
            },
        ]
    },
    "zhipu": {
        "name": "\u667a\u8c31 (GLM)",
        "flag": "\U0001f1e8\U0001f1f3",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "key_placeholder": "\u586b\u5199\u667a\u8c31 API Key",
        "models": [
            {
                "id": "glm-4-plus",
                "name": "GLM-4 Plus",
                "tags": ["\U0001f3c6 \u65d7\u8230", "\u2705 Tool Call"],
                "vision": False,
                "note": "\u667a\u8c31\u6700\u5f3a\u6a21\u578b\uff0cTool Calling\u5b8c\u6574\u652f\u6301"
            },
            {
                "id": "glm-4-flash",
                "name": "GLM-4 Flash",
                "tags": ["\u26a1 \u6781\u5feb", "\U0001f193 \u514d\u8d39"],
                "vision": False,
                "note": "\u901f\u5ea6\u6700\u5feb\uff0c\u6709\u514d\u8d39\u989d\u5ea6"
            },
        ]
    },
    "qwen": {
        "name": "\u901a\u4e49\u5343\u95ee (\u963f\u91cc\u4e91)",
        "flag": "\U0001f1e8\U0001f1f3",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_url": "https://bailian.console.aliyun.com/?apiKey=1",
        "key_placeholder": "sk-...",
        "models": [
            {
                "id": "qwen-plus",
                "name": "Qwen Plus",
                "tags": ["\u26a1 \u5feb", "\u2705 Tool Call"],
                "vision": False,
                "note": "\u963f\u91cc\u4e91\u4e3b\u529b\u6a21\u578b\uff0c\u7a33\u5b9a\u53ef\u9760"
            },
            {
                "id": "qwen-max",
                "name": "Qwen Max",
                "tags": ["\U0001f9e0 \u6700\u5f3a", "\u2705 Tool Call"],
                "vision": False,
                "note": "\u901a\u4e49\u5343\u95ee\u6700\u5f3a\uff0c\u590d\u6742\u63a8\u7406"
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

# ── Cookie/Session 持久化 ─────────────────────────────────────────

def get_session_file(domain: str) -> Path:
    safe_name = domain.replace(".", "_").replace("/", "_")
    return SESSION_DIR / f"{safe_name}_cookies.json"

def save_cookies(browser_context, domain: str):
    """保存浏览器 cookies 到本地文件"""
    try:
        import asyncio
        cookies = asyncio.get_event_loop().run_until_complete(
            browser_context.cookies()
        )
        sf = get_session_file(domain)
        sf.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
    except Exception:
        pass

async def load_cookies(browser_context, domain: str):
    """从本地文件加载 cookies 到浏览器"""
    sf = get_session_file(domain)
    if sf.exists():
        try:
            cookies = json.loads(sf.read_text())
            if cookies:
                await browser_context.add_cookies(cookies)
                return True
        except Exception:
            pass
    return False

async def save_cookies_async(browser_context, domain: str):
    """异步保存浏览器 cookies"""
    try:
        cookies = await browser_context.cookies()
        sf = get_session_file(domain)
        sf.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
    except Exception:
        pass

# ── 构建 LLM ────────────────────────────────────────────────────────

def build_llm(cfg: dict):
    provider = cfg.get("provider", "anthropic")
    model_id  = cfg.get("model", "claude-sonnet-4-6")
    api_keys  = cfg.get("api_keys", {})
    pinfo     = PROVIDERS.get(provider, {})
    base_url  = pinfo.get("base_url")

    key = api_keys.get(provider) or os.getenv(f"{provider.upper()}_API_KEY", "")

    if not key:
        raise ValueError(f"\u672a\u914d\u7f6e {pinfo.get('name',provider)} \u7684 API Key\uff0c\u8bf7\u70b9\u51fb\u53f3\u4e0a\u89d2 \u2699\ufe0f \u586b\u5199")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_id, api_key=key, temperature=0)

    else:
        from langchain_openai import ChatOpenAI
        kwargs = dict(model=model_id, api_key=key, temperature=0)
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)

# ── Browser-Use 执行器 ──────────────────────────────────────────────

def run_browser_task(task_id: str, task_text: str, cfg: dict):
    store = task_store[task_id]
    cancel_event = task_cancel.get(task_id)

    def log(msg, t="info"):
        store["logs"].append({"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "type": t})

    store["status"] = "running"
    pname = PROVIDERS.get(cfg.get("provider",""), {}).get("name", cfg.get("provider",""))
    log(f"\U0001f680 \u542f\u52a8 | {pname} / {cfg.get('model')}")

    max_steps = int(os.getenv("MAX_STEPS", 50))
    task_timeout = int(os.getenv("TASK_TIMEOUT", 600))
    headless = os.getenv("HEADLESS", "true").lower() != "false"

    async def _run():
        browser = None
        try:
            from browser_use import Agent
            from browser_use.browser import BrowserConfig, Browser

            # 检查是否已取消
            if cancel_event and cancel_event.is_set():
                store.update(status="error", result="\u4efb\u52a1\u5df2\u53d6\u6d88")
                log("\u26d4 \u4efb\u52a1\u5df2\u53d6\u6d88", "error")
                return

            llm     = build_llm(cfg)
            browser = Browser(config=BrowserConfig(headless=headless))
            log("\U0001f310 \u6d4f\u89c8\u5668\u5df2\u542f\u52a8...")

            # 尝试加载已保存的 cookies
            try:
                pb = browser.playwright_browser
                if pb and pb.contexts:
                    ctx = pb.contexts[0]
                    # 从任务文本中提取可能的域名
                    for domain in ["facebook.com", "1688.com", "google.com"]:
                        if domain in task_text.lower():
                            loaded = await load_cookies(ctx, domain)
                            if loaded:
                                log(f"\U0001f36a \u5df2\u52a0\u8f7d {domain} \u7684\u767b\u5f55\u72b6\u6001")
            except Exception:
                pass

            # 截图后台任务
            screenshot_on = True
            async def capture():
                while screenshot_on:
                    if cancel_event and cancel_event.is_set():
                        break
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
            log("\u2699\ufe0f  AI \u5f00\u59cb\u5206\u6790\u4efb\u52a1...")

            # 带超时的任务执行
            try:
                history = await asyncio.wait_for(
                    agent.run(max_steps=max_steps),
                    timeout=task_timeout
                )
            except asyncio.TimeoutError:
                store.update(status="error", result=f"\u4efb\u52a1\u8d85\u65f6\uff08\u8d85\u8fc7 {task_timeout} \u79d2\uff09")
                log(f"\u23f0 \u4efb\u52a1\u8d85\u65f6\uff08\u8d85\u8fc7 {task_timeout} \u79d2\uff09", "error")
                screenshot_on = False
                cap_t.cancel()
                return
            except asyncio.CancelledError:
                store.update(status="error", result="\u4efb\u52a1\u5df2\u53d6\u6d88")
                log("\u26d4 \u4efb\u52a1\u5df2\u53d6\u6d88", "error")
                screenshot_on = False
                cap_t.cancel()
                return

            screenshot_on = False
            cap_t.cancel()

            # 任务完成后保存 cookies
            try:
                pb = browser.playwright_browser
                if pb and pb.contexts:
                    ctx = pb.contexts[0]
                    for domain in ["facebook.com", "1688.com", "google.com"]:
                        if domain in task_text.lower():
                            await save_cookies_async(ctx, domain)
                            log(f"\U0001f36a \u5df2\u4fdd\u5b58 {domain} \u7684\u767b\u5f55\u72b6\u6001")
            except Exception:
                pass

            await browser.close()
            browser = None

            result = ""
            if history and hasattr(history, "final_result"):
                result = history.final_result() or "\u2705 \u4efb\u52a1\u5b8c\u6210"
            else:
                result = "\u2705 \u4efb\u52a1\u5b8c\u6210"

            store.update(status="done", result=result)
            log("\u2705 \u4efb\u52a1\u5b8c\u6210", "success")

        except ImportError as e:
            store.update(status="error", result=f"\u7f3a\u5c11\u4f9d\u8d56: {e}\n\u8bf7\u8fd0\u884c:\npip install browser-use langchain-anthropic langchain-openai")
            log(f"\u274c \u7f3a\u5c11\u4f9d\u8d56: {e}", "error")
        except Exception as e:
            store.update(status="error", result=str(e))
            log(f"\u274c {e}", "error")
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            # 清理取消事件
            task_cancel.pop(task_id, None)

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
            safe["api_keys"][k] = v[:4] + "\u2022" * max(len(v)-8, 4) + v[-4:]
    return jsonify(safe)

@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.json
    cfg  = load_config()
    for f in ("provider", "model"):
        if f in data: cfg[f] = data[f]
    cfg.setdefault("api_keys", {})
    for pid, val in data.get("api_keys", {}).items():
        if val and "\u2022" not in val:
            cfg["api_keys"][pid] = val.strip()
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/run", methods=["POST"])
def run_task():
    task = (request.json or {}).get("task", "").strip()
    if not task: return jsonify({"error": "\u4efb\u52a1\u4e0d\u80fd\u4e3a\u7a7a"}), 400
    cfg = load_config()
    tid = f"t{int(time.time()*1000)}"
    task_store[tid] = {"status": "pending", "logs": [], "result": "", "screenshot": ""}
    cancel_evt = threading.Event()
    task_cancel[tid] = cancel_evt
    threading.Thread(target=run_browser_task, args=(tid, task, cfg), daemon=True).start()
    return jsonify({"task_id": tid})

@app.route("/api/cancel/<tid>", methods=["POST"])
def cancel_task(tid):
    """取消正在执行的任务"""
    if tid not in task_store:
        return jsonify({"error": "\u4efb\u52a1\u4e0d\u5b58\u5728"}), 404
    store = task_store[tid]
    if store["status"] not in ("pending", "running"):
        return jsonify({"error": "\u4efb\u52a1\u5df2\u7ed3\u675f\uff0c\u65e0\u6cd5\u53d6\u6d88"}), 400
    # 设置取消标志
    evt = task_cancel.get(tid)
    if evt:
        evt.set()
    store.update(status="error", result="\u4efb\u52a1\u5df2\u88ab\u7528\u6237\u53d6\u6d88")
    store["logs"].append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "msg": "\u26d4 \u4efb\u52a1\u5df2\u88ab\u7528\u6237\u53d6\u6d88",
        "type": "error"
    })
    return jsonify({"ok": True})

@app.route("/api/sessions")
def list_sessions():
    """列出已保存的 cookie sessions"""
    sessions = []
    for f in SESSION_DIR.glob("*_cookies.json"):
        domain = f.stem.replace("_cookies", "").replace("_", ".")
        try:
            cookies = json.loads(f.read_text())
            sessions.append({
                "domain": domain,
                "cookie_count": len(cookies),
                "file": f.name
            })
        except Exception:
            pass
    return jsonify(sessions)

@app.route("/api/sessions/<domain>", methods=["DELETE"])
def delete_session(domain):
    """删除指定域名的 cookies"""
    sf = get_session_file(domain)
    if sf.exists():
        sf.unlink()
        return jsonify({"ok": True})
    return jsonify({"error": "\u672a\u627e\u5230\u8be5\u57df\u540d\u7684 session"}), 404

@app.route("/api/stream/<tid>")
def stream(tid):
    def gen():
        li, ls = 0, ""
        NL = "\n\n"
        while True:
            if tid not in task_store:
                msg = json.dumps({"type": "error", "msg": "任务不存在"})
                yield f"data: {msg}{NL}"
                break
            s = task_store[tid]
            while li < len(s["logs"]):
                msg = json.dumps({"type": "log", **s["logs"][li]})
                yield f"data: {msg}{NL}"
                li += 1
            shot = s.get("screenshot", "")
            if shot and shot != ls:
                msg = json.dumps({"type": "screenshot", "data": shot})
                yield f"data: {msg}{NL}"
                ls = shot
            if s["status"] in ("done", "error"):
                while li < len(s["logs"]):
                    msg = json.dumps({"type": "log", **s["logs"][li]})
                    yield f"data: {msg}{NL}"
                    li += 1
                msg = json.dumps({"type": "end", "status": s["status"], "result": s["result"]})
                yield f"data: {msg}{NL}"
                break
            time.sleep(0.5)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 7788))
    print(f"\n\U0001f680 Browser-Use Web UI v4 \u2192 http://0.0.0.0:{port}")
    print(f"   \u8d85\u65f6: {os.getenv('TASK_TIMEOUT', 600)}s | \u6700\u5927\u6b65\u6570: {os.getenv('MAX_STEPS', 50)}")
    print(f"   Session \u76ee\u5f55: {SESSION_DIR.absolute()}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
