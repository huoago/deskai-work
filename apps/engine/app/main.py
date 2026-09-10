from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.chat import router as chat_router
from app.api.errors import AppError, app_error_handler
from app.api.files import router as files_router
from app.api.health import router as health_router
from app.api.knowledge import router as knowledge_router
from app.api.providers import router as providers_router
from app.api.roots import router as roots_router
from app.api.settings import router as settings_router
from app.api.workspaces import router as workspace_router
from app.ai.provider import OpenAIChatProvider
from app.core.config import Settings
from app.database.migrate import run_migrations
from app.database.session import Database
from app.indexing.watcher import WorkspaceWatcher
from app.knowledge.search import HybridSearch
from app.knowledge.service import KnowledgeIndexer
from app.parsing.service import ParserWorker
from app.security.secrets import SecretStore

ALLOWED_DESKTOP_ORIGINS = [
    "http://127.0.0.1:1420",
    "http://localhost:1420",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.load()
    resolved.ensure_directories()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run_migrations(resolved.database_url)
        app.state.database = Database(resolved.database_url, resolved.database_path)
        app.state.session_token = resolved.session_token
        app.state.secret_store = SecretStore()
        app.state.openai_provider = OpenAIChatProvider()
        watcher = WorkspaceWatcher(
            app.state.database,
            interval_seconds=resolved.watcher_interval_seconds,
        )
        app.state.workspace_watcher = watcher
        parser_worker = ParserWorker(
            app.state.database,
            resolved.data_dir,
            interval_seconds=resolved.parser_worker_interval_seconds,
        )
        app.state.parser_worker = parser_worker
        knowledge_indexer = KnowledgeIndexer(
            app.state.database,
            resolved.data_dir,
            resolved.vector_path,
            interval_seconds=resolved.knowledge_worker_interval_seconds,
        )
        app.state.knowledge_indexer = knowledge_indexer
        app.state.hybrid_search = HybridSearch(
            app.state.database,
            knowledge_indexer.vector_store,
        )
        if resolved.watcher_enabled:
            watcher.start()
        if resolved.parser_worker_enabled:
            parser_worker.start()
        if resolved.knowledge_worker_enabled:
            knowledge_indexer.start()
        try:
            yield
        finally:
            knowledge_indexer.stop()
            parser_worker.stop()
            watcher.stop()
            app.state.database.dispose()

    app = FastAPI(
        title="DeskAI Engine",
        version="0.5.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def require_session_token(request: Request, call_next):
        expected = resolved.session_token
        if expected and request.method != "OPTIONS":
            if request.headers.get("X-DeskAI-Token") != expected:
                return JSONResponse(
                    status_code=401,
                    content={
                        "code": "INVALID_SESSION_TOKEN",
                        "message": "DeskAI local session token is missing or invalid.",
                        "details": {},
                        "recoverable": True,
                    },
                )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_DESKTOP_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-DeskAI-Token"],
    )
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(health_router)
    app.include_router(workspace_router)
    app.include_router(roots_router)
    app.include_router(files_router)
    app.include_router(knowledge_router)
    app.include_router(providers_router)
    app.include_router(chat_router)
    app.include_router(settings_router)
    return app
