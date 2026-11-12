import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from ..config import settings


class FeishuConfigError(RuntimeError):
    pass


class FeishuAPIError(RuntimeError):
    pass


@dataclass
class FeishuTableConfig:
    companies: str
    users: str
    projects: str
    positions: str
    prompts: str
    job_labels: str
    match_results: str
    client_recommendations: str
    chat_messages: str
    tracking: str

    def as_dict(self) -> dict[str, str]:
        return {
            "companies": self.companies,
            "users": self.users,
            "projects": self.projects,
            "positions": self.positions,
            "prompts": self.prompts,
            "job_labels": self.job_labels,
            "match_results": self.match_results,
            "client_recommendations": self.client_recommendations,
            "chat_messages": self.chat_messages,
            "tracking": self.tracking,
        }


def table_config() -> FeishuTableConfig:
    return FeishuTableConfig(
        companies=settings.feishu_table_companies,
        users=settings.feishu_table_users,
        projects=settings.feishu_table_projects,
        positions=settings.feishu_table_positions,
        prompts=settings.feishu_table_prompts,
        job_labels=settings.feishu_table_job_labels,
        match_results=settings.feishu_table_match_results,
        client_recommendations=settings.feishu_table_client_recommendations,
        chat_messages=settings.feishu_table_chat_messages,
        tracking=settings.feishu_table_tracking,
    )


def config_status() -> dict[str, Any]:
    tables = table_config().as_dict()
    return {
        "app_id_configured": bool(settings.feishu_app_id),
        "app_secret_configured": bool(settings.feishu_app_secret),
        "base_token_configured": bool(settings.feishu_base_token),
        "sync_enabled": settings.feishu_sync_enabled,
        "sync_strict": settings.feishu_sync_strict,
        "tables": {name: {"configured": bool(table_id), "table_id": table_id} for name, table_id in tables.items()},
        "ready": bool(settings.feishu_app_id and settings.feishu_app_secret and settings.feishu_base_token and all(tables.values())),
    }


class FeishuClient:
    def __init__(self):
        self.base_url = "https://open.feishu.cn/open-apis"
        self._token = ""
        self._token_expire_at = 0.0
        self._field_cache: dict[str, set[str]] = {}

    def _require_config(self):
        missing = []
        if not settings.feishu_app_id:
            missing.append("FEISHU_APP_ID")
        if not settings.feishu_app_secret:
            missing.append("FEISHU_APP_SECRET")
        if not settings.feishu_base_token:
            missing.append("FEISHU_BASE_TOKEN")
        if missing:
            raise FeishuConfigError(f"飞书配置缺失：{', '.join(missing)}")

    def tenant_access_token(self) -> str:
        self._require_config()
        now = time.time()
        if self._token and now < self._token_expire_at - 60:
            return self._token
        resp = requests.post(
            f"{self.base_url}/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
            timeout=20,
        )
        data = self._parse_response(resp)
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuAPIError("飞书未返回 tenant_access_token")
        self._token = token
        self._token_expire_at = now + int(data.get("expire") or 7200)
        return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tenant_access_token()}", "Content-Type": "application/json"}

    def _parse_response(self, resp: requests.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError as exc:
            raise FeishuAPIError(f"飞书接口返回非 JSON：HTTP {resp.status_code}") from exc
        code = data.get("code", 0)
        if resp.status_code >= 400 or code not in (0, None):
            message = data.get("msg") or data.get("message") or resp.text[:300]
            raise FeishuAPIError(f"飞书接口失败：HTTP {resp.status_code}, code={code}, msg={message}")
        return data

    def list_records(
        self,
        table_id: str,
        *,
        page_size: int = 100,
        page_token: str = "",
        filter_text: str = "",
        field_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page_size": max(1, min(page_size, 500))}
        if page_token:
            params["page_token"] = page_token
        if filter_text:
            params["filter"] = filter_text
        if field_names:
            params["field_names"] = ",".join(field_names)
        resp = requests.get(
            f"{self.base_url}/bitable/v1/apps/{settings.feishu_base_token}/tables/{table_id}/records",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        return self._parse_response(resp).get("data", {})

    def list_fields(self, table_id: str) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{self.base_url}/bitable/v1/apps/{settings.feishu_base_token}/tables/{table_id}/fields",
            headers=self._headers(),
            params={"page_size": 200},
            timeout=30,
        )
        return self._parse_response(resp).get("data", {}).get("items", [])

    def allowed_field_names(self, table_id: str) -> set[str]:
        if table_id not in self._field_cache:
            self._field_cache[table_id] = {
                field.get("field_name")
                for field in self.list_fields(table_id)
                if field.get("field_name")
            }
        return self._field_cache[table_id]

    def filter_fields(self, table_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        allowed = self.allowed_field_names(table_id)
        return {key: value for key, value in fields.items() if key in allowed}

    def create_records(self, table_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        records = [self.filter_fields(table_id, fields) for fields in records]
        records = [fields for fields in records if fields]
        if not records:
            return {}
        resp = requests.post(
            f"{self.base_url}/bitable/v1/apps/{settings.feishu_base_token}/tables/{table_id}/records/batch_create",
            headers=self._headers(),
            json={"records": [{"fields": fields} for fields in records]},
            timeout=30,
        )
        return self._parse_response(resp).get("data", {})

    def update_record(self, table_id: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        fields = self.filter_fields(table_id, fields)
        if not fields:
            return {}
        resp = requests.put(
            f"{self.base_url}/bitable/v1/apps/{settings.feishu_base_token}/tables/{table_id}/records/{record_id}",
            headers=self._headers(),
            json={"fields": fields},
            timeout=30,
        )
        return self._parse_response(resp).get("data", {})

    def delete_record(self, table_id: str, record_id: str) -> dict[str, Any]:
        resp = requests.delete(
            f"{self.base_url}/bitable/v1/apps/{settings.feishu_base_token}/tables/{table_id}/records/{record_id}",
            headers=self._headers(),
            timeout=30,
        )
        return self._parse_response(resp).get("data", {})


client = FeishuClient()
