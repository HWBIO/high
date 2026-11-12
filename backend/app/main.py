from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import auth, candidates, companies, feishu, maintenance, matching, positions, projects, prompts, tracking
from .services import match_queue, temp_data

app = FastAPI(title="hh-match-agent", description="猎头简历-岗位匹配 Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()
try:
    from .db import get_conn
    temp_data.cleanup_expired_temp_resume_files(get_conn())
    temp_data.cleanup_expired_temp_resume_payloads(get_conn())
    temp_data.cleanup_orphan_upload_files(get_conn())
except Exception:
    # 清理失败不应阻断共享服务启动。
    pass

app.include_router(auth.router)
app.include_router(candidates.router, dependencies=[Depends(auth.require_user)])
app.include_router(companies.router, dependencies=[Depends(auth.require_user)])
app.include_router(feishu.router, dependencies=[Depends(auth.require_user)])
app.include_router(maintenance.router, dependencies=[Depends(auth.require_user)])
app.include_router(positions.router, dependencies=[Depends(auth.require_user)])
app.include_router(projects.router, dependencies=[Depends(auth.require_user)])
app.include_router(prompts.router, dependencies=[Depends(auth.require_user)])
app.include_router(matching.router, dependencies=[Depends(auth.require_user)])
app.include_router(tracking.router, dependencies=[Depends(auth.require_user)])


@app.on_event("startup")
def start_match_queue_workers():
    match_queue.start_workers()


@app.get("/health")
def health():
    return {"ok": True}


if (settings.frontend_dist_dir / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=settings.frontend_dist_dir / "assets"),
        name="frontend-assets",
    )


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(404, "接口不存在")
    index_file = settings.frontend_dist_dir / "index.html"
    if not index_file.is_file():
        raise HTTPException(404, "前端构建产物不存在，请先在 frontend 执行 npm run build")
    requested_file = (settings.frontend_dist_dir / full_path).resolve()
    try:
        requested_file.relative_to(settings.frontend_dist_dir)
    except ValueError as exc:
        raise HTTPException(404, "文件不存在") from exc
    if requested_file.is_file():
        return FileResponse(requested_file)
    return FileResponse(index_file)
