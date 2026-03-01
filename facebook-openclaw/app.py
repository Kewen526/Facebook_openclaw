"""
Flask Web 管理后台

API 端点：
  认证     POST /api/auth/login  POST /api/auth/logout  GET /api/auth/me
  用户管理  GET/POST /api/users   PUT/DELETE /api/users/<id>
  账号管理  GET/POST /api/accounts  PUT/DELETE /api/accounts/<id>
  OpenClaw GET /api/openclaw/status  POST /api/openclaw/<id>/start  POST /api/openclaw/<id>/stop
  监控控制  POST /api/monitor/start  POST /api/monitor/stop  GET /api/monitor/status
  帖子     GET /api/posts  GET /api/posts/<id>  POST /api/posts/<id>/feedback
  任务     GET /api/tasks
  统计     GET /api/stats
"""

import logging
import threading
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify, request, session, render_template

from config import FLASK_PORT, FLASK_SECRET
from models import (
    Account, Post, SendTask, PostFeedback, PostAction, MonitorLog,
    User, get_session, init_db,
)
from openclaw_manager import manager as oc_manager

app = Flask(__name__)
app.secret_key = FLASK_SECRET

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
)
logger = logging.getLogger(__name__)

# 运行中的线程
_monitor_threads: dict[int, threading.Thread] = {}
_sender_threads:  dict[int, threading.Thread] = {}
_scanner_thread: threading.Thread | None = None
_stop_events:     dict[int, threading.Event] = {}
_scanner_stop:    threading.Event | None = None


# ============================================================
# 认证装饰器
# ============================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': '请先登录'}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': '请先登录'}), 401
        db = get_session()
        try:
            user = db.query(User).filter(User.id == session['user_id']).first()
            if not user or user.role != 'admin':
                return jsonify({'error': '需要管理员权限'}), 403
        finally:
            db.close()
        return f(*args, **kwargs)
    return decorated


def _current_user(db) -> User | None:
    return db.query(User).filter(User.id == session.get('user_id')).first()


# ============================================================
# 认证
# ============================================================

@app.post('/api/auth/login')
def login():
    data = request.json or {}
    db = get_session()
    try:
        user = db.query(User).filter(User.username == data.get('username')).first()
        if not user or not user.check_password(data.get('password', '')) or not user.enabled:
            return jsonify({'error': '用户名或密码错误'}), 401
        session['user_id'] = user.id
        return jsonify({'user': user.to_dict()})
    finally:
        db.close()


@app.post('/api/auth/logout')
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.get('/api/auth/me')
@login_required
def me():
    db = get_session()
    try:
        user = _current_user(db)
        return jsonify({'user': user.to_dict()})
    finally:
        db.close()


@app.post('/api/auth/change-password')
@login_required
def change_password():
    data = request.json or {}
    db = get_session()
    try:
        user = _current_user(db)
        if not user.check_password(data.get('old_password', '')):
            return jsonify({'error': '旧密码错误'}), 400
        user.set_password(data['new_password'])
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ============================================================
# 用户管理
# ============================================================

@app.get('/api/users')
@login_required
def list_users():
    db = get_session()
    try:
        me = _current_user(db)
        if me.role == 'admin':
            users = db.query(User).all()
        elif me.role == 'manager':
            sub_ids = me.get_team_user_ids(db)
            users = db.query(User).filter(User.id.in_(sub_ids)).all()
        else:
            users = [me]
        return jsonify({'users': [u.to_dict() for u in users]})
    finally:
        db.close()


@app.post('/api/users')
@admin_required
def create_user():
    data = request.json or {}
    db = get_session()
    try:
        if db.query(User).filter(User.username == data['username']).first():
            return jsonify({'error': '用户名已存在'}), 400
        user = User(
            username  = data['username'],
            role      = data.get('role', 'employee'),
            parent_id = data.get('parent_id'),
        )
        user.set_password(data['password'])
        db.add(user)
        db.commit()
        return jsonify({'user': user.to_dict()}), 201
    finally:
        db.close()


@app.put('/api/users/<int:user_id>')
@admin_required
def update_user(user_id):
    data = request.json or {}
    db = get_session()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return jsonify({'error': '用户不存在'}), 404
        for field in ('role', 'parent_id', 'enabled'):
            if field in data:
                setattr(user, field, data[field])
        if 'password' in data:
            user.set_password(data['password'])
        db.commit()
        return jsonify({'user': user.to_dict()})
    finally:
        db.close()


@app.delete('/api/users/<int:user_id>')
@admin_required
def delete_user(user_id):
    db = get_session()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return jsonify({'error': '用户不存在'}), 404
        db.delete(user)
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ============================================================
# 账号管理
# ============================================================

