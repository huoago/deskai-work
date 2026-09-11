from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__

from app.api.artifacts import router as artifacts_router
from app.api.chat import router as chat_router
from app.api.errors import AppError, app_error_handler
from app.api.file_operation_batches import router as file_operation_batches_router
from app.api.file_operations import router as file_operations_router
from app.api.files import router as files_router
from app.api.health import router as health_router
from app.api.knowledge import router as knowledge_router
from app.api.memory import router as memory_router
from app.api.providers import router as providers_router
from app.api.recycle import router as recycle_router
from app.api.recycle_batches import router as recycle_batches_router
from app.api.recovery import router as recovery_router
from app.api.roots import router as roots_router
from app.api.settings import router as settings_router
from app.api.source_edit_batches import router as source_edit_batches_router
from app.api.source_edits import router as source_edits_router
from app.api.tasks import router as tasks_router
from app.api.workspaces import router as workspace_router
from app.agent.orchestrator import AgentOrchestrator
from app.agent.tools import ToolRegistry
from app.agent.worker import AgentWorker
from app.ai.provider import OpenAIChatProvider
from app.analysis.service import DataAnalysisService
from app.artifacts.service import ArtifactService
from app.core.config import Settings
from app.database.migrate import run_migrations
from app.database.session import Database
from app.file_ops.batch_service import FileOrganizationBatchService
from app.file_ops.service import FileOrganizationService
from app.recycle.batch_service import FileRecycleBatchService
from app.recycle.service import FileRecycleService
from app.indexing.watcher import WorkspaceWatcher
from app.knowledge.search import HybridSearch
from app.knowledge.service import KnowledgeIndexer
from app.memory.service import MemoryService
from app.memory.worker import MemoryWorker
from app.parsing.service import ParserWorker
from app.security.secrets import SecretStore
from app.source_edits.batch_service import SourceFileEditBatchService
from app.source_edits.service import SourceFileEditService
from app.web.service import WebResearchService

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
        memory_service = MemoryService(app.state.database)
        app.state.memory_service = memory_service
        memory_worker = MemoryWorker(
            app.state.database,
            memory_service,
            app.state.secret_store,
            app.state.openai_provider,
            interval_seconds=resolved.memory_worker_interval_seconds,
        )
        app.state.memory_worker = memory_worker
        artifact_service = ArtifactService(app.state.database, resolved.data_dir)
        app.state.artifact_service = artifact_service
        analysis_service = DataAnalysisService(app.state.database, parser_worker.cache)
        app.state.analysis_service = analysis_service
        web_research_service = WebResearchService(
            app.state.database,
            app.state.secret_store,
            app.state.openai_provider,
        )
        app.state.web_research_service = web_research_service
        source_edit_service = SourceFileEditService(app.state.database, resolved.data_dir)
        app.state.source_edit_service = source_edit_service
        source_edit_batch_service = SourceFileEditBatchService(
            app.state.database,
            source_edit_service,
        )
        app.state.source_edit_batch_service = source_edit_batch_service
        source_edit_batch_service.recover_incomplete_batches()
        file_organization_service = FileOrganizationService(
            app.state.database,
            source_edit_service,
        )
        app.state.file_organization_service = file_organization_service
        file_organization_batch_service = FileOrganizationBatchService(
            app.state.database,
            file_organization_service,
        )
        app.state.file_organization_batch_service = file_organization_batch_service
        file_organization_batch_service.recover_incomplete_batches()
        file_organization_service.recover_incomplete_operations()
        file_recycle_service = FileRecycleService(
            app.state.database,
            resolved.data_dir,
            source_edit_service,
        )
        app.state.file_recycle_service = file_recycle_service
        file_recycle_batch_service = FileRecycleBatchService(
            app.state.database,
            file_recycle_service,
        )
        app.state.file_recycle_batch_service = file_recycle_batch_service
        file_recycle_batch_service.recover_incomplete_batches()
        file_recycle_service.recover_incomplete_operations()
        tool_registry = ToolRegistry(
            app.state.database,
            app.state.hybrid_search,
            memory_service,
            parser_worker.cache,
            artifact_service,
            analysis_service,
            web_research_service,
            source_edit_service,
            source_edit_batch_service,
            file_organization_service,
            file_organization_batch_service,
            file_recycle_service,
            file_recycle_batch_service,
        )
        app.state.tool_registry = tool_registry
        agent_orchestrator = AgentOrchestrator(
            app.state.database,
            app.state.secret_store,
            app.state.openai_provider,
            tool_registry,
        )
        app.state.agent_orchestrator = agent_orchestrator
        agent_worker = AgentWorker(
            app.state.database,
            agent_orchestrator,
            interval_seconds=resolved.agent_worker_interval_seconds,
        )
        app.state.agent_worker = agent_worker
        if resolved.watcher_enabled:
            watcher.start()
        if resolved.parser_worker_enabled:
            parser_worker.start()
        if resolved.knowledge_worker_enabled:
            knowledge_indexer.start()
        if resolved.memory_worker_enabled:
            memory_worker.start()
        if resolved.agent_worker_enabled:
            agent_worker.start()
        try:
            yield
        finally:
            agent_worker.stop()
            memory_worker.stop()
            knowledge_indexer.stop()
            parser_worker.stop()
            watcher.stop()
            app.state.database.dispose()

    app = FastAPI(
        title="DeskAI Engine",
        version=__version__,
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
    app.include_router(artifacts_router)
    app.include_router(workspace_router)
    app.include_router(roots_router)
    app.include_router(file_operation_batches_router)
    app.include_router(file_operations_router)
    app.include_router(files_router)
    app.include_router(knowledge_router)
    app.include_router(memory_router)
    app.include_router(providers_router)
    app.include_router(recycle_router)
    app.include_router(recycle_batches_router)
    app.include_router(recovery_router)
    app.include_router(chat_router)
    app.include_router(settings_router)
    app.include_router(source_edit_batches_router)
    app.include_router(source_edits_router)
    app.include_router(tasks_router)
    return app
