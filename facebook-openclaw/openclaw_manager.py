"""
OpenClaw 多实例管理器

负责：
  - 为每个 Facebook 账号分配端口
  - 启动 / 停止 / 重启 OpenClaw 进程
  - 定期心跳检测，自动标记异常实例
  - 提供 OpenClawClient 工厂方法
"""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from config import (
    OPENCLAW_BASE_PORT,
    OPENCLAW_BIN,
    OPENCLAW_DATA_ROOT,
    OPENCLAW_START_TIMEOUT,
    OPENCLAW_HEARTBEAT_INTERVAL,
)
from models import Account, get_session
from openclaw_client import OpenClawClient, wait_for_instance

logger = logging.getLogger(__name__)


class OpenClawManager:
    """
    管理所有 OpenClaw 实例的生命周期。

    每个 Account 对应一个实例：
      port = OPENCLAW_BASE_PORT + (account.id - 1)

    实例信息同步写回 accounts 表（openclaw_port, openclaw_status, openclaw_pid）。
    """

    def __init__(self):
        self._lock              = threading.Lock()
        self._heartbeat_thread  = None
        self._running           = False

    # ----------------------------------------------------------
    # 端口分配
    # ----------------------------------------------------------

    @staticmethod
    def get_port_for_account(account_id: int) -> int:
        """根据账号 ID 计算对应端口（固定映射，不依赖运行时状态）"""
        return OPENCLAW_BASE_PORT + (account_id - 1)

    @staticmethod
    def get_data_dir_for_account(account_id: int) -> str:
        """返回该账号的 OpenClaw 数据目录路径"""
        return os.path.join(OPENCLAW_DATA_ROOT, f'account_{account_id}')

    # ----------------------------------------------------------
    # 启动实例
    # ----------------------------------------------------------

    def start_instance(self, account_id: int) -> bool:
        """
        启动指定账号对应的 OpenClaw 实例。
        如果实例已在运行则直接返回 True。
        """
        port     = self.get_port_for_account(account_id)
        data_dir = self.get_data_dir_for_account(account_id)

        # 先检查是否已经在线
        client = OpenClawClient(port=port)
        if client.is_alive():
            logger.info(f'account_id={account_id} OpenClaw 实例已运行 (port={port})')
            self._update_account_status(account_id, 'running', port=port, data_dir=data_dir)
            return True

        # 创建数据目录
        os.makedirs(data_dir, exist_ok=True)

        # 构建启动命令
        # openclaw start --port <port> --data-dir <dir> --headless
        cmd = [
            OPENCLAW_BIN, 'start',
            '--port',     str(port),
            '--data-dir', data_dir,
            '--headless',          # 无头模式，不显示 UI
        ]

        logger.info(f'启动 OpenClaw 实例 account_id={account_id} port={port}: {" ".join(cmd)}')
        self._update_account_status(account_id, 'starting', port=port, data_dir=data_dir)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # 与父进程解耦，Flask 重启不影响它
            )
        except FileNotFoundError:
            logger.error(f'找不到 openclaw 可执行文件: {OPENCLAW_BIN}，请确认已安装')
            self._update_account_status(account_id, 'error')
            return False
        except Exception as e:
            logger.error(f'启动 OpenClaw 进程失败: {e}')
            self._update_account_status(account_id, 'error')
            return False

        # 等待实例启动完成
        if wait_for_instance(port, timeout=OPENCLAW_START_TIMEOUT):
            self._update_account_status(
                account_id, 'running',
                pid=proc.pid, port=port, data_dir=data_dir
            )
            logger.info(f'OpenClaw 实例已就绪 account_id={account_id} pid={proc.pid} port={port}')
            return True
        else:
            logger.error(f'OpenClaw 实例启动超时 account_id={account_id} port={port}')
            proc.terminate()
            self._update_account_status(account_id, 'error')
            return False

    # ----------------------------------------------------------
    # 停止实例
    # ----------------------------------------------------------

    def stop_instance(self, account_id: int) -> bool:
        """停止指定账号的 OpenClaw 实例"""
        session = get_session()
        try:
            account = session.query(Account).filter(Account.id == account_id).first()
            if not account:
                return False
            pid  = account.openclaw_pid
            port = account.openclaw_port
        finally:
            session.close()

        # 先尝试通过 HTTP API 优雅关闭
        if port:
            client = OpenClawClient(port=port)
            try:
                client._post_agent('shutdown', timeout=10)
            except Exception:
                pass

        # 再尝试 kill PID
        if pid:
            try:
                os.kill(pid, 15)  # SIGTERM
                time.sleep(2)
                os.kill(pid, 9)   # SIGKILL（如果还没退出）
            except ProcessLookupError:
                pass  # 进程已不存在
            except Exception as e:
                logger.warning(f'kill pid={pid} 失败: {e}')

        self._update_account_status(account_id, 'stopped', pid=None)
        logger.info(f'已停止 OpenClaw 实例 account_id={account_id}')
        return True

    # ----------------------------------------------------------
    # 获取客户端
    # ----------------------------------------------------------

    def get_client(self, account_id: int) -> Optional[OpenClawClient]:
        """
        获取指定账号的 OpenClawClient。
        如果实例未运行，先尝试启动。
        """
        port   = self.get_port_for_account(account_id)
        client = OpenClawClient(port=port)

        if client.is_alive():
            return client

        # 尝试启动
        logger.info(f'实例不在线，尝试启动 account_id={account_id}')
        if self.start_instance(account_id):
            return OpenClawClient(port=port)

        return None

    # ----------------------------------------------------------
    # 心跳检测（后台线程）
    # ----------------------------------------------------------

    def start_heartbeat(self):
        """启动后台心跳检测线程"""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._running = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name='openclaw-heartbeat',
            daemon=True,
        )
        self._heartbeat_thread.start()
        logger.info('OpenClaw 心跳检测线程已启动')

    def stop_heartbeat(self):
        self._running = False

    def _heartbeat_loop(self):
        while self._running:
            try:
                self._check_all_instances()
            except Exception as e:
                logger.error(f'心跳检测异常: {e}')
            time.sleep(OPENCLAW_HEARTBEAT_INTERVAL)

    def _check_all_instances(self):
        """检查所有标记为 running 的实例，更新心跳时间或标记为 error"""
        session = get_session()
        try:
            running_accounts = (
                session.query(Account)
                .filter(Account.openclaw_status == 'running', Account.enabled == True)
                .all()
            )
        finally:
            session.close()

        for account in running_accounts:
            port   = account.openclaw_port
            if not port:
                continue
            client = OpenClawClient(port=port)
            if client.is_alive():
                self._update_account_heartbeat(account.id)
            else:
                logger.warning(f'心跳失败 account_id={account.id} port={port}，标记为 error')
                self._update_account_status(account.id, 'error')

    # ----------------------------------------------------------
    # 批量操作
    # ----------------------------------------------------------

    def start_all_enabled(self):
        """启动所有已启用账号的 OpenClaw 实例"""
        session = get_session()
        try:
            accounts = session.query(Account).filter(Account.enabled == True).all()
            account_ids = [a.id for a in accounts]
        finally:
            session.close()

        for account_id in account_ids:
            self.start_instance(account_id)
            time.sleep(1)  # 错开启动，避免同时抢占资源

    def stop_all(self):
        """停止所有实例"""
        session = get_session()
        try:
            accounts = (
                session.query(Account)
                .filter(Account.openclaw_status.in_(['running', 'starting']))
                .all()
            )
            account_ids = [a.id for a in accounts]
        finally:
            session.close()

        for account_id in account_ids:
            self.stop_instance(account_id)

    def get_all_status(self) -> list[dict]:
        """返回所有账号的 OpenClaw 实例状态"""
        session = get_session()
        try:
            accounts = session.query(Account).filter(Account.enabled == True).all()
            return [
                {
                    'account_id':       a.id,
                    'account_name':     a.name,
                    'account_type':     a.account_type,
                    'openclaw_port':    a.openclaw_port,
                    'openclaw_status':  a.openclaw_status,
                    'openclaw_pid':     a.openclaw_pid,
                    'last_heartbeat':   a.last_heartbeat_at.isoformat() if a.last_heartbeat_at else None,
                }
                for a in accounts
            ]
        finally:
            session.close()

    # ----------------------------------------------------------
    # 内部：写回数据库
    # ----------------------------------------------------------

    def _update_account_status(
        self,
        account_id: int,
        status: str,
        pid: Optional[int] = None,
        port: Optional[int] = None,
        data_dir: Optional[str] = None,
    ):
        session = get_session()
        try:
            account = session.query(Account).filter(Account.id == account_id).first()
            if not account:
                return
            account.openclaw_status = status
            if pid is not None:
                account.openclaw_pid = pid
            if port is not None:
                account.openclaw_port = port
            if data_dir is not None:
                account.openclaw_data_dir = data_dir
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f'更新账号状态失败 account_id={account_id}: {e}')
        finally:
            session.close()

    def _update_account_heartbeat(self, account_id: int):
        session = get_session()
        try:
            account = session.query(Account).filter(Account.id == account_id).first()
            if account:
                account.last_heartbeat_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f'更新心跳失败 account_id={account_id}: {e}')
        finally:
            session.close()


# 全局单例
manager = OpenClawManager()
