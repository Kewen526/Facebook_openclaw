"""
Facebook 发送模块（OpenClaw 版）

负责执行三类发送任务：
  - comment : 在目标帖子下发 AI 生成的评论（引导私信）
  - dm      : 向帖子作者发 AI 生成的私信（直接索要 WhatsApp）
  - whatsapp: 通过 OpenClaw WhatsApp 集成向已获取号码的客户发首条消息

每次执行前均通过 OpenClaw 重新注入 Cookie，保证 Facebook 登录状态有效。
"""

import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from config import SEND_COOLDOWN, DAILY_SEND_LIMIT, TASK_MAX_RETRY
from models import Account, Post, SendTask, PostAction, get_session
from openclaw_manager import manager as oc_manager
from openclaw_client import OpenClawClient
from ai_generator import generate_comment, generate_dm, generate_whatsapp_message
from fb_monitor import download_cookies

logger = logging.getLogger(__name__)


# ============================================================
# Cookie 注入辅助
# ============================================================

def _inject_cookies_for_account(client: OpenClawClient, account: Account) -> bool:
    """为账号注入 Cookie，返回是否成功"""
    cookies = download_cookies(account.cookie_url)
    if not cookies:
        logger.error(f'account={account.name} 下载 Cookie 失败')
        return False
    return client.inject_cookies(cookies)


# ============================================================
# 执行评论任务
# ============================================================

def execute_comment(task: SendTask, account: Account, post: Post) -> tuple[bool, str]:
    """
    在目标帖子下发评论。
    返回 (success, message_text_or_error)
    """
    client = oc_manager.get_client(account.id)
    if not client:
        return False, '无法获取 OpenClaw 实例'

    if not _inject_cookies_for_account(client, account):
        return False, 'Cookie 注入失败'

    # 检测内容语言（简单判断是否含中文字符）
    lang = 'zh' if _is_chinese(post.content) else 'en'

    # AI 生成评论
    comment_text = generate_comment(post.content or '', lang=lang)
    if not comment_text:
        return False, 'AI 生成评论失败'

    # 执行评论
    success = client.post_comment(post.post_url, comment_text)
    if success:
        logger.info(f'评论成功 post_id={post.post_id} account={account.name}')
        return True, comment_text
    else:
        return False, '评论操作未确认成功'


# ============================================================
# 执行私信任务
# ============================================================

def execute_dm(task: SendTask, account: Account, post: Post) -> tuple[bool, str]:
    """
    向帖子作者发私信（含 WhatsApp 索取）。
    返回 (success, message_text_or_error)
    """
    if not post.author_profile_url:
        return False, '缺少作者主页 URL，无法发送私信'

    client = oc_manager.get_client(account.id)
    if not client:
        return False, '无法获取 OpenClaw 实例'

    if not _inject_cookies_for_account(client, account):
        return False, 'Cookie 注入失败'

    lang = 'zh' if _is_chinese(post.content) else 'en'

    # AI 生成私信
    dm_text = generate_dm(post.content or '', lang=lang)
    if not dm_text:
        return False, 'AI 生成私信失败'

    # 执行私信
    success = client.send_dm(post.author_profile_url, dm_text)
    if success:
        logger.info(f'私信成功 post_id={post.post_id} author={post.author_name} account={account.name}')
        return True, dm_text
    else:
        return False, '私信操作未确认成功'


# ============================================================
# 执行 WhatsApp 任务
# ============================================================

def execute_whatsapp(task: SendTask, account: Account, post: Post) -> tuple[bool, str]:
    """
    通过 OpenClaw WhatsApp 集成向客户发送首条消息。
    task.target_wa_number 必须已填写。
    """
    wa_number = task.target_wa_number
    if not wa_number:
        return False, '任务缺少 target_wa_number'

    client = oc_manager.get_client(account.id)
    if not client:
        return False, '无法获取 OpenClaw 实例'

    lang = 'zh' if _is_chinese(post.content) else 'en'

    # AI 生成 WhatsApp 首条消息
    wa_text = generate_whatsapp_message(post.content or '', lang=lang)
    if not wa_text:
        return False, 'AI 生成 WhatsApp 消息失败'

    # 通过 OpenClaw 的 WhatsApp 集成发送消息
    # 发送到指定号码：指令让 OpenClaw 通过其 WhatsApp 频道发消息
    instruction = (
        f'Send a WhatsApp message to the phone number {wa_number}. '
        f'Message content: {wa_text} '
        f'Use the WhatsApp channel connected to this OpenClaw instance. '
        f'Reply with "SUCCESS" if sent, or "FAILED" with reason.'
    )
    try:
        result = client._post_agent(instruction, timeout=60)
        response_text = result.get('response', '').upper()
        if 'SUCCESS' in response_text:
            logger.info(f'WhatsApp 发送成功 → {wa_number} account={account.name}')
            return True, wa_text
        else:
            return False, f'WhatsApp 发送失败: {result.get("response", "")}'
    except Exception as e:
        return False, f'WhatsApp 请求异常: {e}'


# ============================================================
# 发送后：检查回复并提取 WhatsApp 号码
# ============================================================

