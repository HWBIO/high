from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..db import get_conn
from .auth import require_user

router = APIRouter(prefix="/api/candidates", tags=["candidates"])

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
}

@router.get("/{candidate_id}/resume")
def download_resume(candidate_id: int, user=Depends(require_user)):
    """简历附件：浏览器内联预览 PDF。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT name, resume_file FROM candidates WHERE id=?",
        (candidate_id,),
    ).fetchone()
    if not row or not row["resume_file"]:
        raise HTTPException(404, "该候选人没有简历附件")
    path = Path(row["resume_file"])
    if not path.exists():
        raise HTTPException(404, "简历文件已丢失")
    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        filename=f"{row['name']}_简历{path.suffix}",
        content_disposition_type="inline",
    )
