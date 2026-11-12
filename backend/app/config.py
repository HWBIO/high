import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _csv(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def _origin(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _provider() -> str:
    return os.environ.get("LLM_PROVIDER", "openai").strip().lower()


@dataclass
class Settings:
    llm_provider: str = field(default_factory=_provider)
    openai_api_key: str = field(default_factory=lambda: (
        os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        if _provider() == "deepseek"
        else os.environ.get("OPENAI_API_KEY", "")
    ))
    openai_base_url: str = field(default_factory=lambda: (
        os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        if _provider() == "deepseek"
        else os.environ.get("OPENAI_BASE_URL", "http://litellm.starways.team/v1")
    ))
    openai_model: str = field(default_factory=lambda: (
        os.environ.get("DEEPSEEK_MODEL", "") or os.environ.get("OPENAI_MODEL", "") or "deepseek-v4-flash"
        if _provider() == "deepseek"
        else os.environ.get("OPENAI_MODEL", "deepseek-v3")
    ))
    openai_timeout: int = field(default_factory=lambda: _int("OPENAI_TIMEOUT", 120))
    openai_max_tokens: int = field(default_factory=lambda: _int("OPENAI_MAX_TOKENS", 4096))
    openai_max_retries: int = field(default_factory=lambda: _int("OPENAI_MAX_RETRIES", 3))
    llm_max_concurrency: int = field(default_factory=lambda: _int("LLM_MAX_CONCURRENCY", 3))
    llm_json_mode: bool = field(default_factory=lambda: _bool("LLM_JSON_MODE", _provider() == "deepseek"))
    deepseek_thinking: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_THINKING", ""))
    deepseek_reasoning_effort: str = field(default_factory=lambda: os.environ.get("DEEPSEEK_REASONING_EFFORT", "high"))
    ai_daily_request_limit: int = field(default_factory=lambda: _int("AI_DAILY_REQUEST_LIMIT", 0))
    ai_daily_token_limit: int = field(default_factory=lambda: _int("AI_DAILY_TOKEN_LIMIT", 0))
    super_admin_names: list[str] = field(default_factory=lambda: _csv("SUPER_ADMIN_NAMES", ["高贺雅姝"]))
    business_admin_names: list[str] = field(default_factory=lambda: _csv("BUSINESS_ADMIN_NAMES", ["lantao"]))

    db_path: Path = field(default_factory=lambda: (BASE_DIR / os.environ.get("DB_PATH", "./data/hh.db")).resolve())
    upload_dir: Path = field(default_factory=lambda: (BASE_DIR / os.environ.get("UPLOAD_DIR", "./data/uploads")).resolve())
    temp_resume_file_retention_days: int = field(default_factory=lambda: _int("TEMP_RESUME_FILE_RETENTION_DAYS", 30))
    temp_resume_text_retention_days: int = field(default_factory=lambda: _int("TEMP_RESUME_TEXT_RETENTION_DAYS", 30))
    match_queue_enabled: bool = field(default_factory=lambda: _bool("MATCH_QUEUE_ENABLED", True))
    match_queue_workers: int = field(default_factory=lambda: _int("MATCH_QUEUE_WORKERS", 1))
    match_queue_poll_seconds: int = field(default_factory=lambda: _int("MATCH_QUEUE_POLL_SECONDS", 2))
    match_global_concurrency: int = field(default_factory=lambda: _int("MATCH_GLOBAL_CONCURRENCY", 2))
    match_run_stale_minutes: int = field(default_factory=lambda: _int("MATCH_RUN_STALE_MINUTES", 120))
    public_base_url: str = field(default_factory=lambda: os.environ.get("APP_PUBLIC_URL", "").strip().rstrip("/"))
    cors_allow_origins: list[str] = field(default_factory=lambda: _csv(
        "CORS_ALLOW_ORIGINS",
        [origin] if (origin := _origin(os.environ.get("APP_PUBLIC_URL", ""))) else [],
    ))
    frontend_dist_dir: Path = field(default_factory=lambda: (
        Path(os.environ["FRONTEND_DIST_DIR"]).expanduser()
        if os.environ.get("FRONTEND_DIST_DIR")
        else BASE_DIR.parent / "frontend" / "dist"
    ).resolve())

    feishu_app_id: str = field(default_factory=lambda: os.environ.get("FEISHU_APP_ID", "").strip())
    feishu_app_secret: str = field(default_factory=lambda: os.environ.get("FEISHU_APP_SECRET", "").strip())
    feishu_base_token: str = field(default_factory=lambda: (
        os.environ.get("FEISHU_BASE_TOKEN", "") or os.environ.get("BITABLE_APP_TOKEN", "")
    ).strip().removeprefix("base/"))
    feishu_table_companies: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_COMPANIES", "").strip().rstrip("&"))
    feishu_table_users: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_USERS", "").strip().rstrip("&"))
    feishu_table_projects: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_PROJECTS", "").strip().rstrip("&"))
    feishu_table_positions: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_POSITIONS", "").strip().rstrip("&"))
    feishu_table_prompts: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_PROMPTS", "").strip().rstrip("&"))
    feishu_table_job_labels: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_JOB_LABELS", "").strip().rstrip("&"))
    feishu_table_match_results: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_MATCH_RESULTS", "").strip().rstrip("&"))
    feishu_table_client_recommendations: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_CLIENT_RECOMMENDATIONS", "").strip().rstrip("&"))
    feishu_table_chat_messages: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_CHAT_MESSAGES", "").strip().rstrip("&"))
    feishu_table_tracking: str = field(default_factory=lambda: os.environ.get("FEISHU_TABLE_TRACKING", "").strip().rstrip("&"))
    feishu_sync_enabled: bool = field(default_factory=lambda: _bool("FEISHU_SYNC_ENABLED", True))
    feishu_sync_strict: bool = field(default_factory=lambda: _bool("FEISHU_SYNC_STRICT", False))

    # 阈值：人工审核 / 重点推荐。对外口径统一为 80/90 分。
    review_threshold: int = 80
    top_threshold: int = 90


settings = Settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)
settings.db_path.parent.mkdir(parents=True, exist_ok=True)
