import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows 无 fcntl，本地单进程运行无需文件锁
    fcntl = None

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    legal_name TEXT DEFAULT '',
    short_name TEXT DEFAULT '',
    city TEXT DEFAULT '',
    industry TEXT DEFAULT '',
    contact_user_id INTEGER REFERENCES users(id),
    contact_name TEXT DEFAULT '',
    status TEXT DEFAULT '开发中',
    notes TEXT DEFAULT '',
    extra_json TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER REFERENCES companies(id),
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual')),
    external_id TEXT,
    seq_no TEXT DEFAULT '',
    title TEXT NOT NULL,
    jd_raw TEXT NOT NULL,
    jd_hash TEXT DEFAULT '',
    jd_struct_json TEXT DEFAULT '',
    consultant_notes TEXT DEFAULT '',
    salary_range TEXT DEFAULT '',
    hard_requirements TEXT DEFAULT '',
    nice_to_have TEXT DEFAULT '',
    project_status TEXT DEFAULT '未开始',
    n_talents INTEGER DEFAULT 0,
    n_recommend INTEGER DEFAULT 0,
    n_interview INTEGER DEFAULT 0,
    n_offer INTEGER DEFAULT 0,
    consultant TEXT DEFAULT '',
    hard_struct_json TEXT DEFAULT '',
    project_json TEXT DEFAULT '',
    headcount INTEGER DEFAULT 1,
    is_project INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL CHECK (layer IN ('general','company','position')),
    company_id INTEGER REFERENCES companies(id),
    position_id INTEGER REFERENCES positions(id),
    content TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'company_default',
    owner_user_id INTEGER REFERENCES users(id),
    owner_name TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS user_prompt_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    general_prompt_id INTEGER REFERENCES prompts(id),
    general_prompt_source TEXT NOT NULL DEFAULT 'company'
        CHECK (general_prompt_source IN ('company','personal')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT DEFAULT '',
    resume_file TEXT DEFAULT '',
    resume_hash TEXT DEFAULT '',
    resume_text TEXT DEFAULT '',
    resume_struct_json TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    city TEXT DEFAULT '',
    current_salary TEXT DEFAULT '',
    expected_salary TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    target_position TEXT DEFAULT '',
    current_company TEXT DEFAULT '',
    current_title TEXT DEFAULT '',
    age TEXT DEFAULT '',
    talent_status TEXT DEFAULT '未推荐',
    owner TEXT DEFAULT '',
    created_by_user_id INTEGER,
    is_temp INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS match_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    created_by_user_id INTEGER,
    position_ids_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','detailing','done','done_with_errors','failed')),
    model TEXT DEFAULT '',
    prompt_snapshot_json TEXT DEFAULT '',
    error TEXT DEFAULT '',
    total_tokens INTEGER DEFAULT 0,
    started_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS match_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES match_runs(id),
    position_id INTEGER NOT NULL REFERENCES positions(id),
    exclude_reason TEXT DEFAULT '',
    final_score INTEGER,
    recommend_level TEXT,
    detail_rows_json TEXT DEFAULT '',
    dims_json TEXT DEFAULT '',
    suggestions_json TEXT DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending','approved','rejected','needs_info')),
    reviewer TEXT DEFAULT '',
    review_comment TEXT DEFAULT '',
    reviewed_at TEXT,
    raw_llm_output TEXT DEFAULT '',
    input_signature TEXT DEFAULT '',
    reused_from_result_id INTEGER,
    tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_results_run ON match_results(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created ON match_runs(created_at);

CREATE TABLE IF NOT EXISTS client_recommendation_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER NOT NULL REFERENCES match_results(id),
    version INTEGER NOT NULL DEFAULT 1,
    content_json TEXT NOT NULL DEFAULT '',
    markdown TEXT NOT NULL DEFAULT '',
    model TEXT DEFAULT '',
    tokens INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT '草稿',
    feishu_record_id TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_client_reports_result
    ON client_recommendation_reports(result_id, version);

CREATE TABLE IF NOT EXISTS candidate_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    position_id INTEGER REFERENCES positions(id),
    match_result_id INTEGER,
    final_score_snapshot REAL,
    recommend_level_snapshot TEXT DEFAULT '',
    source TEXT DEFAULT '',
    recommended_at TEXT DEFAULT '',
    interview_at TEXT DEFAULT '',
    stage TEXT NOT NULL DEFAULT '待推荐',
    reject_reason TEXT DEFAULT '',
    client_feedback TEXT DEFAULT '待反馈',
    next_action TEXT DEFAULT '',
    owner TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tracking_cand_pos
    ON candidate_tracking(candidate_id, position_id);

CREATE TABLE IF NOT EXISTS tracking_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_id INTEGER,
    actor TEXT DEFAULT '',
    action TEXT NOT NULL,
    field TEXT DEFAULT '',
    old_value TEXT DEFAULT '',
    new_value TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'consultant'
        CHECK (role IN ('super_admin','business_admin','consultant')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('pending','active','disabled')),
    feishu_record_id TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    run_ids_json TEXT NOT NULL DEFAULT '[]',
    added_result_ids_json TEXT NOT NULL DEFAULT '[]',
    hidden_result_ids_json TEXT NOT NULL DEFAULT '[]',
    title TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(user_id, run_ids_json)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL DEFAULT '',
    model TEXT DEFAULT '',
    tokens INTEGER DEFAULT 0,
    feishu_record_id TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS feishu_sync_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    action TEXT NOT NULL DEFAULT 'push',
    status TEXT NOT NULL DEFAULT 'failed'
        CHECK (status IN ('pending','synced','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(entity_type, entity_id, action)
);
CREATE INDEX IF NOT EXISTS idx_feishu_sync_jobs_status
    ON feishu_sync_jobs(status, updated_at);

CREATE TABLE IF NOT EXISTS ai_usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    endpoint TEXT NOT NULL DEFAULT '',
    model TEXT DEFAULT '',
    tokens INTEGER DEFAULT 0,
    metadata_json TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_user_date
    ON ai_usage_logs(user_id, created_at);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users(id),
    actor_name TEXT DEFAULT '',
    actor_role TEXT DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    entity_type TEXT NOT NULL DEFAULT '',
    entity_id INTEGER,
    entity_name TEXT DEFAULT '',
    detail_json TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_operation_logs_entity
    ON operation_logs(entity_type, entity_id, created_at);
"""

_local = threading.local()


def get_conn(db_path: Path = None) -> sqlite3.Connection:
    """线程内复用连接；匹配流水线在后台线程执行，各自持有独立连接。"""
    path = str(db_path or settings.db_path)
    conn = getattr(_local, "conn_" + path, None)
    if conn is None:
        conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        setattr(_local, "conn_" + path, conn)
    return conn


def table_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def company_display_expr(conn, alias: str = "c") -> str:
    cols = table_columns(conn, "companies")
    parts = []
    if "legal_name" in cols:
        parts.append(f"NULLIF({alias}.legal_name,'')")
    if "short_name" in cols:
        parts.append(f"NULLIF({alias}.short_name,'')")
    parts.append(f"{alias}.name")
    if len(parts) == 1:
        return parts[0]
    return f"COALESCE({', '.join(parts)})"


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """多 worker（uvicorn --workers）同时启动时，用文件锁串行化建库/迁移，避免 SQLite 写锁竞争。"""
    if fcntl is None:
        _do_init_db()
        return
    lock_path = settings.db_path.parent / ".init.lock"
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _do_init_db()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _do_init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    _seed_general_prompt(conn)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, existing_cols: set[str], column: str, ddl: str) -> None:
    if column in existing_cols:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def ensure_chat_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            run_ids_json TEXT NOT NULL DEFAULT '[]',
            added_result_ids_json TEXT NOT NULL DEFAULT '[]',
            hidden_result_ids_json TEXT NOT NULL DEFAULT '[]',
            title TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(user_id, run_ids_json)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
            role TEXT NOT NULL CHECK (role IN ('user','assistant')),
            content TEXT NOT NULL DEFAULT '',
            model TEXT DEFAULT '',
            tokens INTEGER DEFAULT 0,
            feishu_record_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()
    chat_session_cols = {r["name"] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
    for col, ddl in (
        ("user_id", "INTEGER REFERENCES users(id)"),
        ("title", "TEXT DEFAULT ''"),
        ("created_at", "TEXT DEFAULT ''"),
        ("updated_at", "TEXT DEFAULT ''"),
        ("hidden_result_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("added_result_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        _add_column_if_missing(conn, "chat_sessions", chat_session_cols, col, ddl)
    chat_message_cols = {r["name"] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    for col, ddl in (("model", "TEXT DEFAULT ''"), ("tokens", "INTEGER DEFAULT 0"), ("feishu_record_id", "TEXT DEFAULT ''"), ("created_at", "TEXT DEFAULT ''")):
        _add_column_if_missing(conn, "chat_messages", chat_message_cols, col, ddl)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_session "
        "ON chat_messages(session_id, created_at)"
    )
    conn.commit()


def _match_runs_allows_done_with_errors(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='match_runs'"
    ).fetchone()
    return bool(row and "done_with_errors" in (row["sql"] or ""))


def _rebuild_match_runs_status_check(conn: sqlite3.Connection):
    if _match_runs_allows_done_with_errors(conn):
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("""
            CREATE TABLE match_runs_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL REFERENCES candidates(id),
                created_by_user_id INTEGER,
                position_ids_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','detailing','done','done_with_errors','failed')),
                model TEXT DEFAULT '',
                prompt_snapshot_json TEXT DEFAULT '',
                error TEXT DEFAULT '',
                total_tokens INTEGER DEFAULT 0,
                started_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                finished_at TEXT
            )
        """)
        conn.execute("""
            INSERT INTO match_runs_new (
                id, candidate_id, created_by_user_id, position_ids_json, status, model, prompt_snapshot_json,
                error, total_tokens, started_at, attempts, created_at, finished_at
            )
            SELECT
                id, candidate_id, NULL, position_ids_json, status, model, prompt_snapshot_json,
                error, total_tokens, started_at, attempts, created_at, finished_at
            FROM match_runs
        """)
        conn.execute("DROP TABLE match_runs")
        conn.execute("ALTER TABLE match_runs_new RENAME TO match_runs")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_created ON match_runs(created_at)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate(conn: sqlite3.Connection):
    """已有库的增量迁移：新增列时在这里补 ALTER TABLE。"""
    company_cols = {r["name"] for r in conn.execute("PRAGMA table_info(companies)").fetchall()}
    for col, ddl in (
        ("legal_name", "TEXT DEFAULT ''"),
        ("short_name", "TEXT DEFAULT ''"),
        ("city", "TEXT DEFAULT ''"),
        ("industry", "TEXT DEFAULT ''"),
        ("contact_user_id", "INTEGER REFERENCES users(id)"),
        ("contact_name", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT '开发中'"),
        ("extra_json", "TEXT DEFAULT ''"),
    ):
        if col not in company_cols:
            conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {ddl}")
            conn.commit()
    # 历史 name 保存的是顾问日常使用的公司名/品牌名，不能直接视为工商主体名。
    # 首次迁移时把它保留为简称，营业执照名称留空等待人工补全。
    conn.execute(
        "UPDATE companies SET short_name=name"
        " WHERE TRIM(COALESCE(legal_name,''))=''"
        " AND TRIM(COALESCE(short_name,''))='' AND TRIM(name)!=''"
    )
    conn.execute("DROP INDEX IF EXISTS idx_companies_legal_name")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_legal_name"
        " ON companies(legal_name) WHERE TRIM(legal_name)!=''"
    )
    conn.commit()
    company_cols = {r["name"] for r in conn.execute("PRAGMA table_info(companies)").fetchall()}
    if "feishu_record_id" not in company_cols:
        conn.execute("ALTER TABLE companies ADD COLUMN feishu_record_id TEXT DEFAULT ''")
        conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "headcount" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN headcount INTEGER DEFAULT 1")
        conn.commit()
    for col in ("salary_range", "hard_requirements", "nice_to_have"):
        if col not in cols:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} TEXT DEFAULT ''")
            conn.commit()
    if "project_status" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN project_status TEXT DEFAULT '未开始'")
        conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    for col in ("n_talents", "n_recommend", "n_interview", "n_offer"):
        if col not in cols:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {col} INTEGER DEFAULT 0")
            conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "consultant" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN consultant TEXT DEFAULT ''")
        conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "project_json" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN project_json TEXT DEFAULT ''")
        conn.commit()
    conn.execute(
        "UPDATE positions SET project_status=CASE"
        " WHEN project_status IN ('待开始','待启动') THEN '未开始'"
        " WHEN project_status IN ('进展中','Live','live') THEN '进行中'"
        " WHEN project_status IN ('已暂停','已完成','已取消') THEN '已结束'"
        " WHEN project_status IS NULL OR project_status='' THEN '未开始'"
        " ELSE project_status END"
    )
    conn.commit()
    if "hard_struct_json" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN hard_struct_json TEXT DEFAULT ''")
        conn.commit()
    if "is_project" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN is_project INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "feishu_record_id" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN feishu_record_id TEXT DEFAULT ''")
        conn.commit()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(positions)").fetchall()}
    if "feishu_project_record_id" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN feishu_project_record_id TEXT DEFAULT ''")
        conn.commit()
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(candidates)").fetchall()}
    for col in ("phone", "city", "current_salary", "expected_salary", "tags",
                "target_position", "current_company", "current_title", "age", "owner", "resume_hash"):
        if col not in ccols:
            conn.execute(f"ALTER TABLE candidates ADD COLUMN {col} TEXT DEFAULT ''")
            conn.commit()
    if "created_by_user_id" not in ccols:
        conn.execute("ALTER TABLE candidates ADD COLUMN created_by_user_id INTEGER")
        conn.commit()
    if "talent_status" not in ccols:
        conn.execute("ALTER TABLE candidates ADD COLUMN talent_status TEXT DEFAULT '未推荐'")
        conn.commit()
    if "is_temp" not in ccols:
        conn.execute("ALTER TABLE candidates ADD COLUMN is_temp INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(prompts)").fetchall()}
    if "feishu_record_id" not in pcols:
        conn.execute("ALTER TABLE prompts ADD COLUMN feishu_record_id TEXT DEFAULT ''")
        conn.commit()
    if "scope" not in pcols:
        conn.execute("ALTER TABLE prompts ADD COLUMN scope TEXT NOT NULL DEFAULT 'company_default'")
        conn.commit()
    if "owner_user_id" not in pcols:
        conn.execute("ALTER TABLE prompts ADD COLUMN owner_user_id INTEGER REFERENCES users(id)")
        conn.commit()
    if "owner_name" not in pcols:
        conn.execute("ALTER TABLE prompts ADD COLUMN owner_name TEXT DEFAULT ''")
        conn.commit()
    conn.execute(
        "UPDATE prompts SET scope='company_default' WHERE scope IS NULL OR scope=''"
    )
    conn.commit()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_prompt_preferences (
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            general_prompt_id INTEGER REFERENCES prompts(id),
            general_prompt_source TEXT NOT NULL DEFAULT 'company'
                CHECK (general_prompt_source IN ('company','personal')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute("UPDATE match_runs SET status='detailing' WHERE status='screening'")
    run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(match_runs)").fetchall()}
    if "started_at" not in run_cols:
        conn.execute("ALTER TABLE match_runs ADD COLUMN started_at TEXT")
        conn.commit()
    if "attempts" not in run_cols:
        conn.execute("ALTER TABLE match_runs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "created_by_user_id" not in run_cols:
        conn.execute("ALTER TABLE match_runs ADD COLUMN created_by_user_id INTEGER")
        conn.commit()
    _rebuild_match_runs_status_check(conn)
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(candidate_tracking)").fetchall()}
    if "owner" not in tcols:
        conn.execute("ALTER TABLE candidate_tracking ADD COLUMN owner TEXT DEFAULT ''")
        conn.commit()
    if "stage" not in tcols:
        conn.execute("ALTER TABLE candidate_tracking ADD COLUMN stage TEXT NOT NULL DEFAULT '待推荐'")
        if "reject_reason" not in tcols:
            conn.execute("ALTER TABLE candidate_tracking ADD COLUMN reject_reason TEXT DEFAULT ''")
        # 旧库如果还存在四布尔列（推荐/初筛/一面/二面）+ status，只在迁移时映射一次到新 stage。
        if {"recommend", "screen_pass", "first_pass", "second_pass", "status"}.issubset(tcols):
            conn.execute("""
                UPDATE candidate_tracking SET stage = CASE
                    WHEN status = '已入职' THEN '入职'
                    WHEN recommend = '否' THEN 'pass'
                    WHEN status = '已终止' THEN 'pass'
                    WHEN second_pass = '是' THEN '二面'
                    WHEN first_pass = '是' THEN '一面'
                    WHEN screen_pass = '是' THEN '初筛（已推荐客户）'
                    WHEN recommend = '是' THEN '初筛（已推荐客户）'
                    ELSE '待推荐'
                END,
                reject_reason = CASE
                    WHEN recommend = '否' THEN '顾问放弃'
                    WHEN status = '已终止' THEN '其他'
                    ELSE ''
                END
            """)
        conn.commit()
    elif "reject_reason" not in tcols:
        conn.execute("ALTER TABLE candidate_tracking ADD COLUMN reject_reason TEXT DEFAULT ''")
        conn.commit()
    for col, default in (("client_feedback", "待反馈"), ("next_action", "")):
        if col not in tcols:
            conn.execute(f"ALTER TABLE candidate_tracking ADD COLUMN {col} TEXT DEFAULT '{default}'")
            conn.commit()
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(candidate_tracking)").fetchall()}
    tracking_extra_columns = {
        "match_result_id": "INTEGER",
        "final_score_snapshot": "REAL",
        "recommend_level_snapshot": "TEXT DEFAULT ''",
        "source": "TEXT DEFAULT ''",
        "recommended_at": "TEXT DEFAULT ''",
        "interview_at": "TEXT DEFAULT ''",
    }
    for col, ddl in tracking_extra_columns.items():
        if col not in tcols:
            conn.execute(f"ALTER TABLE candidate_tracking ADD COLUMN {col} {ddl}")
            conn.commit()
    conn.execute(
        """
        UPDATE candidate_tracking SET stage = CASE stage
            WHEN '已推荐客户' THEN '初筛（已推荐客户）'
            WHEN '客户初筛通过' THEN '初筛（已推荐客户）'
            WHEN '客户初筛未通过' THEN 'pass'
            WHEN '需补充信息' THEN '初筛（已推荐客户）'
            WHEN '终面' THEN '四面'
            WHEN 'Offer' THEN 'offer'
            WHEN '淘汰' THEN 'pass'
            ELSE stage
        END
        """
    )
    conn.commit()
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(candidate_tracking)").fetchall()}
    if "feishu_record_id" not in tcols:
        conn.execute("ALTER TABLE candidate_tracking ADD COLUMN feishu_record_id TEXT DEFAULT ''")
        conn.commit()
    rcols = {r["name"] for r in conn.execute("PRAGMA table_info(match_results)").fetchall()}
    if "dims_json" not in rcols:
        conn.execute("ALTER TABLE match_results ADD COLUMN dims_json TEXT DEFAULT ''")
        conn.commit()
    if {"screen_low", "screen_high", "screen_included"}.issubset(rcols):
        conn.execute(
            "UPDATE match_results SET screen_low=NULL, screen_high=NULL, screen_included=NULL"
        )
        conn.commit()
    for col in ("review_status", "reviewer", "review_comment", "reviewed_at"):
        if col not in rcols:
            if col == "review_status":
                conn.execute("ALTER TABLE match_results ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending'")
            else:
                conn.execute(f"ALTER TABLE match_results ADD COLUMN {col} TEXT DEFAULT ''")
            conn.commit()
    for col, ddl in (("input_signature", "TEXT DEFAULT ''"), ("reused_from_result_id", "INTEGER")):
        if col not in rcols:
            conn.execute(f"ALTER TABLE match_results ADD COLUMN {col} {ddl}")
            conn.commit()
    rcols = {r["name"] for r in conn.execute("PRAGMA table_info(match_results)").fetchall()}
    if "feishu_record_id" not in rcols:
        conn.execute("ALTER TABLE match_results ADD COLUMN feishu_record_id TEXT DEFAULT ''")
        conn.commit()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_recommendation_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER NOT NULL REFERENCES match_results(id),
            version INTEGER NOT NULL DEFAULT 1,
            content_json TEXT NOT NULL DEFAULT '',
            markdown TEXT NOT NULL DEFAULT '',
            model TEXT DEFAULT '',
            tokens INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT '草稿',
            feishu_record_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_client_reports_result "
        "ON client_recommendation_reports(result_id, version)"
    )
    crcols = {r["name"] for r in conn.execute("PRAGMA table_info(client_recommendation_reports)").fetchall()}
    for col, ddl in (
        ("markdown", "TEXT NOT NULL DEFAULT ''"),
        ("model", "TEXT DEFAULT ''"),
        ("tokens", "INTEGER DEFAULT 0"),
        ("status", "TEXT NOT NULL DEFAULT '草稿'"),
        ("feishu_record_id", "TEXT DEFAULT ''"),
        ("updated_at", "TEXT DEFAULT (datetime('now', 'localtime'))"),
    ):
        if col not in crcols:
            conn.execute(f"ALTER TABLE client_recommendation_reports ADD COLUMN {col} {ddl}")
            conn.commit()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            endpoint TEXT NOT NULL DEFAULT '',
            model TEXT DEFAULT '',
            tokens INTEGER DEFAULT 0,
            metadata_json TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_usage_user_date "
        "ON ai_usage_logs(user_id, created_at)"
    )
    conn.commit()
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "role" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'consultant'")
        conn.commit()
    if "status" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        conn.commit()
    if "feishu_record_id" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN feishu_record_id TEXT DEFAULT ''")
        conn.commit()
    conn.execute("UPDATE users SET role='consultant' WHERE role IN ('reviewer','readonly')")
    conn.commit()
    super_admin_names = [name for name in settings.super_admin_names if name]
    if super_admin_names:
        placeholders = ",".join(["?"] * len(super_admin_names))
        conn.execute(
            f"UPDATE users SET role='super_admin' WHERE username IN ({placeholders})"
            f" OR display_name IN ({placeholders})",
            [*super_admin_names, *super_admin_names],
        )
        conn.commit()
    business_admin_names = [name for name in settings.business_admin_names if name]
    if business_admin_names:
        placeholders = ",".join(["?"] * len(business_admin_names))
        conn.execute(
            f"UPDATE users SET role='consultant' WHERE role='business_admin'"
            f" AND username NOT IN ({placeholders}) AND display_name NOT IN ({placeholders})",
            [*business_admin_names, *business_admin_names],
        )
        conn.execute(
            f"UPDATE users SET role='business_admin' WHERE role!='super_admin'"
            f" AND (username IN ({placeholders}) OR display_name IN ({placeholders}))",
            [*business_admin_names, *business_admin_names],
        )
        conn.commit()
    ensure_chat_schema(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS operation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER REFERENCES users(id),
            actor_name TEXT DEFAULT '',
            actor_role TEXT DEFAULT '',
            action TEXT NOT NULL DEFAULT '',
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id INTEGER,
            entity_name TEXT DEFAULT '',
            detail_json TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operation_logs_entity "
        "ON operation_logs(entity_type, entity_id, created_at)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracking_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_id INTEGER,
            actor TEXT DEFAULT '',
            action TEXT NOT NULL,
            field TEXT DEFAULT '',
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_run ON match_results(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_resume_hash ON candidates(resume_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_created_by ON match_runs(created_by_user_id, created_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_results_position_run "
        "ON match_results(position_id, run_id)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feishu_sync_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            action TEXT NOT NULL DEFAULT 'push',
            status TEXT NOT NULL DEFAULT 'failed'
                CHECK (status IN ('pending','synced','failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(entity_type, entity_id, action)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_feishu_sync_jobs_status "
        "ON feishu_sync_jobs(status, updated_at)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_created ON match_runs(created_at)")
    conn.execute(
        "UPDATE positions SET consultant_notes='' "
        "WHERE consultant_notes IN ('非90后+211/985 勿推','非90后勿推')"
    )
    conn.commit()


def _seed_general_prompt(conn: sqlite3.Connection):
    """首次启动时把 app/prompts/general_default.md 写入通用提示词。"""
    row = conn.execute(
        "SELECT id FROM prompts WHERE layer='general' LIMIT 1"
    ).fetchone()
    if row:
        return
    default_path = Path(__file__).parent / "prompts" / "general_default.md"
    if default_path.exists():
        conn.execute(
            "INSERT INTO prompts (layer, content, updated_by) VALUES ('general', ?, 'seed')",
            (default_path.read_text(encoding="utf-8"),),
        )
        conn.commit()


def rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
        " updated_at=datetime('now','localtime')",
        (key, value),
    )
    conn.commit()


def loads_or(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
