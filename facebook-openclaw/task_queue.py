"""
任务队列模块

职责：
  1. 当帖子被标记为目标客户时，自动创建 comment + dm 任务（分配给所有可用发送账号）
  2. 当帖子获取到 WhatsApp 号码时，自动创建 whatsapp 任务
  3. 按账号运行发送循环（每个发送账号一个线程）
  4. 遵守频率限制（冷却期 + 每日上限）
  5. 失败任务自动重试（上限 TASK_MAX_RETRY 次）
"""

import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from config import SEND_COOLDOWN, TASK_MAX_RETRY
from models import Account, Post, SendTask, get_session
from fb_sender import (
    execute_comment,
    execute_dm,
    execute_whatsapp,
    can_send,
    record_action,
    update_account_last_task,
    check_and_extract_wa_replies,
)

logger = logging.getLogger(__name__)

# ============================================================
# 任务生成
# ============================================================

def generate_tasks_for_post(post_id: int):
    """
    为一个目标帖子创建发送任务。
    对每个可用的发送账号各创建一条 comment 任务 + 一条 dm 任务。
    已存在的任务不重复创建。
    """
    session = get_session()
    try:
        post = session.query(Post).filter(Post.id == post_id).first()
        if not post or not post.is_target:
            return

        sender_accounts = (
            session.query(Account)
            .filter(
                Account.account_type == 'sender',
                Account.enabled == True,
                Account.status == 'active',
            )
            .all()
        )

        created = 0
        for account in sender_accounts:
            for task_type in ('comment', 'dm'):
                # 去重：同一 post + account + type 只创建一次
                exists = (
                    session.query(SendTask)
                    .filter(
                        SendTask.post_id    == post_id,
                        SendTask.account_id == account.id,
                        SendTask.task_type  == task_type,
                    )
                    .first()
                )
                if not exists:
                    task = SendTask(
                        post_id    = post_id,
                        account_id = account.id,
                        task_type  = task_type,
                    )
                    session.add(task)
                    created += 1

        if created:
            session.commit()
            logger.info(f'为 post_id={post_id} 创建了 {created} 个发送任务')

    except Exception as e:
        session.rollback()
        logger.error(f'生成任务失败 post_id={post_id}: {e}')
    finally:
        session.close()


def generate_whatsapp_task(post_id: int):
    """
    当帖子获取到 WhatsApp 号码后，为所有发送账号创建 whatsapp 任务。
    只选第一个可用发送账号（WhatsApp 消息只需发一次）。
    """
    session = get_session()
    try:
        post = session.query(Post).filter(Post.id == post_id).first()
        if not post or not post.customer_wa:
            return
        if post.wa_messaged:
            return  # 已发过，不重复

        # 已存在 whatsapp 任务则跳过
        exists = (
            session.query(SendTask)
            .filter(SendTask.post_id == post_id, SendTask.task_type == 'whatsapp')
            .first()
        )
        if exists:
            return

        # 取第一个可用发送账号
        account = (
            session.query(Account)
            .filter(
                Account.account_type == 'sender',
                Account.enabled == True,
                Account.status == 'active',
            )
            .first()
        )
        if not account:
            return

        task = SendTask(
            post_id          = post_id,
            account_id       = account.id,
            task_type        = 'whatsapp',
            target_wa_number = post.customer_wa,
        )
        session.add(task)
        session.commit()
        logger.info(f'为 post_id={post_id} 创建 whatsapp 任务 → {post.customer_wa}')

    except Exception as e:
        session.rollback()
        logger.error(f'生成 whatsapp 任务失败: {e}')
    finally:
        session.close()


# ============================================================
# 扫描新目标帖子，自动生成任务
# ============================================================

def scan_and_create_tasks():
    """
    扫描数据库中已标记为目标但尚未生成任务的帖子，创建对应任务。
    由后台线程定期调用。
    """
    session = get_session()
    try:
        # 找到有目标帖子但没有 comment 任务的帖子
        posts_without_tasks = (
            session.query(Post)
            .filter(Post.is_target == True)
            .outerjoin(
                SendTask,
                (SendTask.post_id == Post.id) & (SendTask.task_type == 'comment')
            )
            .filter(SendTask.id == None)
            .limit(50)
            .all()
        )
        post_ids = [p.id for p in posts_without_tasks]

        # 找到已有 WA 号但未发过消息、也没有 whatsapp 任务的帖子
        posts_need_wa = (
            session.query(Post)
            .filter(Post.customer_wa != None, Post.wa_messaged == False)
            .outerjoin(
                SendTask,
                (SendTask.post_id == Post.id) & (SendTask.task_type == 'whatsapp')
            )
            .filter(SendTask.id == None)
            .limit(20)
            .all()
        )
        wa_post_ids = [p.id for p in posts_need_wa]

    finally:
        session.close()

    for post_id in post_ids:
        generate_tasks_for_post(post_id)

    for post_id in wa_post_ids:
        generate_whatsapp_task(post_id)


# ============================================================
# 执行单个任务
# ============================================================

