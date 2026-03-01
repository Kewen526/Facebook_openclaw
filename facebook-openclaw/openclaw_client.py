"""
OpenClaw Gateway HTTP 客户端

封装与服务器上运行的单一 openclaw-gateway 实例的所有通信。
Gateway 暴露 OpenAI 兼容的 API，使用 Qwen3 模型。

实际部署信息（来自服务器 openclaw.json）：
  端口: 18789（对外）/ 18792（仅本地，内部控制）
  认证: Bearer token（openclaw.json → gateway.auth.token）
  模型: qwen3-max-2026-01-23（alibaba-cloud/qwen3-max-2026-01-23）
  API:  POST /v1/chat/completions（OpenAI 兼容格式）
"""

import json
import logging
import re
import time
from typing import Optional

import requests

from config import OPENCLAW_API_URL, OPENCLAW_AUTH_TOKEN, OPENCLAW_API_MODEL

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60   # 普通 chat 请求超时（秒）
MAX_TOKENS      = 512  # 默认最大输出 token 数


class OpenClawError(Exception):
    pass


class OpenClawClient:
    """
    与 openclaw-gateway 通信的客户端（单实例）。

    用法：
        client = OpenClawClient()
        if client.is_alive():
            reply = client.chat([{"role": "user", "content": "你好"}])
    """

    def __init__(
        self,
        base_url: str = OPENCLAW_API_URL,
        token: str = OPENCLAW_AUTH_TOKEN,
        model: str = OPENCLAW_API_MODEL,
    ):
        self.base_url = base_url.rstrip('/')
        self.model    = model
        self.session  = requests.Session()
        self.session.headers.update({
            'Content-Type':  'application/json',
            'Authorization': f'Bearer {token}',
        })

    # ----------------------------------------------------------
    # 健康检测
    # ----------------------------------------------------------

    def is_alive(self, timeout: int = 5) -> bool:
        """检查 Gateway 是否在线（调用 /v1/models 端点）"""
        try:
            resp = self.session.get(f'{self.base_url}/v1/models', timeout=timeout)
            return resp.status_code in (200, 401)  # 401 说明服务在线但 token 不对
        except Exception:
            return False

    # ----------------------------------------------------------
    # 核心：OpenAI 兼容 Chat Completions
    # ----------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        max_tokens: int = MAX_TOKENS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Optional[str]:
        """
        调用 /v1/chat/completions，返回模型回复文本。
        失败返回 None。

        messages 格式（OpenAI 标准）：
            [{"role": "user", "content": "..."}]
            [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
        """
        payload = {
            'model':       self.model,
            'messages':    messages,
            'temperature': temperature,
            'max_tokens':  max_tokens,
            'stream':      False,
        }
        try:
            resp = self.session.post(
                f'{self.base_url}/v1/chat/completions',
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data['choices'][0]['message']['content'].strip()
        except requests.exceptions.Timeout:
            logger.error(f'OpenClaw 请求超时（{timeout}s）')
            return None
        except requests.exceptions.HTTPError as e:
            logger.error(f'OpenClaw HTTP 错误 {e.response.status_code}: {e.response.text[:200]}')
            return None
        except Exception as e:
            logger.error(f'OpenClaw 请求失败: {e}')
            return None

    # ----------------------------------------------------------
    # 工具函数：提取 JSON 数组
    # ----------------------------------------------------------

    @staticmethod
    def extract_json_array(text: str) -> list:
        """从模型回复中提取第一个 JSON 数组"""
        if not text:
            return []
        start = text.find('[')
        end   = text.rfind(']')
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            logger.warning(f'JSON 解析失败，片段: {text[start:start + 200]}')
            return []

    # ----------------------------------------------------------
    # 从文本中提取 WhatsApp 号码（正则优先，AI 兜底）
    # ----------------------------------------------------------

    def extract_wa_number(self, text: str) -> Optional[str]:
        """从文本中提取 WhatsApp / 电话号码"""
        if not text:
            return None

        patterns = [
            r'wa\.me/(\d{7,15})',
            r'\+\d{7,15}',
            r'\b\d{3}[-.\s]?\d{3,4}[-.\s]?\d{4}\b',
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                number = m.group(1) if m.lastindex else m.group(0)
                number = re.sub(r'[\s\-.]', '', number)
                if 'wa.me' in text and not number.startswith('+'):
                    number = '+' + number
                return number

        # 正则未找到，AI 兜底
        result = self.chat(
            [{'role': 'user', 'content': (
                f'Extract any WhatsApp or phone number from this text. '
                f'Return ONLY the number in international format (e.g. +8613800138000) or NONE.\n\n'
                f'Text: {text[:500]}'
            )}],
            temperature=0.1,
            max_tokens=50,
        )
        if result and result.strip().upper() != 'NONE':
            return result.strip()
        return None


# ----------------------------------------------------------
# 全局单例（整个应用共用一个连接）
# ----------------------------------------------------------

_client: Optional[OpenClawClient] = None


def get_client() -> OpenClawClient:
    """获取全局 OpenClawClient 单例"""
    global _client
    if _client is None:
        _client = OpenClawClient()
    return _client


def wait_for_gateway(timeout: int = 30, interval: int = 2) -> bool:
    """
    等待 openclaw-gateway 启动就绪（轮询 /v1/models）。
    一般在应用启动时调用一次。
    """
    client   = OpenClawClient()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.is_alive():
            logger.info('OpenClaw Gateway 已就绪')
            return True
        logger.debug('等待 OpenClaw Gateway 启动...')
        time.sleep(interval)
    logger.error(f'OpenClaw Gateway 在 {timeout}s 内未响应')
    return False
