from fastapi import APIRouter, Depends

from ..db import get_conn
from ..services import temp_data
from .auth import require_business

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.get("/temp-resumes")
def get_temp_resume_stats(_user=Depends(require_business)):
    conn = get_conn()
    return temp_data.temp_resume_stats(conn)


@router.post("/temp-resumes/cleanup")
def cleanup_temp_resumes(_user=Depends(require_business)):
    conn = get_conn()
    result = temp_data.cleanup_expired_temp_resume_payloads(conn)
    orphan_result = temp_data.cleanup_orphan_upload_files(conn)
    return {**result, **orphan_result, "stats": temp_data.temp_resume_stats(conn)}


@router.post("/temp-resumes/cleanup-orphans")
def cleanup_orphan_upload_files(_user=Depends(require_business)):
    conn = get_conn()
    result = temp_data.cleanup_orphan_upload_files(conn)
    return {**result, "stats": temp_data.temp_resume_stats(conn)}