def _execute_task(task_id: int) -> bool:
    """执行一个任务，更新状态，记录日志。返回是否成功。"""
    session = get_session()
    try:
        task    = session.query(SendTask).filter(SendTask.id == task_id).first()
        account = session.query(Account).filter(Account.id == task.account_id).first()
        post    = session.query(Post).filter(Post.id == task.post_id).first()
        if not task or not account or not post:
            return False

        # 标记进行中
        task.status     = 'in_progress'
        task.started_at = datetime.now(timezone.utc)
        session.commit()

    finally:
        session.close()

    # 根据任务类型调用对应发送函数
    try:
        if task.task_type == 'comment':
            success, result_text = execute_comment(task, account, post)
        elif task.task_type == 'dm':
            success, result_text = execute_dm(task, account, post)
        elif task.task_type == 'whatsapp':
            success, result_text = execute_whatsapp(task, account, post)
        else:
            success, result_text = False, f'未知任务类型: {task.task_type}'
    except Exception as e:
        success, result_text = False, str(e)
        logger.exception(f'执行任务 task_id={task_id} 异常: {e}')

    # 更新任务状态
    session = get_session()
    try:
        task = session.query(SendTask).filter(SendTask.id == task_id).first()
        if task:
            if success:
                task.status        = 'completed'
                task.generated_text = result_text
                task.completed_at   = datetime.now(timezone.utc)
                # WhatsApp 任务额外更新
                if task.task_type == 'whatsapp':
                    task.wa_status  = 'sent'
                    task.wa_sent_at = datetime.now(timezone.utc)
                    # 标记帖子已发过 WA 消息
                    post_obj = session.query(Post).filter(Post.id == task.post_id).first()
                    if post_obj:
                        post_obj.wa_messaged = True
            else:
                task.retry_count += 1
                if task.retry_count >= TASK_MAX_RETRY:
                    task.status        = 'failed'
                    task.error_message = result_text
                else:
                    task.status        = 'pending'   # 等待重试
                    task.error_message = result_text
                if task.task_type == 'whatsapp':
                    task.wa_status = 'failed'
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f'更新任务状态失败 task_id={task_id}: {e}')
    finally:
        session.close()

    # 记录执行日志
    record_action(
        post_id     = post.id,
        account_id  = account.id,
        task_id     = task_id,
        action_type = task.task_type,
        success     = success,
        message_text = result_text if success else None,
        error        = result_text if not success else None,
    )

    if success:
        update_account_last_task(account.id)
        logger.info(f'任务完成 task_id={task_id} type={task.task_type} account={account.name}')
    else:
        logger.warning(f'任务失败 task_id={task_id} type={task.task_type}: {result_text}')

    return success


# ============================================================
# 账号级发送循环
# ============================================================

def run_sender_for_account(account_id: int, stop_event=None):
    """
    持续处理指定发送账号的任务队列。
    每执行一个任务后等待冷却期，再取下一个。
    stop_event: threading.Event，设置后退出循环。
    """
    logger.info(f'发送循环启动 account_id={account_id}')

    while not (stop_event and stop_event.is_set()):
        session = get_session()
        try:
            account = session.query(Account).filter(Account.id == account_id).first()
            if not account or not account.enabled:
                logger.info(f'account_id={account_id} 已禁用，停止发送循环')
                return
        finally:
            session.close()

        # 频率检查
        ok, reason = can_send(account)
        if not ok:
            logger.debug(f'account_id={account_id} 暂不可发送: {reason}')
            time.sleep(30)
            continue

        # 取下一个待处理任务
        task = _get_next_task(account_id)
        if not task:
            # 没有待处理任务时，顺便检查一下回复（提取 WA 号）
            check_and_extract_wa_replies(account_id)
            time.sleep(60)
            continue

        _execute_task(task.id)

        # 冷却期（加入随机抖动，防止固定间隔）
        wait = SEND_COOLDOWN + random.randint(-30, 60)
        logger.debug(f'account_id={account_id} 冷却 {wait}s')
        _interruptible_sleep(wait, stop_event)


def _get_next_task(account_id: int) -> Optional[SendTask]:
    """获取该账号下一个 pending 任务（优先 comment > dm > whatsapp）"""
    session = get_session()
    try:
        priority = {'comment': 0, 'dm': 1, 'whatsapp': 2}
        tasks = (
            session.query(SendTask)
            .filter(
                SendTask.account_id == account_id,
                SendTask.status     == 'pending',
            )
            .all()
        )
        if not tasks:
            return None
        # 按优先级排序，同优先级按创建时间
        tasks.sort(key=lambda t: (priority.get(t.task_type, 9), t.created_at or datetime.min))
        return tasks[0]
    finally:
        session.close()


def _interruptible_sleep(seconds: int, stop_event=None):
    """可被 stop_event 打断的 sleep"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if stop_event and stop_event.is_set():
            return
        time.sleep(1)


# ============================================================
# 任务扫描循环（后台线程）
# ============================================================

def run_task_scanner(stop_event=None):
    """
    后台定期扫描数据库，为新目标帖子自动创建任务。
    每 30 秒运行一次。
    """
    logger.info('任务扫描线程启动')
    while not (stop_event and stop_event.is_set()):
        try:
            scan_and_create_tasks()
        except Exception as e:
            logger.error(f'任务扫描异常: {e}')
        _interruptible_sleep(30, stop_event)
