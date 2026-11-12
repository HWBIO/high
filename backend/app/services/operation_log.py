import json
from typing import Any, Optional


def _actor_value(actor, key: str):
    if hasattr(actor, "get"):
        return actor.get(key)
    try:
        return actor[key]
    except (KeyError, TypeError, IndexError):
        return None


def record(
    conn,
    actor: dict,
    action: str,
    entity_type: str,
    entity_id: int,
    entity_name: str = "",
    detail: Optional[dict[str, Any]] = None,
) -> None:
    conn.execute(
        "INSERT INTO operation_logs"
        " (actor_user_id, actor_name, actor_role, action, entity_type, entity_id, entity_name, detail_json)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            _actor_value(actor, "id"),
            _actor_value(actor, "display_name") or _actor_value(actor, "username") or "",
            _actor_value(actor, "role") or "",
            action,
            entity_type,
            entity_id,
            entity_name,
            json.dumps(detail or {}, ensure_ascii=False),
        ),
    )