def check_and_extract_wa_replies(account_id: int):
    """
    用发送账号的浏览器实例，检查 Facebook 上的评论和私信回复，
    提取其中的 WhatsApp 号码，更新到对应帖子。
    """
    from ai_generator import extract_wa_number

    session = get_session()
    try:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            return
    finally:
        session.close()

    client = oc_manager.get_client(account_id)
    if not client:
        logger.warning(f'account_id={account_id} 无法获取 OpenClaw 实例，跳过回复检查')
        return

    if not _inject_cookies_for_account(client, account):
        return

    # 检查私信回复
    dm_replies = client.check_dm_replies()
    for reply in dm_replies:
        wa_num = reply.get('wa_number') or extract_wa_number(reply.get('message', ''))
        if wa_num:
            _save_wa_to_post_by_author(reply.get('author_id', ''), wa_num, source='dm_reply')

    # 检查最近发送过评论的帖子的回复
    session = get_session()
    try:
        # 找到该账号最近 24h 成功评论过的帖子
        recent_posts = (
            session.query(Post)
            .join(PostAction, PostAction.post_id == Post.id)
            .filter(
                PostAction.account_id == account_id,
                PostAction.action_type == 'comment',
                PostAction.action_status == 'success',
            )
            .limit(20)
            .all()
        )
        post_list = [(p.post_id, p.post_url) for p in recent_posts if p.post_url]
    finally:
        session.close()

    for post_id_str, post_url in post_list:
        comment_replies = client.check_post_replies(post_url)
        for reply in comment_replies:
            wa_num = reply.get('wa_number') or extract_wa_number(reply.get('comment', ''))
            if wa_num:
                _save_wa_to_post(post_id_str, wa_num, source='comment_reply')

        time.sleep(random.uniform(1, 3))


# ============================================================
# 频率限制检查
# ============================================================

def can_send(account: Account) -> tuple[bool, str]:
    """
    检查账号是否可以执行新任务。
    返回 (can_send, reason)
    """
    now = datetime.now(timezone.utc)

    # 检查是否在 rate limit 期间
    if account.rate_limited_until and account.rate_limited_until > now:
        remaining = int((account.rate_limited_until - now).total_seconds() / 60)
        return False, f'账号被 Facebook 限制，剩余 {remaining} 分钟'

    # 检查冷却期
    if account.last_task_at:
        elapsed = (now - account.last_task_at.replace(tzinfo=timezone.utc)).total_seconds()
        if elapsed < SEND_COOLDOWN:
            wait = int(SEND_COOLDOWN - elapsed)
            return False, f'冷却期中，剩余 {wait}s'

    # 检查每日限额（用 PostAction 表统计今日已发数量）
    session = get_session()
    try:
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = (
            session.query(PostAction)
            .filter(
                PostAction.account_id == account.id,
                PostAction.action_type.in_(['comment', 'dm', 'whatsapp']),
                PostAction.action_status == 'success',
                PostAction.executed_at >= today_start,
            )
            .count()
        )
    finally:
        session.close()

    if today_count >= DAILY_SEND_LIMIT:
        return False, f'今日发送已达上限 ({DAILY_SEND_LIMIT})'

    return True, 'ok'


# ============================================================
# 数据库工具
# ============================================================

def _save_wa_to_post(post_id_str: str, wa_number: str, source: str):
    """将提取到的 WhatsApp 号码保存到帖子"""
    session = get_session()
    try:
        post = session.query(Post).filter(Post.post_id == post_id_str).first()
        if post and not post.customer_wa:
            post.customer_wa     = wa_number
            post.wa_source       = source
            post.wa_extracted_at = datetime.now(timezone.utc)
            session.commit()
            logger.info(f'提取到 WhatsApp 号码: {wa_number} ← post_id={post_id_str}')
    except Exception as e:
        session.rollback()
        logger.error(f'保存 WA 号码失败: {e}')
    finally:
        session.close()


def _save_wa_to_post_by_author(author_id: str, wa_number: str, source: str):
    """通过作者 ID 找到帖子，保存 WhatsApp 号码"""
    if not author_id:
        return
    session = get_session()
    try:
        post = (
            session.query(Post)
            .filter(Post.author_id == author_id, Post.customer_wa == None)
            .order_by(Post.created_at.desc())
            .first()
        )
        if post:
            post.customer_wa     = wa_number
            post.wa_source       = source
            post.wa_extracted_at = datetime.now(timezone.utc)
            session.commit()
            logger.info(f'提取到 WhatsApp 号码: {wa_number} ← author_id={author_id}')
    except Exception as e:
        session.rollback()
        logger.error(f'保存 WA 号码失败: {e}')
    finally:
        session.close()


def record_action(post_id: int, account_id: int, task_id: int,
                  action_type: str, success: bool,
                  message_text: str = None, error: str = None):
    """写入 post_actions 执行日志"""
    session = get_session()
    try:
        action = PostAction(
            post_id       = post_id,
            account_id    = account_id,
            task_id       = task_id,
            action_type   = action_type,
            action_status = 'success' if success else 'failed',
            message_text  = message_text,
            error_message = error,
        )
        session.add(action)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f'记录操作日志失败: {e}')
    finally:
        session.close()


def update_account_last_task(account_id: int):
    """更新账号最后任务时间"""
    session = get_session()
    try:
        acc = session.query(Account).filter(Account.id == account_id).first()
        if acc:
            acc.last_task_at = datetime.now(timezone.utc)
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f'更新 last_task_at 失败: {e}')
    finally:
        session.close()


# ============================================================
# 工具函数
# ============================================================

def _is_chinese(text: str) -> bool:
    """简单判断文本是否主要为中文"""
    if not text:
        return False
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return chinese_chars / max(len(text), 1) > 0.2
