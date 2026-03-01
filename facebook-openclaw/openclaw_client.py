"""
OpenClaw Gateway HTTP 客户端

封装与 openclaw-gateway 的所有通信。
Gateway 暴露两个端点：
  POST /v1/chat/completions  —— 文本生成（AI 分析、话术生成）
  POST /v1/responses         —— Agent 任务（浏览器控制、多步操作）

服务器实际配置（来自 openclaw.json）：
  URL:   http://127.0.0.1:18789
  Token: 15386f2dfc54fc186314846c80f35922
  Model: qwen3-max-2026-01-23
"""

import json
import logging
import re
import time
from typing import Optional

import requests

from config import OPENCLAW_API_URL, OPENCLAW_AUTH_TOKEN, OPENCLAW_API_MODEL

logger = logging.getLogger(__name__)

CHAT_TIMEOUT  = 60   # 文本生成请求超时（秒）
AGENT_TIMEOUT = 180  # 浏览器 Agent 任务超时（秒）
MAX_TOKENS    = 512


class OpenClawError(Exception):
    pass


class OpenClawClient:
    """
    与 openclaw-gateway 通信的客户端。

    两种调用模式：
      1. chat()       ——调用 /v1/chat/completions，纯文本生成
      2. _run_agent() ——调用 /v1/responses，让 AI Agent 执行多步骤任务
                        （浏览器导航、Cookie 注入、点击、抓取等）
    """

    def __init__(
        self,
        base_url: str = OPENCLAW_API_URL,
        token: str    = OPENCLAW_AUTH_TOKEN,
        model: str    = OPENCLAW_API_MODEL,
    ):
        self.base_url = base_url.rstrip('/')
        self.model    = model
        self.session  = requests.Session()
        self.session.headers.update({
            'Content-Type':  'application/json',
            'Authorization': f'Bearer {token}',
        })

    # =========================================================
    # 健康检测
    # =========================================================

    def is_alive(self, timeout: int = 5) -> bool:
        """检查 Gateway 是否在线"""
        try:
            r = self.session.get(f'{self.base_url}/v1/models', timeout=timeout)
            return r.status_code in (200, 401)
        except Exception:
            return False

    # =========================================================
    # 文本生成  /v1/chat/completions
    # =========================================================

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int    = MAX_TOKENS,
        timeout: int       = CHAT_TIMEOUT,
    ) -> Optional[str]:
        """
        调用 chat completions 端点，返回模型回复文本。
        用于：帖子分析、评论/私信/WA 消息生成。
        """
        try:
            r = self.session.post(
                f'{self.base_url}/v1/chat/completions',
                json={
                    'model':       self.model,
                    'messages':    messages,
                    'temperature': temperature,
                    'max_tokens':  max_tokens,
                    'stream':      False,
                },
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()['choices'][0]['message']['content'].strip()
        except requests.exceptions.Timeout:
            logger.error('chat completions 超时')
            return None
        except Exception as e:
            logger.error(f'chat completions 失败: {e}')
            return None

    # =========================================================
    # Agent 任务  /v1/responses（OpenAI Responses API）
    # =========================================================

    def _run_agent(self, instruction: str, timeout: int = AGENT_TIMEOUT) -> str:
        """
        通过 /v1/responses 让 OpenClaw AI Agent 执行多步任务。
        Agent 有 browser_use / computer_use / bash 等内置技能（Skills），
        会根据指令自动选择工具完成任务，返回执行结果文本。
        """
        payload = {
            'model':  self.model,
            'input':  [{'role': 'user', 'content': instruction}],
            'stream': False,
        }
        try:
            r = self.session.post(
                f'{self.base_url}/v1/responses',
                json=payload,
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
            # 兼容 OpenAI Responses API 和 Chat Completions 格式
            if 'output' in data:
                # Responses API: output 是 list[{type, content}]
                for item in data.get('output', []):
                    if item.get('type') == 'message':
                        for part in item.get('content', []):
                            if part.get('type') == 'output_text':
                                return part.get('text', '')
            if 'choices' in data:
                # fallback: chat completions 格式
                return data['choices'][0]['message']['content'].strip()
            return str(data)
        except requests.exceptions.Timeout:
            logger.error(f'Agent 任务超时（{timeout}s）: {instruction[:80]}')
            return ''
        except Exception as e:
            logger.error(f'Agent 任务失败: {e}  |  指令: {instruction[:80]}')
            return ''

    # =========================================================
    # Facebook 浏览器操作（通过 Agent Skills 实现）
    # =========================================================

    def inject_cookies(self, cookies: list[dict]) -> bool:
        """
        注入 Facebook Cookie，登录状态验证。
        Agent 使用 browser_use 技能执行。
        """
        cookies_json = json.dumps(cookies)
        result = self._run_agent(
            f'Use browser automation to inject the following Facebook cookies and verify login.\n'
            f'Steps:\n'
            f'1. Open browser and go to https://www.facebook.com/\n'
            f'2. Inject all cookies from this JSON list: {cookies_json}\n'
            f'3. Reload the page\n'
            f'4. Check if user is logged in (home feed visible, no login form)\n'
            f'Reply with exactly "SUCCESS" if logged in, "FAILED <reason>" if not.'
        )
        ok = 'SUCCESS' in result.upper()
        if not ok:
            logger.warning(f'Cookie 注入失败: {result[:120]}')
        return ok

    def navigate(self, url: str) -> dict:
        """导航到指定 URL，等待页面加载完成"""
        result = self._run_agent(
            f'Use browser automation:\n'
            f'1. Navigate to: {url}\n'
            f'2. Wait for the page to fully load\n'
            f'3. Return the current page title and URL'
        )
        return {'response': result, 'url': url}

    def scroll_and_collect_posts(self, scroll_times: int = 5) -> list[dict]:
        """
        在当前 Facebook 页面滚动并抓取帖子列表。
        返回列表，每项包含：post_id, post_url, author_name, author_id,
        author_profile_url, content, post_time
        """
        result = self._run_agent(
            f'Use browser automation on the current Facebook page:\n'
            f'1. Scroll down {scroll_times} times slowly, wait 2 seconds between scrolls\n'
            f'2. Collect ALL visible posts with these fields for each:\n'
            f'   - post_id: numeric ID from post URL\n'
            f'   - post_url: full URL to the post\n'
            f'   - author_name: display name of poster\n'
            f'   - author_id: Facebook user ID\n'
            f'   - author_profile_url: full URL to poster profile\n'
            f'   - content: full text of the post\n'
            f'   - post_time: when posted (as shown on page)\n'
            f'3. Return ONLY a JSON array with the above fields, no extra text.'
        )
        posts = _extract_json_array(result)
        logger.info(f'抓取到 {len(posts)} 条帖子')
        return posts

    def post_comment(self, post_url: str, comment_text: str) -> bool:
        """在指定帖子下发表评论"""
        result = self._run_agent(
            f'Use browser automation:\n'
            f'1. Navigate to: {post_url}\n'
            f'2. Find the comment input box and click on it\n'
            f'3. Type this comment exactly: {comment_text}\n'
            f'4. Submit by pressing Enter or clicking Post button\n'
            f'5. Confirm the comment appeared on the page\n'
            f'Reply with "SUCCESS" if posted, "FAILED <reason>" if not.'
        )
        ok = 'SUCCESS' in result.upper()
        if not ok:
            logger.warning(f'发表评论失败 post={post_url}: {result[:100]}')
        return ok

    def send_dm(self, author_profile_url: str, message_text: str) -> bool:
        """向指定用户发送 Facebook 私信"""
        result = self._run_agent(
            f'Use browser automation:\n'
            f'1. Navigate to profile: {author_profile_url}\n'
            f'2. Click the "Message" button to open direct message\n'
            f'3. Type this message exactly: {message_text}\n'
            f'4. Send the message\n'
            f'5. Confirm message was sent\n'
            f'Reply with "SUCCESS" if sent, "FAILED <reason>" if not.'
        )
        ok = 'SUCCESS' in result.upper()
        if not ok:
            logger.warning(f'发送私信失败 profile={author_profile_url}: {result[:100]}')
        return ok

    def click_interested(self, post_url: str) -> bool:
        """点击帖子的"有兴趣"按钮"""
        result = self._run_agent(
            f'Use browser automation:\n'
            f'1. Navigate to: {post_url}\n'
            f'2. Find and click the "Interested" button or reaction\n'
            f'Reply with "SUCCESS" if clicked, "FAILED <reason>" if not.'
        )
        return 'SUCCESS' in result.upper()

    def click_not_interested(self, post_url: str) -> bool:
        """点击帖子的"没有兴趣"或"隐藏帖子"按钮"""
        result = self._run_agent(
            f'Use browser automation:\n'
            f'1. Navigate to: {post_url}\n'
            f'2. Find and click "Not Interested" or "Hide post"\n'
            f'Reply with "SUCCESS" if clicked, "FAILED <reason>" if not.'
        )
        return 'SUCCESS' in result.upper()

    def check_post_replies(self, post_url: str) -> list[dict]:
        """
        抓取帖子评论，提取含 WhatsApp 号码的评论。
        返回：[{author_name, author_id, comment, wa_number}]
        """
        result = self._run_agent(
            f'Use browser automation:\n'
            f'1. Navigate to: {post_url}\n'
            f'2. Load all comments (click "View more" if visible)\n'
            f'3. Find comments containing WhatsApp numbers, phone numbers,\n'
            f'   or "wa.me/" links\n'
            f'4. For each matching comment extract:\n'
            f'   - author_name, author_id, comment (full text), wa_number\n'
            f'5. Return ONLY a JSON array with those fields. If none found, return []'
        )
        return _extract_json_array(result)

    def check_dm_replies(self) -> list[dict]:
        """
        检查 Messenger 收件箱，提取含 WhatsApp 号码的消息。
        返回：[{author_name, author_id, message, wa_number}]
        """
        result = self._run_agent(
            f'Use browser automation:\n'
            f'1. Navigate to https://www.facebook.com/messages/\n'
            f'2. Check recent conversations for messages containing\n'
            f'   WhatsApp numbers or phone numbers\n'
            f'3. For each matching message extract:\n'
            f'   - author_name, author_id, message (text), wa_number\n'
            f'4. Return ONLY a JSON array with those fields. If none found, return []'
        )
        return _extract_json_array(result)

    # =========================================================
    # 兼容性接口（供 fb_sender.py 的 WhatsApp 任务调用）
    # =========================================================

    def _post_agent(self, instruction: str, timeout: int = AGENT_TIMEOUT) -> dict:
        """
        兼容旧接口：调用 Agent 并返回 {'response': str} 格式的字典。
        fb_sender.py 的 WhatsApp 发送任务使用此接口。
        """
        text = self._run_agent(instruction, timeout=timeout)
        return {'response': text}

    # =========================================================
    # 工具方法
    # =========================================================

    @staticmethod
    def extract_json_array(text: str) -> list:
        return _extract_json_array(text)

    def extract_wa_number(self, text: str) -> Optional[str]:
        """从文本中提取 WhatsApp 号码（正则优先，AI 兜底）"""
        if not text:
            return None
        for pattern in [r'wa\.me/(\d{7,15})', r'\+\d{7,15}', r'\b\d{3}[-.\s]?\d{3,4}[-.\s]?\d{4}\b']:
            m = re.search(pattern, text)
            if m:
                number = m.group(1) if m.lastindex else m.group(0)
                number = re.sub(r'[\s\-.]', '', number)
                if 'wa.me' in text and not number.startswith('+'):
                    number = '+' + number
                return number
        result = self.chat(
            [{'role': 'user', 'content':
              f'Extract any WhatsApp or phone number. Return ONLY the number in international '
              f'format (e.g. +8613800138000) or NONE.\n\nText: {text[:500]}'}],
            temperature=0.1, max_tokens=50,
        )
        if result and result.strip().upper() != 'NONE':
            return result.strip()
        return None


# =========================================================
# 工具函数
# =========================================================

def _extract_json_array(text: str) -> list:
    """从 AI 响应中提取第一个 JSON 数组"""
    if not text:
        return []
    start = text.find('[')
    end   = text.rfind(']')
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        logger.warning(f'JSON 解析失败，片段: {text[start:start+200]}')
        return []


# =========================================================
# 全局单例
# =========================================================

_client: Optional[OpenClawClient] = None


def get_client() -> OpenClawClient:
    global _client
    if _client is None:
        _client = OpenClawClient()
    return _client


def wait_for_gateway(timeout: int = 30, interval: int = 2) -> bool:
    """等待 openclaw-gateway 就绪"""
    client   = OpenClawClient()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.is_alive():
            logger.info('OpenClaw Gateway 已就绪')
            return True
        logger.debug('等待 OpenClaw Gateway 启动...')
        time.sleep(interval)
    logger.error(f'OpenClaw Gateway {timeout}s 内未响应')
    return False
