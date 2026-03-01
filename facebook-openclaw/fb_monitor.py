"""
Facebook 监控模块（OpenClaw 版）

流程：
  1. 获取账号 Cookie → 通过 OpenClaw 注入浏览器
  2. 依次访问 MONITOR_PAGES，滚动并抓取帖子
  3. 关键词快速过滤 → AI 3票表决分析
  4. 写入数据库（去重）
  5. 记录监控日志
"""

import logging
import random
import time
from datetime import datetime, timezone

import requests

from config import (
    MONITOR_PAGES,
    SEARCH_KEYWORDS,
    MAX_POSTS_PER_SCAN,
    MONITOR_INTERVAL,
    SCROLL_WAIT_MIN,
    SCROLL_WAIT_MAX,
    INTEREST_PROBABILITY,
    NOT_INTEREST_PROBABILITY,
    LIKE_PROBABILITY,
)
from models import Account, Post, MonitorLog, get_session
from openclaw_manager import manager as oc_manager
from ai_generator import analyze_post

logger = logging.getLogger(__name__)


# ============================================================
# Cookie 工具
# ============================================================

def download_cookies(cookie_url: str) -> list[dict]:
    """从 URL 下载 Cookie JSON 并返回列表"""
    try:
        resp = requests.get(cookie_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # 支持两种格式：直接列表 或 {"cookies": [...]}
        if isinstance(data, list):
            return data
        return data.get('cookies', [])
    except Exception as e:
        logger.error(f'下载 Cookie 失败 url={cookie_url}: {e}')
        return []


# ============================================================
# 关键词快速过滤
# ============================================================

def _is_relevant(content: str) -> bool:
    """粗过滤：帖子是否包含至少一个目标关键词（大小写不敏感）"""
    if not content:
        return False
    lower = content.lower()
    return any(kw in lower for kw in SEARCH_KEYWORDS)


# ============================================================
# 单次页面监控
# ============================================================

def _monitor_one_page(account: Account, page_url: str) -> dict:
    """
    用指定账号监控一个页面。
    返回统计字典 {posts_scanned, posts_new, posts_target, error}
    """
    stats = {'posts_scanned': 0, 'posts_new': 0, 'posts_target': 0, 'error': None}

    # 获取 OpenClaw 客户端
    client = oc_manager.get_client(account.id)
    if not client:
        stats['error'] = f'无法获取 account_id={account.id} 的 OpenClaw 实例'
        return stats

    # 注入 Cookie（登录状态）
    cookies = download_cookies(account.cookie_url)
    if not cookies:
        stats['error'] = '下载 Cookie 失败'
        return stats

    if not client.inject_cookies(cookies):
        stats['error'] = 'Cookie 注入失败，账号可能已失效'
        _mark_cookie_invalid(account.id)
        return stats

    _mark_cookie_valid(account.id)

    # 导航到目标页面
    client.navigate(page_url)
    time.sleep(random.uniform(SCROLL_WAIT_MIN, SCROLL_WAIT_MAX))

    # 计算本次滚动次数（帖子越多滚越深）
    scroll_times = random.randint(4, 7)
    posts = client.scroll_and_collect_posts(scroll_times=scroll_times)

    if not posts:
        logger.info(f'account={account.name} page={page_url} 未抓到帖子')
        return stats

    stats['posts_scanned'] = len(posts)
    logger.info(f'account={account.name} 抓到 {len(posts)} 条帖子 from {page_url}')

    # 按最大数量截断
    posts = posts[:MAX_POSTS_PER_SCAN]

    # 去重 + 分析
    session = get_session()
    try:
        for post_data in posts:
            post_id = str(post_data.get('post_id', '')).strip()
            content = (post_data.get('content') or '').strip()

            if not post_id:
                continue

            # 数据库去重
            if session.query(Post).filter(Post.post_id == post_id).first():
                continue

            stats['posts_new'] += 1

            # 快速关键词过滤（省 AI 调用）
            if not _is_relevant(content):
                _save_post(session, post_data, account.id, page_url, is_target=False, ai_votes=0)
                continue

            # AI 分析（3 票表决）
            is_target = analyze_post(content)
            ai_votes  = 3 if is_target else 0  # 简化记录；精确投票数在 analyze_post 内部
            _save_post(session, post_data, account.id, page_url, is_target=is_target, ai_votes=ai_votes)

            if is_target:
                stats['posts_target'] += 1
                logger.info(f'发现目标帖子 post_id={post_id} author={post_data.get("author_name")}')

                # 点"有兴趣"按钮（概率触发）
                if random.random() < INTEREST_PROBABILITY:
                    client.click_interested(post_data.get('post_url', ''))
                    _update_post_flags(session, post_id, action_interested=True)
            else:
                # 非目标：小概率点"没兴趣"
                if random.random() < NOT_INTEREST_PROBABILITY:
                    client.click_not_interested(post_data.get('post_url', ''))
                    _update_post_flags(session, post_id, action_not_interested=True)

            # 极低概率点赞（所有帖子）
            if random.random() < LIKE_PROBABILITY:
                client.click_like(post_data.get('post_url', ''))
                _update_post_flags(session, post_id, action_liked=True)

    finally:
        session.close()

    return stats


# ============================================================
# 账号级监控循环
# ============================================================

def run_monitor_for_account(account_id: int, stop_event=None):
    """
    持续监控指定账号。
    stop_event: threading.Event，设置后退出循环。
    """
    session = get_session()
    try:
        account = session.query(Account).filter(Account.id == account_id).first()
        if not account:
            logger.error(f'账号 account_id={account_id} 不存在')
            return
        account_name = account.name
        cookie_url   = account.cookie_url
    finally:
        session.close()

    logger.info(f'开始监控账号: {account_name} (id={account_id})')

    while not (stop_event and stop_event.is_set()):
        for page_url in MONITOR_PAGES:
            if stop_event and stop_event.is_set():
                break

            # 确定页面类型
            if 'groups' in page_url:
                page_type = 'groups'
            elif 'search' in page_url:
                page_type = 'search'
            else:
                page_type = 'home'

            # 创建监控日志
            log_id = _create_monitor_log(account_id, page_type)

            try:
                # 重新查询账号（可能已更新状态）
                session = get_session()
                try:
                    account = session.query(Account).filter(Account.id == account_id).first()
                finally:
                    session.close()

                if not account or not account.enabled:
                    logger.info(f'账号 {account_name} 已禁用，停止监控')
                    return

                stats = _monitor_one_page(account, page_url)
                _finish_monitor_log(
                    log_id,
                    posts_scanned=stats['posts_scanned'],
                    posts_new=stats['posts_new'],
                    posts_target=stats['posts_target'],
                    error=stats.get('error'),
                )

                logger.info(
                    f'[{account_name}] {page_type} 本轮: '
                    f'扫描={stats["posts_scanned"]} 新增={stats["posts_new"]} 目标={stats["posts_target"]}'
                )

            except Exception as e:
                logger.exception(f'监控账号 {account_name} 页面 {page_url} 异常: {e}')
                _finish_monitor_log(log_id, error=str(e))

            # 页面间短暂等待
            time.sleep(random.uniform(3, 6))

        # 一轮结束后等待再继续
        if not (stop_event and stop_event.is_set()):
            logger.debug(f'[{account_name}] 等待 {MONITOR_INTERVAL}s 后开始下一轮')
            time.sleep(MONITOR_INTERVAL)


# ============================================================
# 数据库操作工具
# ============================================================

def _save_post(session, post_data: dict, account_id: int, page_url: str,
               is_target: bool, ai_votes: int):
    """将帖子保存到数据库"""
    if 'groups' in page_url:
        source_page = 'groups'
    elif 'search' in page_url:
        source_page = 'search'
    else:
        source_page = 'home'

    post = Post(
        post_id            = str(post_data.get('post_id', '')),
        post_url           = post_data.get('post_url', ''),
        author_name        = post_data.get('author_name', ''),
        author_id          = post_data.get('author_id', ''),
        author_profile_url = post_data.get('author_profile_url', ''),
        content            = post_data.get('content', ''),
        post_time          = post_data.get('post_time', ''),
        source_page        = source_page,
        is_target          = is_target,
        ai_votes           = ai_votes,
        discovered_by      = account_id,
    )
    try:
        session.add(post)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f'保存帖子失败 post_id={post_data.get("post_id")}: {e}')