@app.get('/api/accounts')
@login_required
def list_accounts():
    db = get_session()
    try:
        me = _current_user(db)
        if me.role == 'admin':
            accounts = db.query(Account).all()
        else:
            team_ids = me.get_team_user_ids(db)
            accounts = db.query(Account).filter(Account.user_id.in_(team_ids)).all()
        return jsonify({'accounts': [a.to_dict() for a in accounts]})
    finally:
        db.close()


@app.post('/api/accounts')
@login_required
def create_account():
    data = request.json or {}
    db = get_session()
    try:
        me = _current_user(db)
        account = Account(
            name         = data['name'],
            account_type = data['account_type'],
            cookie_url   = data['cookie_url'],
            user_id      = me.id,
        )
        db.add(account)
        db.commit()
        return jsonify({'account': account.to_dict()}), 201
    finally:
        db.close()


@app.put('/api/accounts/<int:account_id>')
@login_required
def update_account(account_id):
    data = request.json or {}
    db = get_session()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return jsonify({'error': '账号不存在'}), 404
        for field in ('name', 'cookie_url', 'enabled', 'status'):
            if field in data:
                setattr(account, field, data[field])
        db.commit()
        return jsonify({'account': account.to_dict()})
    finally:
        db.close()


@app.delete('/api/accounts/<int:account_id>')
@admin_required
def delete_account(account_id):
    db = get_session()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return jsonify({'error': '账号不存在'}), 404
        db.delete(account)
        db.commit()
        return jsonify({'ok': True})
    finally:
        db.close()


# ============================================================
# OpenClaw 实例管理
# ============================================================

@app.get('/api/openclaw/status')
@login_required
def openclaw_status():
    return jsonify({'instances': oc_manager.get_all_status()})


@app.post('/api/openclaw/<int:account_id>/start')
@login_required
def openclaw_start(account_id):
    success = oc_manager.start_instance(account_id)
    return jsonify({'ok': success})


@app.post('/api/openclaw/<int:account_id>/stop')
@login_required
def openclaw_stop(account_id):
    success = oc_manager.stop_instance(account_id)
    return jsonify({'ok': success})


# ============================================================
# 监控 & 发送控制
# ============================================================

@app.post('/api/monitor/start')
@login_required
def start_monitor():
    """启动所有启用的监控账号 + 所有发送账号 + 任务扫描线程"""
    global _scanner_thread, _scanner_stop

    db = get_session()
    try:
        monitor_accounts = (
            db.query(Account)
            .filter(Account.account_type == 'monitor', Account.enabled == True)
            .all()
        )
        sender_accounts = (
            db.query(Account)
            .filter(Account.account_type == 'sender', Account.enabled == True)
            .all()
        )
        monitor_ids = [a.id for a in monitor_accounts]
        sender_ids  = [a.id for a in sender_accounts]
    finally:
        db.close()

    from fb_monitor import run_monitor_for_account
    from task_queue import run_sender_for_account, run_task_scanner

    # 启动监控线程
    for acc_id in monitor_ids:
        if acc_id not in _monitor_threads or not _monitor_threads[acc_id].is_alive():
            stop_ev = threading.Event()
            _stop_events[f'monitor_{acc_id}'] = stop_ev
            t = threading.Thread(
                target=run_monitor_for_account,
                args=(acc_id, stop_ev),
                name=f'monitor-{acc_id}',
                daemon=True,
            )
            t.start()
            _monitor_threads[acc_id] = t

    # 启动发送线程
    for acc_id in sender_ids:
        if acc_id not in _sender_threads or not _sender_threads[acc_id].is_alive():
            stop_ev = threading.Event()
            _stop_events[f'sender_{acc_id}'] = stop_ev
            t = threading.Thread(
                target=run_sender_for_account,
                args=(acc_id, stop_ev),
                name=f'sender-{acc_id}',
                daemon=True,
            )
            t.start()
            _sender_threads[acc_id] = t

    # 启动任务扫描线程
    if _scanner_thread is None or not _scanner_thread.is_alive():
        _scanner_stop = threading.Event()
        _scanner_thread = threading.Thread(
            target=run_task_scanner,
            args=(_scanner_stop,),
            name='task-scanner',
            daemon=True,
        )
        _scanner_thread.start()

    # 启动心跳检测
    oc_manager.start_heartbeat()

    return jsonify({
        'ok': True,
        'monitor_accounts': len(monitor_ids),
        'sender_accounts':  len(sender_ids),
    })


@app.post('/api/monitor/stop')
@login_required
def stop_monitor():
    """停止所有运行中的线程"""
    global _scanner_stop

    for key, ev in _stop_events.items():
        ev.set()
    _stop_events.clear()
    _monitor_threads.clear()
    _sender_threads.clear()

    if _scanner_stop:
        _scanner_stop.set()

    oc_manager.stop_heartbeat()

    return jsonify({'ok': True})


@app.get('/api/monitor/status')
@login_required
def monitor_status():
    monitor_running = {k: v.is_alive() for k, v in _monitor_threads.items()}
    sender_running  = {k: v.is_alive() for k, v in _sender_threads.items()}
    return jsonify({
        'monitor_threads': monitor_running,
        'sender_threads':  sender_running,
        'scanner_running': _scanner_thread is not None and _scanner_thread.is_alive(),
    })


