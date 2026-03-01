"""
OpenClaw Gateway 管理器

服务器上只运行一个 openclaw-gateway 进程（端口 18789）。
本模块负责：
  - 提供全局 OpenClawClient 访问入口
  - 定期心跳检测，监控 Gateway 是否在线
  - 将 Gateway 状态暴露给上层（Flask / 任务队列）

注意：不再需要多实例管理。所有 Facebook 账号共用同一个
openclaw-gateway 实例进行 AI 分析和内容生成。
"""

import logging
import threading
import time

from config import OPENCLAW_HEARTBEAT_INTERVAL
from openclaw_client import OpenClawClient, get_client

logger = logging.getLogger(__name__)


class OpenClawManager:
    """
    管理与 openclaw-gateway 的连接状态。

    提供心跳检测线程，确保 Gateway 可用时记录正常，
    不可用时输出告警（不自动重启，Gateway 由系统管理员维护）。
    """

    def __init__(self):
        self._lock             = threading.Lock()
        self._heartbeat_thread = None
        self._running          = False
        self._gateway_online   = False

    # ----------------------------------------------------------
    # 客户端访问
    # ----------------------------------------------------------

    def get_client(self) -> OpenClawClient:
        """返回全局 OpenClawClient 单例"""
        return get_client()

    @property
    def is_online(self) -> bool:
        """Gateway 当前是否在线（基于最近一次心跳结果）"""
        return self._gateway_online

    # ----------------------------------------------------------
    # 心跳检测
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
        logger.info('OpenClaw Gateway 心跳检测线程已启动')

    def stop_heartbeat(self):
        self._running = False

    def _heartbeat_loop(self):
        while self._running:
            try:
                client = get_client()
                online = client.is_alive()
                with self._lock:
                    if online != self._gateway_online:
                        if online:
                            logger.info('OpenClaw Gateway 已恢复在线')
                        else:
                            logger.error(
                                'OpenClaw Gateway 心跳失败！'
                                f'请检查服务器 {client.base_url} 是否正常运行'
                            )
                    self._gateway_online = online
            except Exception as e:
                logger.error(f'心跳检测异常: {e}')
            time.sleep(OPENCLAW_HEARTBEAT_INTERVAL)

    # ----------------------------------------------------------
    # 状态报告
    # ----------------------------------------------------------

    def get_status(self) -> dict:
        """返回 Gateway 当前状态（供 Flask /api/status 使用）"""
        client = get_client()
        return {
            'gateway_url':    client.base_url,
            'gateway_model':  client.model,
            'gateway_online': self._gateway_online,
        }


# 全局单例
manager = OpenClawManager()
