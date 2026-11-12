def ensure_company(conn, name: str):
    """按营业执照名称、简称或历史名称查/建公司，返回 company_id。"""
    name = (name or "").strip()
    if not name:
        return None
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(companies)").fetchall()}
    if "legal_name" in cols and "short_name" in cols:
        row = conn.execute(
            "SELECT id FROM companies WHERE legal_name=? OR short_name=? OR name=? ORDER BY id LIMIT 1",
            (name, name, name),
        ).fetchone()
    else:
        row = conn.execute("SELECT id FROM companies WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    if "legal_name" in cols:
        cur = conn.execute("INSERT INTO companies (name, legal_name) VALUES (?,?)", (name, name))
    else:
        cur = conn.execute("INSERT INTO companies (name) VALUES (?)", (name,))
    return cur.lastrowid
