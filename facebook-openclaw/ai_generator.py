"""
AI 内容生成模块

职责：
  1. 分析帖子是否为目标客户（3 票表决）
  2. 根据帖子内容生成个性化 Facebook 评论（引导对方回复 WhatsApp）
  3. 根据帖子内容生成个性化 Facebook 私信（引导对方提供 WhatsApp）
  4. 根据帖子内容生成 WhatsApp 首条消息（拿到号码后发送）

所有生成均通过 OpenClaw Gateway（Qwen3 模型）完成。
Gateway 地址和 token 由 config.py 提供。
"""

import logging
import random
import time
from typing import Optional

from config import AI_VOTE_THRESHOLD, AI_VOTE_TOTAL
from openclaw_client import get_client

logger = logging.getLogger(__name__)


def _call_ai(messages: list[dict], temperature: float = 0.8, max_tokens: int = 512) -> Optional[str]:
    """
    调用 OpenClaw Gateway（Qwen3）生成内容。
    失败返回 None。
    """
    return get_client().chat(messages, temperature=temperature, max_tokens=max_tokens)


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
    对单条帖子进行 AI 分析，返回 True 表示是目标客户。
    使用 3 票表决：AI_VOTE_TOTAL 票中至少 AI_VOTE_THRESHOLD 票为"是"才判定为目标。
    """
    if not content or len(content.strip()) < 10:
        return False

    yes_votes = 0
    for i in range(AI_VOTE_TOTAL):
        messages = [{'role': 'user', 'content': _ANALYZE_PROMPT.format(content=content[:1500])}]
        result   = _call_ai(messages, temperature=0.3, max_tokens=10)
        if result and '是' in result:
            yes_votes += 1
        if i < AI_VOTE_TOTAL - 1:
            time.sleep(random.uniform(1, 2))

    is_target = yes_votes >= AI_VOTE_THRESHOLD
    logger.debug(f'AI 分析: {yes_votes}/{AI_VOTE_TOTAL} 票 → {"目标" if is_target else "非目标"}')
    return is_target


# ============================================================
# 2. 生成 Facebook 评论（引导对方私信我们）
# ============================================================

_COMMENT_PROMPT_EN = """You are a business development representative for a dropshipping supplier factory.

A potential customer posted this on Facebook:
{content}

Write a short Facebook comment (20-50 words) that:
1. References something specific from their post to show you read it
2. Mentions we are a direct factory/supplier with relevant products
3. Invites them to message us directly (do NOT ask for their WhatsApp)
4. Tone: friendly, genuine, like a real person - no excessive exclamation marks
5. Output ONLY the comment text, no explanations or quotes"""

_COMMENT_PROMPT_ZH = """你是一个 dropshipping 供应商的业务员，正在 Facebook 中文群组联系潜在买家。

客户发了这条帖子：
{content}

请写一条简短的 Facebook 评论（20~50 个字），要求：
1. 针对他帖子里的具体内容回应（证明你读了他的帖子）
2. 提到我们是直接工厂/货源，有相关产品
3. 引导对方主动私信我们（不要直接要他的 WhatsApp）
4. 语气友好自然，像真人在发，不夸大
5. 只输出评论内容，不要说明文字，不要引号"""


def generate_comment(post_content: str, lang: str = 'en') -> Optional[str]:
    """
    根据帖子内容生成个性化 Facebook 评论。
    lang: 'en'（英文）或 'zh'（中文）
    """
    prompt   = _COMMENT_PROMPT_ZH if lang == 'zh' else _COMMENT_PROMPT_EN
    messages = [{'role': 'user', 'content': prompt.format(content=post_content[:800])}]
    result   = _call_ai(messages, temperature=0.9)
    if result:
        result = result.strip().strip('"\'')
        logger.debug(f'生成评论: {result[:80]}')
    return result


# ============================================================
# 3. 生成 Facebook 私信（索要 WhatsApp）
# ============================================================

_DM_PROMPT_EN = """You are a business development representative for a dropshipping supplier factory.

A potential customer posted this on Facebook:
{content}

Write a short, personalized Facebook direct message (3 sentences max) that:
1. References something specific from their post (not a mass message)
2. Briefly explains what we offer relevant to their need
3. Naturally asks for their WhatsApp number with a clear reason (e.g., "to send our product catalog")
4. Tone: friendly, professional, genuine - like a real person
5. Do NOT start with "Hi [name]" or generic greetings
6. Output ONLY the message text, no explanations"""

_DM_PROMPT_ZH = """你是一个 dropshipping 供应商的业务员，正在 Facebook 联系潜在买家。

客户发了这条帖子：
{content}

请写一条 Facebook 私信（最多 3 句话），要求：
1. 针对他帖子里的具体内容回应（证明你是真人，不是群发）
2. 简单说明我们能提供什么（与他需求相关，不夸大）
3. 自然地询问他的 WhatsApp 号码，并给出合理理由
4. 语气友好真实，像真人在发
5. 只输出私信内容，不要说明文字"""


def generate_dm(post_content: str, lang: str = 'en') -> Optional[str]:
    """
    根据帖子内容生成个性化 Facebook 私信（含 WhatsApp 索取）。
    lang: 'en' 或 'zh'
    """
    prompt   = _DM_PROMPT_ZH if lang == 'zh' else _DM_PROMPT_EN
    messages = [{'role': 'user', 'content': prompt.format(content=post_content[:800])}]
    result   = _call_ai(messages, temperature=0.85)
    if result:
        result = result.strip().strip('"\'')
        logger.debug(f'生成私信: {result[:80]}')
    return result


# ============================================================
# 4. 生成 WhatsApp 首条消息
# ============================================================

_WA_PROMPT_EN = """You are a business development representative for a dropshipping supplier factory.

Context: This customer shared their WhatsApp after seeing our Facebook message.
Their original Facebook post was:
{content}

Write a WhatsApp opening message (2-3 sentences) that:
1. Briefly reminds them how you got their contact (via their Facebook post about [topic])
2. Introduces what you specifically offer relevant to their post
3. Ends with an open question or clear value offer to encourage a reply
4. Tone: warm, direct, professional
5. Output ONLY the message text"""

_WA_PROMPT_ZH = """你是一个 dropshipping 供应商的业务员。

背景：这位客户在 Facebook 发帖子找货源，并提供了 WhatsApp 号码。
他的 Facebook 帖子内容是：
{content}

请写一条 WhatsApp 开场消息（2~3 句话）：
1. 简单说明你是怎么得到他联系方式的
2. 介绍你是谁以及能提供什么（与他的需求相关）
3. 以一个开放性问题或明确的价值点结尾，引导他回复
4. 语气亲切、直接、专业
5. 只输出消息内容"""


def generate_whatsapp_message(post_content: str, lang: str = 'en') -> Optional[str]:
    """
    生成发给客户的 WhatsApp 首条消息。
    lang: 'en' 或 'zh'
    """
    prompt   = _WA_PROMPT_ZH if lang == 'zh' else _WA_PROMPT_EN
    messages = [{'role': 'user', 'content': prompt.format(content=post_content[:800])}]
    result   = _call_ai(messages, temperature=0.85)
    if result:
        result = result.strip().strip('"\'')
        logger.debug(f'生成 WA 消息: {result[:80]}')
    return result


# ============================================================
# 5. 从文本中提取 WhatsApp 号码
# ============================================================

def extract_wa_number(text: str) -> Optional[str]:
    """
    从文本中提取 WhatsApp 号码。
    委托给 OpenClawClient.extract_wa_number（正则优先，AI 兜底）。
    """
    return get_client().extract_wa_number(text)
