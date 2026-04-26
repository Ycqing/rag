"""
admin/services/store.py  —  文档元数据 + 用户持久化（MySQL）

使用 PyMySQL 直连，连接池用简单的 threading.local 实现（适合 uvicorn 多线程模式）。
敏感配置（host / user / password）全部从 config.py 读取，config.py 再从 .env 读取。
"""

import json
import uuid
import threading
from datetime import datetime

import pymysql
import pymysql.cursors

import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
import config

# ── 线程级连接复用 ────────────────────────────────────────────────────────────
# 每个线程持有自己的连接，避免多线程共享同一连接时的线程安全问题。
_local = threading.local()


def get_conn() -> pymysql.connections.Connection:
    """
    获取当前线程的 MySQL 连接。
    若连接不存在或已断开，则新建一条。
    """
    conn = getattr(_local, "conn", None)
    if conn is None or not conn.open:
        conn = _new_conn()
        _local.conn = conn
    else:
        # ping 一次，自动重连（防止 MySQL 8 h 超时断连）
        try:
            conn.ping(reconnect=True)
        except Exception:
            conn = _new_conn()
            _local.conn = conn
    return conn


def _new_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,   # 结果以 dict 返回，与 sqlite3.Row 行为一致
        autocommit=False,
    )


# ── 初始化表结构 ───────────────────────────────────────────────────────────────

def init_db():
    """
    初始化数据库表结构，应用启动时调用一次。
    使用 CREATE TABLE IF NOT EXISTS，可安全重复执行。
    """
    conn = get_conn()
    with conn.cursor() as cur:
        # ── documents 表 ──────────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id          VARCHAR(36)  PRIMARY KEY,
                filename    VARCHAR(512) NOT NULL,
                doc_type    VARCHAR(20)  NOT NULL,
                roles       TEXT         NOT NULL,
                node_count  INT          DEFAULT 0,
                status      VARCHAR(20)  DEFAULT 'pending'
                    COMMENT 'pending / indexing / indexed / error',
                error_msg   TEXT,
                file_size   INT          DEFAULT 0,
                created_at  DATETIME     NOT NULL,
                updated_at  DATETIME     NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)

        # ── users 表 ──────────────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          VARCHAR(36)  PRIMARY KEY,
                username    VARCHAR(128) UNIQUE NOT NULL,
                password    VARCHAR(256) NOT NULL
                    COMMENT '生产环境请改用 bcrypt 哈希',
                role        VARCHAR(32)  NOT NULL,
                name        VARCHAR(128) NOT NULL,
                created_at  DATETIME     NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)

        # ── 预置测试用户（INSERT IGNORE 保证幂等）─────────────────────────────
        preset_users = [
            ("alice",   "test123", "hr",      "Alice（HR）"),
            ("bob",     "test123", "dev",     "Bob（研发）"),
            ("charlie", "test123", "admin",   "Charlie（管理员）"),
            ("david",   "test123", "all",     "David（普通员工）"),
        ]
        for username, password, role, name in preset_users:
            cur.execute("""
                INSERT IGNORE INTO users (id, username, password, role, name, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), username, password, role, name, _now()))

    conn.commit()


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


# ══════════════════════════════════════════════════════════════════════════════
# 文档 CRUD
# ══════════════════════════════════════════════════════════════════════════════

def create_document(
    filename: str,
    doc_type: str,
    roles: list[str],
    file_size: int,
) -> str:
    doc_id = str(uuid.uuid4())
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO documents
                (id, filename, doc_type, roles, file_size, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
        """, (
            doc_id,
            filename,
            doc_type,
            json.dumps(roles, ensure_ascii=False),
            file_size,
            _now(),
            _now(),
        ))
    conn.commit()
    return doc_id


def update_document_status(
    doc_id: str,
    status: str,
    node_count: int = 0,
    error_msg: str = None,
):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE documents
               SET status = %s, node_count = %s, error_msg = %s, updated_at = %s
             WHERE id = %s
        """, (status, node_count, error_msg, _now(), doc_id))
    conn.commit()


def delete_document(doc_id: str) -> bool:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        affected = cur.rowcount
    conn.commit()
    return affected > 0


def list_documents() -> list[dict]:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM documents ORDER BY created_at DESC")
        rows = cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        # roles 存的是 JSON 字符串，反序列化回 list
        if isinstance(d["roles"], str):
            d["roles"] = json.loads(d["roles"])
        # datetime 对象转字符串，方便前端直接用
        for key in ("created_at", "updated_at"):
            if isinstance(d[key], datetime):
                d[key] = d[key].strftime("%Y-%m-%d %H:%M:%S")
        result.append(d)
    return result


def get_document(doc_id: str) -> dict | None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    if isinstance(d["roles"], str):
        d["roles"] = json.loads(d["roles"])
    for key in ("created_at", "updated_at"):
        if isinstance(d[key], datetime):
            d[key] = d[key].strftime("%Y-%m-%d %H:%M:%S")
    return d


# ══════════════════════════════════════════════════════════════════════════════
# 用户 CRUD
# ══════════════════════════════════════════════════════════════════════════════

def list_users() -> list[dict]:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, username, role, name, created_at FROM users ORDER BY created_at"
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if isinstance(d["created_at"], datetime):
            d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        result.append(d)
    return result


def create_user(
    username: str,
    password: str,
    role: str,
    name: str,
) -> str:
    user_id = str(uuid.uuid4())
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO users (id, username, password, role, name, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, username, password, role, name, _now()))
    conn.commit()
    return user_id


def delete_user(user_id: str) -> bool:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        affected = cur.rowcount
    conn.commit()
    return affected > 0


def get_user_by_username(username: str) -> dict | None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return d


# ══════════════════════════════════════════════════════════════════════════════
# 统计
# ══════════════════════════════════════════════════════════════════════════════

def get_stats() -> dict:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM documents")
        total_docs = cur.fetchone()["cnt"]

        cur.execute("SELECT COALESCE(SUM(node_count), 0) AS total FROM documents")
        total_nodes = cur.fetchone()["total"]

        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        total_users = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM documents WHERE status = 'indexed'")
        indexed = cur.fetchone()["cnt"]

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM documents WHERE status IN ('pending', 'indexing')"
        )
        pending = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(*) AS cnt FROM documents WHERE status = 'error'")
        errors = cur.fetchone()["cnt"]

    return {
        "total_docs":  total_docs,
        "total_nodes": int(total_nodes),
        "total_users": total_users,
        "indexed":     indexed,
        "pending":     pending,
        "errors":      errors,
    }