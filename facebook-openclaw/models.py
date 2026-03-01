from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import (
    create_engine, Column, Integer, SmallInteger, String, Text,
    Boolean, DateTime, Enum, ForeignKey, Index, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from config import DATABASE_URL

Base = declarative_base()


# ============================================================
# 1. 用户表
# ============================================================
class User(Base):
    __tablename__ = 'users'

    id            = Column(Integer,     primary_key=True, autoincrement=True)
    username      = Column(String(128), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role          = Column(Enum('admin', 'manager', 'employee'), nullable=False, default='employee')
    parent_id     = Column(Integer, ForeignKey('users.id'), nullable=True)
    enabled       = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    parent   = relationship('User', remote_side='User.id', backref='children')
    accounts = relationship('Account', back_populates='owner', lazy='dynamic')

    __table_args__ = (
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def set_password(self, password: str):
        self.password_hash = sha256(password.encode()).hexdigest()

    def check_password(self, password: str) -> bool:
        return self.password_hash == sha256(password.encode()).hexdigest()

    def get_team_user_ids(self, session) -> list:
        """返回该用户能看到的所有 user_id 列表（含自身）"""
        if self.role == 'admin':
            return [u.id for u in session.query(User).all()]
        if self.role == 'manager':
            subordinates = session.query(User).filter(User.parent_id == self.id).all()
            return [self.id] + [u.id for u in subordinates]
        return [self.id]

    def to_dict(self):
        return {
            'id':          self.id,
            'username':    self.username,
            'role':        self.role,
            'parent_id':   self.parent_id,
            'parent_name': self.parent.username if self.parent else None,
            'enabled':     self.enabled,
            'created_at':  self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# 2. 账号表（监控账号 + 发送账号）
# ============================================================
class Account(Base):
    __tablename__ = 'accounts'

    id             = Column(Integer,     primary_key=True, autoincrement=True)
    name           = Column(String(256), unique=True, nullable=False)
    account_type   = Column(Enum('monitor', 'sender'), nullable=False)
    cookie_url     = Column(Text, nullable=False)
    cookie_status  = Column(Enum('valid', 'invalid', 'unknown'), default='unknown')
    status         = Column(Enum('active', 'banned', 'paused'),  default='active')
    user_id        = Column(Integer, ForeignKey('users.id'), nullable=True)

    # OpenClaw 实例
    openclaw_port       = Column(Integer,     nullable=True)
    openclaw_status     = Column(Enum('stopped', 'starting', 'running', 'error'), default='stopped')
    openclaw_pid        = Column(Integer,     nullable=True)
    openclaw_data_dir   = Column(String(512), nullable=True)
    last_heartbeat_at   = Column(DateTime,    nullable=True)

    # 频率限制
    last_task_at       = Column(DateTime, nullable=True)
    rate_limited_until = Column(DateTime, nullable=True)

    enabled    = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    owner      = relationship('User',     back_populates='accounts', foreign_keys=[user_id])
    send_tasks = relationship('SendTask', back_populates='account',  lazy='dynamic')

    __table_args__ = (
        Index('idx_type_enabled',  'account_type', 'enabled'),
        Index('idx_openclaw_port', 'openclaw_port'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            'id':                 self.id,
            'name':               self.name,
            'account_type':       self.account_type,
            'cookie_url':         self.cookie_url,
            'cookie_status':      self.cookie_status,
            'status':             self.status,
            'user_id':            self.user_id,
            'owner_name':         self.owner.username if self.owner else None,
            'openclaw_port':      self.openclaw_port,
            'openclaw_status':    self.openclaw_status,
            'last_heartbeat_at':  self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            'last_task_at':       self.last_task_at.isoformat() if self.last_task_at else None,
            'rate_limited_until': self.rate_limited_until.isoformat() if self.rate_limited_until else None,
            'enabled':            self.enabled,
            'created_at':         self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# 3. 帖子表
# ============================================================
class Post(Base):
    __tablename__ = 'posts'

    id                 = Column(Integer,     primary_key=True, autoincrement=True)
    post_id            = Column(String(128), unique=True, nullable=False)   # Facebook 帖子ID
    post_url           = Column(Text,        nullable=True)
    author_name        = Column(String(256), nullable=True)
    author_id          = Column(String(128), nullable=True)
    author_profile_url = Column(Text,        nullable=True)
    content            = Column(Text,        nullable=True)
    content_zh         = Column(Text,        nullable=True)
    image_urls         = Column(Text,        nullable=True)                 # JSON 数组字符串
    post_time          = Column(String(128), nullable=True)
    source_page        = Column(Enum('home', 'groups', 'search'), nullable=True)

    # AI 分析
    ai_result  = Column(Text,        nullable=True)
    ai_votes   = Column(SmallInteger, nullable=False, default=0)            # 0~3
    is_target  = Column(Boolean,     nullable=True)                         # None=待分析

    # 互动状态
    action_interested     = Column(Boolean, default=False)
    action_not_interested = Column(Boolean, default=False)
    action_liked          = Column(Boolean, default=False)

    discovered_by = Column(Integer, ForeignKey('accounts.id'), nullable=True)

    # WhatsApp 收集
    customer_wa      = Column(String(64), nullable=True)
    wa_source        = Column(Enum('comment_reply', 'dm_reply', 'manual'), nullable=True)
    wa_extracted_at  = Column(DateTime,   nullable=True)
    wa_messaged      = Column(Boolean,    default=False)                    # 是否已发过 WA 消息

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    send_tasks = relationship('SendTask',     back_populates='post', lazy='dynamic')
    actions    = relationship('PostAction',   back_populates='post', lazy='dynamic')
    feedbacks  = relationship('PostFeedback', back_populates='post', lazy='dynamic')

    __table_args__ = (
        Index('idx_is_target',    'is_target'),
        Index('idx_discovered',   'discovered_by'),
        Index('idx_created_at',   'created_at'),
        Index('idx_customer_wa',  'customer_wa'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            'id':                    self.id,
            'post_id':               self.post_id,
            'post_url':              self.post_url,
            'author_name':           self.author_name,
            'author_id':             self.author_id,
            'author_profile_url':    self.author_profile_url,
            'content':               self.content,
            'content_zh':            self.content_zh,
            'post_time':             self.post_time,
            'source_page':           self.source_page,
            'ai_votes':              self.ai_votes,
            'is_target':             self.is_target,
            'action_interested':     self.action_interested,
            'action_not_interested': self.action_not_interested,
            'action_liked':          self.action_liked,
            'customer_wa':           self.customer_wa,
            'wa_source':             self.wa_source,
            'wa_messaged':           self.wa_messaged,
            'created_at':            self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# 4. 发送任务表
# ============================================================
class SendTask(Base):
    __tablename__ = 'send_tasks'

    id         = Column(Integer, primary_key=True, autoincrement=True)
    post_id    = Column(Integer, ForeignKey('posts.id'),    nullable=False)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    task_type  = Column(Enum('comment', 'dm', 'whatsapp'),  nullable=False)
    status     = Column(
        Enum('pending', 'in_progress', 'completed', 'failed', 'skipped'),
        default='pending'
    )

    # AI 生成内容
    ai_context     = Column(Text, nullable=True)   # 喂给 AI 的帖子摘要（调试用）
    generated_text = Column(Text, nullable=True)   # 最终发送的消息正文

    # WhatsApp 任务专用
    target_wa_number = Column(String(64), nullable=True)
    wa_status        = Column(Enum('pending', 'sent', 'failed'), nullable=True)
    wa_sent_at       = Column(DateTime, nullable=True)

    # 执行时间
    scheduled_at = Column(DateTime, nullable=True)
    started_at   = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text,   nullable=True)
    retry_count   = Column(SmallInteger, default=0)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    post    = relationship('Post',    back_populates='send_tasks')
    account = relationship('Account', back_populates='send_tasks')

    __table_args__ = (
        Index('idx_status_account', 'status', 'account_id'),
        Index('idx_post_account',   'post_id', 'account_id'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            'id':               self.id,
            'post_id':          self.post_id,
            'account_id':       self.account_id,
            'account_name':     self.account.name if self.account else None,
            'task_type':        self.task_type,
            'status':           self.status,
            'generated_text':   self.generated_text,
            'target_wa_number': self.target_wa_number,
            'wa_status':        self.wa_status,
            'wa_sent_at':       self.wa_sent_at.isoformat() if self.wa_sent_at else None,
            'error_message':    self.error_message,
            'retry_count':      self.retry_count,
            'scheduled_at':     self.scheduled_at.isoformat() if self.scheduled_at else None,
            'started_at':       self.started_at.isoformat() if self.started_at else None,
            'completed_at':     self.completed_at.isoformat() if self.completed_at else None,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# 5. 操作执行日志
# ============================================================
class PostAction(Base):
    __tablename__ = 'post_actions'

    id            = Column(Integer, primary_key=True, autoincrement=True)
    post_id       = Column(Integer, ForeignKey('posts.id'),    nullable=False)
    account_id    = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    task_id       = Column(Integer, ForeignKey('send_tasks.id'), nullable=True)
    action_type   = Column(Enum('comment', 'dm', 'whatsapp', 'add_friend', 'monitor'), nullable=False)
    action_status = Column(Enum('success', 'failed', 'skipped'), nullable=False)
    message_text  = Column(Text,    nullable=True)
    error_message = Column(Text,    nullable=True)
    executed_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    post = relationship('Post', back_populates='actions')

    __table_args__ = (
        Index('idx_action_post',    'post_id'),
        Index('idx_action_account', 'account_id'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            'id':            self.id,
            'post_id':       self.post_id,
            'account_id':    self.account_id,
            'task_id':       self.task_id,
            'action_type':   self.action_type,
            'action_status': self.action_status,
            'message_text':  self.message_text,
            'error_message': self.error_message,
            'executed_at':   self.executed_at.isoformat() if self.executed_at else None,
        }


# ============================================================
# 6. 帖子反馈表（运营人员手动标注）
# ============================================================
class PostFeedback(Base):
    __tablename__ = 'post_feedbacks'

    id               = Column(Integer, primary_key=True, autoincrement=True)
    post_id          = Column(Integer, ForeignKey('posts.id'),  nullable=False)
    user_id          = Column(Integer, ForeignKey('users.id'),  nullable=False)
    is_target_manual = Column(Boolean, nullable=True)
    contact_status   = Column(
        Enum('not_contacted', 'contacted', 'replied', 'converted', 'invalid'),
        default='not_contacted'
    )
    whatsapp_number = Column(String(64), nullable=True)
    notes           = Column(Text,       nullable=True)
    created_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))

    post = relationship('Post', back_populates='feedbacks')
    user = relationship('User')

    __table_args__ = (
        UniqueConstraint('post_id', 'user_id', name='uq_post_user'),
        Index('idx_feedback_user',   'user_id'),
        Index('idx_contact_status',  'contact_status'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            'id':               self.id,
            'post_id':          self.post_id,
            'user_id':          self.user_id,
            'username':         self.user.username if self.user else None,
            'is_target_manual': self.is_target_manual,
            'contact_status':   self.contact_status,
            'whatsapp_number':  self.whatsapp_number,
            'notes':            self.notes,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
            'updated_at':       self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================
# 7. 监控日志表
# ============================================================
class MonitorLog(Base):
    __tablename__ = 'monitor_logs'

    id            = Column(Integer, primary_key=True, autoincrement=True)
    account_id    = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    page_type     = Column(Enum('home', 'groups', 'search'), nullable=False)
    posts_scanned = Column(Integer, default=0)
    posts_new     = Column(Integer, default=0)
    posts_target  = Column(Integer, default=0)
    started_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at   = Column(DateTime, nullable=True)
    status        = Column(Enum('running', 'completed', 'error'), default='running')
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index('idx_log_account',    'account_id'),
        Index('idx_log_started_at', 'started_at'),
        {'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

    def to_dict(self):
        return {
            'id':            self.id,
            'account_id':    self.account_id,
            'page_type':     self.page_type,
            'posts_scanned': self.posts_scanned,
            'posts_new':     self.posts_new,
            'posts_target':  self.posts_target,
            'started_at':    self.started_at.isoformat() if self.started_at else None,
            'finished_at':   self.finished_at.isoformat() if self.finished_at else None,
            'status':        self.status,
            'error_message': self.error_message,
        }


# ============================================================
# 数据库引擎 & 工具函数
# ============================================================
engine       = create_engine(DATABASE_URL, pool_size=5, max_overflow=10, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


def get_session():
    return SessionLocal()


def init_db():
    """建表 + 创建默认 admin 账号"""
    Base.metadata.create_all(engine)
    print('数据库表已就绪')
    _ensure_admin()


def _ensure_admin():
    session = get_session()
    try:
        if not session.query(User).filter(User.username == 'admin').first():
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            session.add(admin)
            session.commit()
            print('已创建默认 admin 账号 (admin / admin123)')
    except Exception as e:
        session.rollback()
        print(f'创建 admin 失败: {e}')
    finally:
        session.close()
