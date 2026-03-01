"""
AI 内容生成模块

职责：
  1. 分析帖子是否为目标客户（3 票表决，沿用旧版逻辑）
  2. 根据帖子内容生成个性化 Facebook 评论（引导对方回复 WhatsApp）
  3. 根据帖子内容生成个性化 Facebook 私信（引导对方提供 WhatsApp）
  4. 根据帖子内容生成 WhatsApp 首条消息（拿到号码后发送）

所有生成均通过 ZhipuAI GLM 模型完成，使用多 key 轮换。
"""

import json
import logging
import random
import time
from typing import Optional

import requests

from config import (
    ZHIPU_MODEL,
    ZHIPU_KEY_SERVER_URL,
    AI_VOTE_THRESHOLD,
    AI_VOTE_TOTAL,
)

logger = logging.getLogger(__name__)

# ============================================================
# ZhipuAI Key 管理
# ============================================================

_zhipu_keys: list[str] = []
_keys_fetched_at: float = 0
_KEYS_TTL = 300  # key 缓存有效期（秒）


def _get_zhipu_keys() -> list[str]:
    """从 key 服务器拉取 ZhipuAI API Keys，带缓存"""
    global _zhipu_keys, _keys_fetched_at
    if _zhipu_keys and (time.time() - _keys_fetched_at) < _KEYS_TTL:
        return _zhipu_keys
    try:
        resp = requests.get(ZHIPU_KEY_SERVER_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        keys = data if isinstance(data, list) else data.get('keys', [])
        if keys:
            _zhipu_keys      = keys
            _keys_fetched_at = time.time()
            return _zhipu_keys
    except Exception as e:
        logger.warning(f'获取 ZhipuAI keys 失败: {e}')
    return _zhipu_keys or []


def _call_zhipu(messages: list[dict], temperature: float = 0.8) -> Optional[str]:
    """
    调用 ZhipuAI API，自动轮换 key，失败重试。
    返回模型回复文本，失败返回 None。
    """
    keys = _get_zhipu_keys()
    if not keys:
        logger.error('没有可用的 ZhipuAI key')
        return None

    random.shuffle(keys)
    for key in keys:
        try:
            resp = requests.post(
                'https://open.bigmodel.cn/api/paas/v4/chat/completions',
                headers={
                    'Authorization': f'Bearer {key}',
                    'Content-Type':  'application/json',
                },
                json={
                    'model':       ZHIPU_MODEL,
                    'messages':    messages,
                    'temperature': temperature,
                    'max_tokens':  512,
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            logger.warning(f'ZhipuAI key {key[:8]}... 调用失败: {e}')
            time.sleep(2)
            continue

    return None


# ============================================================
# 1. 帖子目标客户分析（3 票表决）
# ============================================================

_ANALYZE_PROMPT = """你是一个 Facebook 帖子分析助手，专门识别 dropshipping 潜在买家。

判断规则：
- 目标客户（回答"是"）：正在寻找 dropshipping 供应商、批发商、货源，或者想开始/扩大 dropshipping 业务的人
- 非目标（回答"否"）：卖家、服务商、招聘者、随机发帖者、广告主

只回答"是"或"否"，不要任何其他内容。

帖子内容：
{content}"""


def analyze_post(content: str) -> bool:
    """
    对单条帖子调用 AI 分析，返回 True 表示是目标客户。
    使用 3 票表决：{AI_VOTE_TOTAL} 票中至少 {AI_VOTE_THRESHOLD} 票为"是"才判定为目标。
    """
    if not content or len(content.strip()) < 10:
        return False

    yes_votes = 0
    for i in range(AI_VOTE_TOTAL):
        messages = [
            {'role': 'user', 'content': _ANALYZE_PROMPT.format(content=content[:1500])}
        ]
        result = _call_zhipu(messages, temperature=0.3)
        if result and '是' in result:
            yes_votes += 1
        # 两票之间稍微等待，避免 rate limit
        if i < AI_VOTE_TOTAL - 1:
            time.sleep(random.uniform(1, 3))

    logger.debug(f'AI 分析结果: {yes_votes}/{AI_VOTE_TOTAL} 票 → {"目标" if yes_votes >= AI_VOTE_THRESHOLD else "非目标"}')
    return yes_votes >= AI_VOTE_THRESHOLD


# ============================================================
# 2. 生成 Facebook 评论（引导对方回复 WhatsApp）
# ============================================================

_COMMENT_PROMPT = """你是一个 dropshipping 供应商的业务员，正在 Facebook 上联系潜在买家。

客户发了这条帖子：
{content}

请写一条简短的 Facebook 评论（20~50 个字），要求：
1. 用英文写（因为是 Facebook 国际群组）
2. 根据客户帖子内容有针对性地回应，证明你读了他的帖子，而不是群发
3. 提到我们是直接工厂/货源，有相关产品
4. 引导对方主动私信我们（不要直接要他的 WhatsApp）
5. 语气友好自然，像真人在发，不夸大，不用感叹号堆砌
6. 只输出评论内容，不要任何说明或引号"""

_COMMENT_PROMPT_ZH = """你是一个 dropshipping 供应商的业务员，正在 Facebook 中文群组联系潜在买家。

客户发了这条帖子：
{content}

请写一条简短的 Facebook 评论（20~50 个字），要求：
1. 用中文写
2. 根据客户帖子内容有针对性地回应
3. 提到我们是直接工厂/货源，有相关产品
4. 引导对方主动私信我们
5. 语气友好自然，像真人在发，不夸大
6. 只输出评论内容，不要任何说明或引号"""


def generate_comment(post_content: str, lang: str = 'en') -> Optional[str]:
    """
    根据帖子内容生成个性化 Facebook 评论。
    lang: 'en'（英文）或 'zh'（中文）
    """
    prompt = _COMMENT_PROMPT_ZH if lang == 'zh' else _COMMENT_PROMPT
    messages = [
        {'role': 'user', 'content': prompt.format(content=post_content[:800])}
    ]
    result = _call_zhipu(messages, temperature=0.9)
    if result:
        # 清理多余引号或说明文字
        result = result.strip().strip('"\'')
        logger.debug(f'生成评论: {result[:80]}...')
    return result


# ============================================================
# 3. 生成 Facebook 私信（索要 WhatsApp）
# ============================================================

_DM_PROMPT = """You are a business development representative for a dropshipping supplier factory.

A potential customer posted this on Facebook:
{content}

Write a short, personalized Facebook direct message (3 sentences max) that:
1. References something specific from their post (show you actually read it, not mass messaging)
2. Briefly explains what we offer that's relevant to their need (be specific, no exaggeration)
3. Naturally asks for their WhatsApp number with a clear reason (e.g., "to send you our product catalog")
4. Tone: friendly, professional, genuine - like a real person, not a bot
5. Do NOT start with "Hi [name]" or generic greetings
6. Do NOT use excessive exclamation marks or ALL CAPS
7. Output ONLY the message text, no explanations, no quotes"""

_DM_PROMPT_ZH = """你是一个 dropshipping 供应商的业务员，正在 Facebook 联系潜在买家。

客户发了这条帖子：
{content}

请写一条 Facebook 私信（最多 3 句话），要求：
1. 针对他帖子里的具体内容回应（证明你是真人，不是群发）
2. 简单说明我们能提供什么（与他需求相关，不夸大）
3. 自然地询问他的 WhatsApp 号码，并给出合理理由（如"方便发产品目录给您"）
4. 语气友好真实，像真人在发，不像机器人
5. 只输出私信内容，不要说明文字，不要引号"""


def generate_dm(post_content: str, lang: str = 'en') -> Optional[str]:
    """
    根据帖子内容生成个性化 Facebook 私信（含 WhatsApp 索取）。
    lang: 'en' 或 'zh'
    """
    prompt = _DM_PROMPT_ZH if lang == 'zh' else _DM_PROMPT
    messages = [
        {'role': 'user', 'content': prompt.format(content=post_content[:800])}
    ]
    result = _call_zhipu(messages, temperature=0.85)
    if result:
        result = result.strip().strip('"\'')
        logger.debug(f'生成私信: {result[:80]}...')
    return result


# ============================================================
# 4. 生成 WhatsApp 首条消息（拿到对方号码后发送）
# ============================================================

_WA_PROMPT = """You are a business development representative for a dropshipping supplier factory.

Context: This customer was found on Facebook and has shared their WhatsApp number.
Their original Facebook post was:
{content}

Write a WhatsApp opening message (2-3 sentences) that:
1. Briefly reminds them how you got their contact ("Connected via your Facebook post about [topic]")
2. Introduces who you are and what you specifically offer (relevant to their post)
3. Ends with an open question or a clear value offer to encourage them to reply
4. Tone: warm, direct, professional - like a real business person
5. Do NOT be pushy or over-promise
6. Output ONLY the message text"""

_WA_PROMPT_ZH = """你是一个 dropshipping 供应商的业务员。

背景：这位客户在 Facebook 发帖子找货源，并提供了 WhatsApp 号码。
他的 Facebook 帖子内容是：
{content}

请写一条 WhatsApp 开场消息（2~3 句话）：
1. 简单说明你是怎么得到他联系方式的（"从您在 Facebook 关于[话题]的帖子"）
2. 介绍你是谁以及能提供什么（与他的需求相关）
3. 以一个开放性问题或明确的价值点结尾，引导他回复
4. 语气亲切、直接、专业，像真人在发
5. 不要夸大承诺
6. 只输出消息内容"""


def generate_whatsapp_message(post_content: str, lang: str = 'en') -> Optional[str]:
    """
    生成发给客户的 WhatsApp 首条消息。
    lang: 'en' 或 'zh'
    """
    prompt = _WA_PROMPT_ZH if lang == 'zh' else _WA_PROMPT
    messages = [
        {'role': 'user', 'content': prompt.format(content=post_content[:800])}
    ]
    result = _call_zhipu(messages, temperature=0.85)
    if result:
        result = result.strip().strip('"\'')
        logger.debug(f'生成 WA 消息: {result[:80]}...')
    return result


# ============================================================
# 5. 从文本中提取 WhatsApp 号码
# ============================================================

_EXTRACT_WA_PROMPT = """Extract any WhatsApp or phone number from the following text.
Rules:
- Look for numbers like "+1234567890", "wa.me/1234", "WhatsApp: 123-456", etc.
- Clean the number to international format if possible (e.g., +8613800138000)
- If multiple numbers found, return only the most likely WhatsApp one
- If no number found, return exactly: NONE

Text:
{text}

Reply with ONLY the phone number (e.g., +8613800138000) or NONE."""


def extract_wa_number(text: str) -> Optional[str]:
    """
    从文本中提取 WhatsApp 号码。
    返回格式化的号码字符串（如 +8613800138000），未找到返回 None。
    """
    if not text:
        return None

    # 先用正则快速匹配，避免每次都调用 AI
    import re
    # 常见模式：+开头的国际号码、wa.me/链接
    patterns = [
        r'wa\.me/(\d{7,15})',
        r'\+\d{7,15}',
        r'\b\d{3}[-.\s]?\d{3,4}[-.\s]?\d{4}\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            number = match.group(0) if not match.lastindex else match.group(1)
            number = re.sub(r'[\s\-.]', '', number)
            if not number.startswith('+') and 'wa.me' not in text:
                pass  # 不加 +，保留原样，让上层处理
            elif 'wa.me' in text:
                number = '+' + number
            return number

    # 正则没找到，用 AI 尝试
    messages = [
        {'role': 'user', 'content': _EXTRACT_WA_PROMPT.format(text=text[:500])}
    ]
    result = _call_zhipu(messages, temperature=0.1)
    if result and result.strip().upper() != 'NONE':
        return result.strip()
    return None
