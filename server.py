"""
Browser-Use Web UI v4
支持经过筛选的模型：必须同时满足 Tool Calling + OpenAI兼容API
新增：任务取消、超时控制、Cookie/Session 持久化、.env 支持
"""
import asyncio, base64, json, os, re, signal, threading, time
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_from_directory

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 修复 browser-use 0.12.x 浏览器启动超时问题
# 默认 30s 对服务器环境太短，通过环境变量覆盖为 120s
os.environ.setdefault("TIMEOUT_BrowserStartEvent", "120")
os.environ.setdefault("TIMEOUT_BrowserLaunchEvent", "120")
# 增加导航超时（默认 20s 在网络慢的服务器上太短）
os.environ.setdefault("TIMEOUT_NavigateToUrlEvent", "60")

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
                "id": "kimi-k2.5",
                "name": "Kimi K2.5",
                "tags": ["\U0001f9e0 \u6700\u5f3a", "\U0001f441 \u89c6\u89c9", "\u2705 Tool Call"],
                "vision": True,
                "note": "\u6700\u65b0\u591a\u6a21\u6001\u6a21\u578b\uff0c\u652f\u6301\u89c6\u89c9+\u5de5\u5177\u8c03\u7528"
            },
            {
                "id": "kimi-k2-0905-preview",
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
                "id": "glm-4v-plus",
                "name": "GLM-4V Plus",
                "tags": ["\U0001f3c6 \u65d7\u8230", "\U0001f441 \u89c6\u89c9", "\u2705 Tool Call"],
                "vision": True,
                "note": "\u667a\u8c31\u89c6\u89c9\u65d7\u8230\u6a21\u578b\uff0c\u652f\u6301\u622a\u56fe\u7406\u89e3"
            },
            {
                "id": "glm-4-plus",
                "name": "GLM-4 Plus",
                "tags": ["\U0001f3c6 \u65d7\u8230", "\u2705 Tool Call"],
                "vision": False,
                "note": "\u667a\u8c31\u6700\u5f3a\u6a21\u578b\uff0cTool Calling\u5b8c\u6574\u652f\u6301"
            },
            {
                "id": "glm-4v-flash",
                "name": "GLM-4V Flash",
                "tags": ["\u26a1 \u6781\u5feb", "\U0001f441 \u89c6\u89c9", "\U0001f193 \u514d\u8d39"],
                "vision": True,
                "note": "\u514d\u8d39\u89c6\u89c9\u6a21\u578b\uff0c\u652f\u6301\u622a\u56fe\u7406\u89e3"
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

# ── JSON 清洗 + 结构转换：兼容国产模型 (智谱/Kimi/Qwen 等) ──────────
#
# 国产模型 (GLM/Kimi/Qwen) 不完整支持 OpenAI structured output，常见问题：
#   1. JSON 被 ```json ... ``` 或 <output>...</output> 包裹
#   2. 返回多余字段 → Pydantic extra='forbid' 报错
#   3. 缺少必需字段 (action, memory 等)
#   4. 字段类型错误 (current_plan_item: "Step 4" → 应为 int)
#   5. 字段名不一致 (actions → action, eval → evaluation_previous_goal)
#   6. 字段嵌套在 current_state 里而非顶层
#
# 解决方案：拦截原始输出 → 全面结构转换 → 再交给 Pydantic 解析

def _extract_json_str(raw: str) -> str:
    """从 LLM 原始输出中提取 JSON 字符串"""
    s = raw.strip()
    # 1) 去掉 markdown 代码块
    m = re.search(r'```(?:json)?\s*\n?(.*?)```', s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    # 2) 去掉 <output>...</output> 等 XML 包裹
    m2 = re.search(r'<\w+>\s*(.*?)\s*</\w+>', s, re.DOTALL)
    if m2 and m2.group(1).strip().startswith('{'):
        s = m2.group(1).strip()
    # 3) 截取第一个 { 到最后一个 }
    if not s.startswith('{'):
        start = s.find('{')
        end = s.rfind('}')
        if start != -1 and end != -1 and end > start:
            s = s[start:end+1]
    return s


def _transform_for_schema(raw_json: str, output_format) -> str:
    """
    全面转换 JSON 使其符合 output_format (AgentOutput) 的 Pydantic schema。
    处理多余字段、缺失字段、类型错误、字段名映射等所有问题。
    """
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return raw_json  # 无法解析，原样返回让 Pydantic 报错

    if not isinstance(data, dict):
        return raw_json

    # ── 获取 schema 中的合法字段列表 ──
    try:
        schema = output_format.model_json_schema()
        valid_fields = set(schema.get('properties', {}).keys())
    except Exception:
        valid_fields = {
            'thinking', 'evaluation_previous_goal', 'memory',
            'next_goal', 'current_plan_item', 'plan_update', 'action'
        }

    # ── 展平嵌套的 current_state ──
    if 'current_state' in data and isinstance(data['current_state'], dict):
        cs = data.pop('current_state')
        for k, v in cs.items():
            if k not in data:
                data[k] = v

    # ── 字段名映射 (国产模型常用的替代名称) ──
    _FIELD_ALIASES = {
        'eval': 'evaluation_previous_goal',
        'evaluation': 'evaluation_previous_goal',
        'prev_goal_eval': 'evaluation_previous_goal',
        'previous_goal_evaluation': 'evaluation_previous_goal',
        'evaluationPreviousGoal': 'evaluation_previous_goal',
        'goal': 'next_goal',
        'next': 'next_goal',
        'nextGoal': 'next_goal',
        'actions': 'action',
        'plan': 'plan_update',
        'planUpdate': 'plan_update',
        'memo': 'memory',
        'thought': 'thinking',
        'thoughts': 'thinking',
    }
    for alt, canonical in _FIELD_ALIASES.items():
        if alt in data and canonical not in data:
            data[canonical] = data.pop(alt)

    # ── 过滤：只保留 schema 中定义的字段 (防止 extra='forbid' 报错) ──
    cleaned = {}
    for k, v in data.items():
        if k in valid_fields:
            cleaned[k] = v

    # ── 修复 current_plan_item 类型 (string → int | None) ──
    if 'current_plan_item' in cleaned:
        v = cleaned['current_plan_item']
        if isinstance(v, str):
            nums = re.findall(r'\d+', v)
            cleaned['current_plan_item'] = int(nums[0]) if nums else None
        elif v is not None and not isinstance(v, int):
            cleaned['current_plan_item'] = None

    # ── 确保必需字段有默认值 ──
    if 'evaluation_previous_goal' in valid_fields and 'evaluation_previous_goal' not in cleaned:
        cleaned['evaluation_previous_goal'] = 'Unknown - model did not provide evaluation.'
    if 'memory' in valid_fields and 'memory' not in cleaned:
        cleaned['memory'] = 'No memory state provided.'
    if 'next_goal' in valid_fields and 'next_goal' not in cleaned:
        cleaned['next_goal'] = 'Determine the appropriate next action.'

    # ── 确保 action 字段存在且为列表 ──
    if 'action' not in cleaned:
        cleaned['action'] = []
    elif not isinstance(cleaned['action'], list):
        cleaned['action'] = [cleaned['action']]

    # ── 确保 plan_update 为列表 ──
    if 'plan_update' in cleaned and not isinstance(cleaned['plan_update'], list):
        if isinstance(cleaned['plan_update'], str):
            cleaned['plan_update'] = [cleaned['plan_update']]
        else:
            cleaned['plan_update'] = []

    return json.dumps(cleaned, ensure_ascii=False)


# 需要清洗输出的 provider 列表（不完美支持 structured output 的模型）
_PROVIDERS_NEED_CLEANING = {"zhipu", "kimi", "qwen"}


def _make_cleaned_chat_openai(ChatOpenAICls, **kwargs):
    """
    包装 ChatOpenAI，在解析前对 LLM 原始输出做全面结构转换。
    通过 dont_force_structured_output=True 避免模型不支持 json_schema 时报错，
    通过 add_schema_to_system_prompt=True 在提示词中告知模型期望的 JSON 格式，
    然后在 model_validate_json 之前做完整的 JSON 清洗 + 结构转换。
    """
    from browser_use.llm.exceptions import ModelProviderError
    from browser_use.llm.views import ChatInvokeCompletion

    instance = ChatOpenAICls(
        dont_force_structured_output=True,
        add_schema_to_system_prompt=True,
        **kwargs,
    )

    _original_ainvoke = instance.ainvoke

    async def _patched_ainvoke(messages, output_format=None, **kw):
        if output_format is None:
            return await _original_ainvoke(messages, output_format=None, **kw)

        # 用无结构化输出模式调用（获取原始文本）
        result = await _original_ainvoke(messages, output_format=None, **kw)
        raw_content = result.completion

        # 第一步：提取 JSON 字符串（去掉 markdown/XML 包裹）
        extracted = _extract_json_str(raw_content)

        # 第二步：全面结构转换（过滤多余字段、补齐缺失字段、修复类型）
        transformed = _transform_for_schema(extracted, output_format)

        try:
            parsed = output_format.model_validate_json(transformed)
        except Exception as e:
            # 最后的后备：记录详细错误信息以便调试
            import logging
            logging.getLogger('browser_use').error(
                f'JSON transform failed.\n'
                f'  Raw: {raw_content[:500]}\n'
                f'  Transformed: {transformed[:500]}\n'
                f'  Error: {e}'
            )
            raise ModelProviderError(
                message=f'Model output parsing failed after transformation: {e}',
                model=instance.name,
            ) from e

        return ChatInvokeCompletion(
            completion=parsed,
            usage=result.usage,
            stop_reason=result.stop_reason,
        )

    instance.ainvoke = _patched_ainvoke
    return instance


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
        try:
            from browser_use import ChatAnthropic
        except ImportError:
            from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_id, api_key=key, temperature=0)

    else:
        try:
            from browser_use import ChatOpenAI
        except ImportError:
            from langchain_openai import ChatOpenAI
        kwargs = dict(model=model_id, api_key=key, temperature=0)
        if base_url:
            kwargs["base_url"] = base_url
        # 国产模型不完整支持 OpenAI structured output，需要清洗
        if provider in _PROVIDERS_NEED_CLEANING:
            return _make_cleaned_chat_openai(ChatOpenAI, **kwargs)
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
            try:
                # browser-use >= 0.2.x (new API)
                from browser_use import BrowserSession, BrowserProfile
                use_new_api = True
            except ImportError:
                # browser-use < 0.2.x (old API)
                from browser_use.browser import BrowserConfig, Browser
                use_new_api = False

            # 检查是否已取消
            if cancel_event and cancel_event.is_set():
                store.update(status="error", result="\u4efb\u52a1\u5df2\u53d6\u6d88")
                log("\u26d4 \u4efb\u52a1\u5df2\u53d6\u6d88", "error")
                return

            log("🔧 [DEBUG] 开始构建 LLM...")
            llm     = build_llm(cfg)
            log("🔧 [DEBUG] LLM 构建成功")

            chrome_process = None  # 追踪手动启动的 Chrome 进程

            if use_new_api:
                # ── 终极方案：手动 subprocess 启动 Chromium + CDP ──
                # browser-use 自己的子进程启动在服务器上会卡死，
                # 所以我们手动启动 Chromium 并通过 CDP URL 连接。
                import shutil, subprocess, socket, time as _time

                # 找到 Chromium 二进制
                browser_binary = os.environ.get("BROWSER_BINARY_PATH")
                if not browser_binary:
                    for name in ("chromium-browser", "chromium", "google-chrome-stable", "google-chrome"):
                        path = shutil.which(name)
                        if path:
                            browser_binary = path
                            break
                if not browser_binary:
                    # 用 Playwright 自带的 Chromium
                    try:
                        from playwright.sync_api import sync_playwright
                        with sync_playwright() as p:
                            browser_binary = p.chromium.executable_path
                    except Exception:
                        pass
                log(f"🔧 浏览器路径: {browser_binary or '未找到'}")

                # 选择一个空闲端口
                cdp_port = 9222
                for port_candidate in range(9222, 9232):
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        if s.connect_ex(("127.0.0.1", port_candidate)) != 0:
                            cdp_port = port_candidate
                            break
                log(f"🔧 CDP 端口: {cdp_port}")

                # 启动 Chromium 子进程
                # 代理配置：优先用环境变量 BROWSER_PROXY，默认用本地 mihomo
                proxy_url = os.environ.get("BROWSER_PROXY", "socks5://127.0.0.1:7898")

                chrome_args = [
                    browser_binary,
                    f"--remote-debugging-port={cdp_port}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-sync",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-features=VizDisplayCompositor",
                ]
                if proxy_url:
                    chrome_args.append(f"--proxy-server={proxy_url}")
                    # 强制 DNS 也走代理，避免国内 DNS 解析不了 facebook 等域名
                    chrome_args.append("--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1")
                    log(f"🔧 代理: {proxy_url} (DNS 也走代理)")
                if headless:
                    chrome_args.append("--headless=new")
                chrome_args.append("about:blank")

                log(f"🔧 启动 Chromium: {' '.join(chrome_args[:5])}...")
                chrome_process = subprocess.Popen(
                    chrome_args,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )

                # 等待 CDP 端口就绪（最多 30 秒）
                cdp_ready = False
                for i in range(60):
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        if s.connect_ex(("127.0.0.1", cdp_port)) == 0:
                            cdp_ready = True
                            break
                    # 检查进程是否已退出
                    if chrome_process.poll() is not None:
                        stderr_out = chrome_process.stderr.read().decode(errors="replace")
                        raise RuntimeError(f"Chromium 启动失败 (exit {chrome_process.returncode}): {stderr_out[:500]}")
                    _time.sleep(0.5)

                if not cdp_ready:
                    stderr_out = chrome_process.stderr.read(2048).decode(errors="replace") if chrome_process.stderr else ""
                    chrome_process.kill()
                    raise RuntimeError(f"Chromium CDP 端口 {cdp_port} 在 30 秒内未就绪。stderr: {stderr_out[:500]}")

                cdp_url = f"http://127.0.0.1:{cdp_port}"
                log(f"🔧 Chromium CDP 已就绪: {cdp_url}")

                browser_profile = BrowserProfile(
                    headless=headless,
                    enable_default_extensions=False,
                )
                browser = BrowserSession(
                    cdp_url=cdp_url,
                    browser_profile=browser_profile,
                )
                log("🌐 浏览器已通过 CDP 连接")
            else:
                # browser-use < 0.2.x: 使用 BrowserConfig + Browser
                from browser_use.browser import BrowserConfig, Browser as BUBrowser
                import shutil
                browser_binary = os.environ.get("BROWSER_BINARY_PATH")
                if not browser_binary:
                    for name in ("chromium-browser", "chromium", "google-chrome-stable", "google-chrome"):
                        path = shutil.which(name)
                        if path:
                            browser_binary = path
                            break
                cfg_kwargs = {"headless": headless}
                disable_ext = os.getenv("DISABLE_DEFAULT_EXTENSIONS", "true").lower() != "false"
                if disable_ext:
                    cfg_kwargs["enable_default_extensions"] = False
                browser_cfg = BrowserConfig(**cfg_kwargs)
                if browser_binary:
                    if hasattr(browser_cfg, "browser_binary_path"):
                        browser_cfg.browser_binary_path = browser_binary
                    elif hasattr(browser_cfg, "chrome_instance_path"):
                        browser_cfg.chrome_instance_path = browser_binary
                browser = BUBrowser(config=browser_cfg)
                log("\U0001f310 \u6d4f\u89c8\u5668\u5df2\u542f\u52a8...")

            # 截图后台任务
            screenshot_on = True
            async def capture():
                while screenshot_on:
                    if cancel_event and cancel_event.is_set():
                        break
                    try:
                        if use_new_api:
                            # 新 API: 通过 BrowserSession 获取截图
                            if hasattr(browser, 'get_screenshot'):
                                img = await browser.get_screenshot()
                                if img:
                                    store["screenshot"] = base64.b64encode(img).decode()
                        else:
                            pb = browser.playwright_browser
                            if pb and pb.contexts:
                                pages = pb.contexts[0].pages
                                if pages:
                                    img = await pages[-1].screenshot(type="jpeg", quality=55)
                                    store["screenshot"] = base64.b64encode(img).decode()
                    except Exception:
                        pass
                    await asyncio.sleep(0.8)

            # 检测模型是否支持视觉
            model_vision = True  # 默认启用
            provider_key = cfg.get("provider", "")
            model_id_cfg = cfg.get("model", "")
            pinfo = PROVIDERS.get(provider_key, {})
            for m in pinfo.get("models", []):
                if m["id"] == model_id_cfg:
                    model_vision = m.get("vision", True)
                    break
            if not model_vision:
                log(f"⚠️ 模型 {model_id_cfg} 不支持视觉，已禁用截图分析")

            log("🔧 [DEBUG] 创建 Agent 对象...")
            agent_kwargs = dict(task=task_text, llm=llm, use_vision=model_vision)
            if use_new_api:
                agent_kwargs["browser_session"] = browser
            else:
                agent_kwargs["browser"] = browser
            agent = Agent(**agent_kwargs)
            log("🔧 [DEBUG] Agent 创建成功")
            cap_t   = asyncio.create_task(capture())
            log("\u2699\ufe0f  AI \u5f00\u59cb\u5206\u6790\u4efb\u52a1...")
            log(f"🔧 [DEBUG] 开始 agent.run(max_steps={max_steps}), timeout={task_timeout}s")

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

            # 任务完成后保存 cookies (仅旧 API 支持)
            if not use_new_api:
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

            # browser-use 0.12.x: BrowserSession 用 stop() 而非 close()
            if hasattr(browser, 'stop'):
                await browser.stop()
            elif hasattr(browser, 'close'):
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
            import traceback
            tb = traceback.format_exc()
            store.update(status="error", result=str(e))
            log(f"\u274c {e}", "error")
            log(f"🔧 [DEBUG] 完整错误堆栈:\n{tb}", "error")
        finally:
            if browser:
                try:
                    if hasattr(browser, 'stop'):
                        await browser.stop()
                    elif hasattr(browser, 'close'):
                        await browser.close()
                except Exception:
                    pass
            # 杀掉手动启动的 Chrome 子进程
            try:
                if chrome_process and chrome_process.poll() is None:
                    chrome_process.terminate()
                    chrome_process.wait(timeout=5)
            except Exception:
                try:
                    chrome_process.kill()
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
