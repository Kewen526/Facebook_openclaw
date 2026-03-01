"""
OpenClaw Gateway HTTP 客户端

封装与单个 OpenClaw 实例（运行在特定端口）的所有通信。
每个 Facebook 账号对应一个实例，通过不同端口区分。

OpenClaw Gateway HTTP API（基于 Apify/OpenClawBot 文档）：
  POST /agent          发送自然语言指令，让 AI 控制浏览器
  GET  /health         心跳检测
  GET  /config         读取配置
  POST /config         设置配置
"""

import json
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 单次指令最长等待时间（秒）
DEFAULT_TIMEOUT = 120
# 页面操作类指令的超时（浏览器操作慢）
BROWSER_TIMEOUT = 180


class OpenClawError(Exception):
    pass


class OpenClawClient:
    """
    与单个 OpenClaw Gateway 实例通信的客户端。

    用法示例：
        client = OpenClawClient(port=18789)
        if client.is_alive():
            result = client.navigate('https://www.facebook.com/')
    """

    def __init__(self, port: int, token: Optional[str] = None):
        self.base_url = f'http://127.0.0.1:{port}'
        self.port     = port
        self.token    = token  # 如果 Gateway 配置了鉴权 token
        self.session  = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
        if token:
            self.session.headers.update({'Authorization': f'Bearer {token}'})

    # ----------------------------------------------------------
    # 基础通信
    # ----------------------------------------------------------

    def is_alive(self, timeout: int = 5) -> bool:
        """检查实例是否在线"""
        try:
            resp = self.session.get(f'{self.base_url}/health', timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def _post_agent(self, instruction: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
        """
        向 OpenClaw AI Agent 发送自然语言指令。
        返回 Agent 的响应字典。
        """
        payload = {
            'message': instruction,
            'channel': 'webchat',
        }
        try:
            resp = self.session.post(
                f'{self.base_url}/agent',
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            raise OpenClawError(f'[port={self.port}] 指令超时（{timeout}s）: {instruction[:80]}')
        except requests.exceptions.RequestException as e:
            raise OpenClawError(f'[port={self.port}] 请求失败: {e}')

    # ----------------------------------------------------------
    # Cookie 管理（Facebook 登录）
    # ----------------------------------------------------------

    def inject_cookies(self, cookies: list[dict]) -> bool:
        """
        将 Cookie 列表注入浏览器（facebook.com 域名下）。
        cookies 格式：[{"name": "...", "value": "...", "domain": ".facebook.com", ...}, ...]
        """
        cookies_json = json.dumps(cookies)
        instruction = (
            f'Please inject the following cookies into the browser for facebook.com domain, '
            f'then navigate to https://www.facebook.com/ to verify login. '
            f'Cookies JSON: {cookies_json}'
        )
        try:
            result = self._post_agent(instruction, timeout=BROWSER_TIMEOUT)
            response_text = result.get('response', '').lower()
            # 判断注入是否成功：页面出现 Facebook 主界面关键词
            success_signals = ['facebook', 'home', 'feed', 'news', 'logged in', 'welcome']
            return any(s in response_text for s in success_signals)
        except OpenClawError as e:
            logger.error(f'Cookie 注入失败: {e}')
            return False

    # ----------------------------------------------------------
    # 浏览器导航
    # ----------------------------------------------------------

    def navigate(self, url: str) -> dict:
        """导航到指定 URL 并返回页面状态"""
        instruction = f'Navigate to {url} and wait for the page to fully load.'
        return self._post_agent(instruction, timeout=BROWSER_TIMEOUT)

    def scroll_and_collect_posts(self, scroll_times: int = 5) -> list[dict]:
        """
        在当前页面滚动并收集帖子信息。
        返回帖子列表，每个帖子包含：post_id, post_url, author_name, author_id,
        author_profile_url, content, post_time
        """
        instruction = (
            f'Scroll down the Facebook feed {scroll_times} times slowly, waiting 2 seconds between each scroll. '
            f'After scrolling, collect ALL visible Facebook posts on the page. '
            f'For each post extract: '
            f'1) post_id (the numeric ID from the post URL or data attributes), '
            f'2) post_url (full URL to the post), '
            f'3) author_name (display name of the person who posted), '
            f'4) author_id (Facebook user ID from the profile URL), '
            f'5) author_profile_url (full URL to the author profile), '
            f'6) content (full text content of the post), '
            f'7) post_time (when the post was made, as shown on page). '
            f'Return the results as a JSON array of objects with these exact keys. '
            f'Only return the JSON array, no other text.'
        )
        try:
            result = self._post_agent(instruction, timeout=BROWSER_TIMEOUT)
            response_text = result.get('response', '')
            # 尝试从响应中提取 JSON 数组
            posts = _extract_json_array(response_text)
            return posts
        except OpenClawError as e:
            logger.error(f'抓取帖子失败: {e}')
            return []

    # ----------------------------------------------------------
    # 评论操作
    # ----------------------------------------------------------

    def post_comment(self, post_url: str, comment_text: str) -> bool:
        """
        在指定帖子下发表评论。
        返回 True 表示成功，False 表示失败。
        """
        instruction = (
            f'Go to this Facebook post: {post_url} '
            f'Find the comment box, click on it, type the following comment exactly as written, '
            f'then submit it by pressing Enter or clicking the Post button. '
            f'Comment text: {comment_text} '
            f'After submitting, confirm whether the comment was posted successfully. '
            f'Reply with "SUCCESS" if the comment was posted, or "FAILED" with the reason if not.'
        )
        try:
            result = self._post_agent(instruction, timeout=BROWSER_TIMEOUT)
            response_text = result.get('response', '').upper()
            return 'SUCCESS' in response_text
        except OpenClawError as e:
            logger.error(f'发表评论失败 post_url={post_url}: {e}')
            return False

    # ----------------------------------------------------------
    # 私信操作
    # ----------------------------------------------------------

    def send_dm(self, author_profile_url: str, message_text: str) -> bool:
        """
        向指定用户发送私信。
        先访问对方主页，点击 Message 按钮，再发送消息。
        返回 True 表示成功。
        """
        instruction = (
            f'Go to this Facebook profile: {author_profile_url} '
            f'Find and click the "Message" button to open the direct message dialog. '
            f'Type the following message exactly as written, then send it. '
            f'Message: {message_text} '
            f'After sending, confirm whether the message was sent successfully. '
            f'Reply with "SUCCESS" if sent, or "FAILED" with the reason if not.'
        )
        try:
            result = self._post_agent(instruction, timeout=BROWSER_TIMEOUT)
            response_text = result.get('response', '').upper()
            return 'SUCCESS' in response_text
        except OpenClawError as e:
            logger.error(f'发送私信失败 profile={author_profile_url}: {e}')
            return False

    # ----------------------------------------------------------
    # 互动操作（点赞、有兴趣等）
    # ----------------------------------------------------------

    def click_interested(self, post_url: str) -> bool:
        """点击帖子的"有兴趣"按钮"""
        instruction = (
            f'Go to this Facebook post: {post_url} '
            f'Find and click the "Interested" button or reaction. '
            f'Reply with "SUCCESS" if clicked, "FAILED" otherwise.'
        )
        try:
            result = self._post_agent(instruction, timeout=BROWSER_TIMEOUT)
            return 'SUCCESS' in result.get('response', '').upper()
        except OpenClawError:
            return False

    def click_not_interested(self, post_url: str) -> bool:
        """点击帖子的"没有兴趣"按钮"""
        instruction = (
            f'Go to this Facebook post: {post_url} '
            f'Find and click the "Not Interested" or "Hide post" option. '
            f'Reply with "SUCCESS" if clicked, "FAILED" otherwise.'
        )
        try:
            result = self._post_agent(instruction, timeout=BROWSER_TIMEOUT)
            return 'SUCCESS' in result.get('response', '').upper()
        except OpenClawError:
            return False

    # ----------------------------------------------------------
    # 回复检测（从通知/评论中提取 WhatsApp 号码）
    # ----------------------------------------------------------

    def check_post_replies(self, post_url: str) -> list[dict]:
        """
        访问帖子，提取评论中的 WhatsApp 号码或相关回复。
        返回列表：[{"author_name": ..., "author_id": ..., "comment": ..., "wa_number": ...}]
        """
        instruction = (
            f'Go to this Facebook post: {post_url} '
            f'Load all comments (click "View more comments" if present). '
            f'Look for any comments that contain WhatsApp numbers, phone numbers, '
            f'or mentions of WhatsApp (e.g., "wa.me/", "+1234", "WhatsApp: 123"). '
            f'For each relevant comment, extract: '
            f'author_name, author_id (from profile URL if visible), '
            f'the full comment text, and the WhatsApp/phone number found. '
            f'Return results as a JSON array with keys: '
            f'author_name, author_id, comment, wa_number. '
            f'If no WhatsApp numbers found, return an empty array [].'
        )
        try:
            result = self._post_agent(instruction, timeout=BROWSER_TIMEOUT)
            return _extract_json_array(result.get('response', ''))
        except OpenClawError as e:
            logger.error(f'检查帖子回复失败: {e}')
            return []

    def check_dm_replies(self) -> list[dict]:
        """
        访问 Facebook Messenger 收件箱，提取含有 WhatsApp 号码的回复。
        返回列表：[{"author_name": ..., "author_id": ..., "message": ..., "wa_number": ...}]
        """
        instruction = (
            'Go to https://www.facebook.com/messages/ and check recent message conversations. '
            'Look for any messages that contain WhatsApp numbers or phone numbers. '
            'For each relevant conversation extract: '
            'author_name, author_id (from URL if visible), '
            'the message text, and the WhatsApp/phone number found. '
            'Return results as a JSON array with keys: '
            'author_name, author_id, message, wa_number. '
            'If none found, return [].'
        )
        try:
            result = self._post_agent(instruction, timeout=BROWSER_TIMEOUT)
            return _extract_json_array(result.get('response', ''))
        except OpenClawError as e:
            logger.error(f'检查私信回复失败: {e}')
            return []


# ----------------------------------------------------------
# 工具函数
# ----------------------------------------------------------

def _extract_json_array(text: str) -> list:
    """
    从 AI 响应文本中提取 JSON 数组。
    OpenClaw 可能在 JSON 前后加说明文字，这里尝试找到第一个 [ ... ] 块。
    """
    if not text:
        return []
    # 找到第一个 '[' 和最后一个 ']'
    start = text.find('[')
    end   = text.rfind(']')
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        logger.warning(f'JSON 解析失败，原始文本片段: {text[start:start+200]}')
        return []


def wait_for_instance(port: int, timeout: int = 30, interval: int = 2) -> bool:
    """
    等待 OpenClaw 实例启动完成（轮询 /health 端点）。
    timeout: 最长等待秒数
    interval: 每次检查间隔秒数
    """
    client   = OpenClawClient(port=port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.is_alive():
            return True
        time.sleep(interval)
    return False