# ============================================================
# 帖子
# ============================================================

@app.get('/api/posts')
@login_required
def list_posts():
    db = get_session()
    try:
        me = _current_user(db)
        team_ids = me.get_team_user_ids(db)

        q = db.query(Post).join(Account, Account.id == Post.discovered_by, isouter=True)
        if me.role != 'admin':
            q = q.filter(Account.user_id.in_(team_ids))

        # 过滤参数
        if request.args.get('is_target') is not None:
            q = q.filter(Post.is_target == (request.args['is_target'] == '1'))
        if request.args.get('has_wa') == '1':
            q = q.filter(Post.customer_wa != None)
        if request.args.get('q'):
            keyword = f"%{request.args['q']}%"
            q = q.filter(Post.content.like(keyword))

        total = q.count()
        page  = int(request.args.get('page', 1))
        size  = int(request.args.get('size', 20))
        posts = q.order_by(Post.created_at.desc()).offset((page - 1) * size).limit(size).all()

        return jsonify({
            'posts': [p.to_dict() for p in posts],
            'total': total,
            'page':  page,
            'pages': (total + size - 1) // size,
        })
    finally:
        db.close()


@app.get('/api/posts/<int:post_id>')
@login_required
def get_post(post_id):
    db = get_session()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            return jsonify({'error': '帖子不存在'}), 404
        data = post.to_dict()
        # 附带任务状态
        tasks = db.query(SendTask).filter(SendTask.post_id == post_id).all()
        data['tasks'] = [t.to_dict() for t in tasks]
        return jsonify({'post': data})
    finally:
        db.close()


@app.post('/api/posts/<int:post_id>/feedback')
@login_required
def post_feedback(post_id):
    data = request.json or {}
    db = get_session()
    try:
        me = _current_user(db)
        fb = db.query(PostFeedback).filter(
            PostFeedback.post_id == post_id,
            PostFeedback.user_id == me.id,
        ).first()
        if not fb:
            fb = PostFeedback(post_id=post_id, user_id=me.id)
            db.add(fb)
        for field in ('is_target_manual', 'contact_status', 'whatsapp_number', 'notes'):
            if field in data:
                setattr(fb, field, data[field])
        # 如果运营手动录入了 WA 号，同步到帖子
        if data.get('whatsapp_number'):
            post = db.query(Post).filter(Post.id == post_id).first()
            if post and not post.customer_wa:
                post.customer_wa     = data['whatsapp_number']
                post.wa_source       = 'manual'
                post.wa_extracted_at = datetime.now(timezone.utc)
        db.commit()
        return jsonify({'feedback': fb.to_dict()})
    finally:
        db.close()


# ============================================================
# 任务
# ============================================================

@app.get('/api/tasks')
@login_required
def list_tasks():
    db = get_session()
    try:
        q = db.query(SendTask)
        if request.args.get('status'):
            q = q.filter(SendTask.status == request.args['status'])
        if request.args.get('type'):
            q = q.filter(SendTask.task_type == request.args['type'])
        if request.args.get('account_id'):
            q = q.filter(SendTask.account_id == int(request.args['account_id']))

        total = q.count()
        page  = int(request.args.get('page', 1))
        size  = int(request.args.get('size', 20))
        tasks = q.order_by(SendTask.created_at.desc()).offset((page - 1) * size).limit(size).all()

        return jsonify({
            'tasks': [t.to_dict() for t in tasks],
            'total': total,
            'page':  page,
        })
    finally:
        db.close()


# ============================================================
# 统计
# ============================================================

@app.get('/api/stats')
@login_required
def stats():
    db = get_session()
    try:
        total_posts   = db.query(Post).count()
        target_posts  = db.query(Post).filter(Post.is_target == True).count()
        wa_collected  = db.query(Post).filter(Post.customer_wa != None).count()
        wa_messaged   = db.query(Post).filter(Post.wa_messaged == True).count()
        tasks_pending = db.query(SendTask).filter(SendTask.status == 'pending').count()
        tasks_done    = db.query(SendTask).filter(SendTask.status == 'completed').count()
        tasks_failed  = db.query(SendTask).filter(SendTask.status == 'failed').count()
        return jsonify({
            'total_posts':   total_posts,
            'target_posts':  target_posts,
            'wa_collected':  wa_collected,
            'wa_messaged':   wa_messaged,
            'tasks_pending': tasks_pending,
            'tasks_done':    tasks_done,
            'tasks_failed':  tasks_failed,
        })
    finally:
        db.close()


# ============================================================
# 启动入口
# ============================================================

@app.get('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    init_db()
    oc_manager.start_heartbeat()
    app.run(host='0.0.0.0', port=FLASK_PORT, debug=False)