def _update_post_flags(session, post_id: str, **flags):
    """更新帖子的互动标记"""
    try:
        post = session.query(Post).filter(Post.post_id == post_id).first()
        if post:
            for k, v in flags.items():
                setattr(post, k, v)
            session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f'更新帖子标记失败 post_id={post_id}: {e}')


def _mark_cookie_valid(account_id: int):
    session = get_session()
    try:
        acc = session.query(Account).filter(Account.id == account_id).first()
        if acc and acc.cookie_status != 'valid':
            acc.cookie_status = 'valid'
            session.commit()
    finally:
        session.close()


def _mark_cookie_invalid(account_id: int):
    session = get_session()
    try:
        acc = session.query(Account).filter(Account.id == account_id).first()
        if acc:
            acc.cookie_status = 'invalid'
            session.commit()
    finally:
        session.close()


def _create_monitor_log(account_id: int, page_type: str) -> int:
    session = get_session()
    try:
        log = MonitorLog(account_id=account_id, page_type=page_type, status='running')
        session.add(log)
        session.commit()
        return log.id
    except Exception as e:
        session.rollback()
        logger.error(f'创建监控日志失败: {e}')
        return -1
    finally:
        session.close()


def _finish_monitor_log(log_id: int, posts_scanned: int = 0,
                        posts_new: int = 0, posts_target: int = 0,
                        error: str = None):
    if log_id < 0:
        return
    session = get_session()
    try:
        log = session.query(MonitorLog).filter(MonitorLog.id == log_id).first()
        if log:
            log.posts_scanned = posts_scanned
            log.posts_new     = posts_new
            log.posts_target  = posts_target
            log.finished_at   = datetime.now(timezone.utc)
            log.status        = 'error' if error else 'completed'
            log.error_message = error
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f'更新监控日志失败: {e}')
    finally:
        session.close()
