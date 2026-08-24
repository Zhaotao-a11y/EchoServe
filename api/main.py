"""
EchoServe V0.1.0 — FastAPI 主入口（P2 完整版）

启动顺序：
    1. 加载配置
    2. 创建 BaizeContext
    3. 发现并注册插件（按依赖顺序）
    4. 启动 FiberManager（按依赖顺序初始化插件）
    5. 暴露 HTTP API + Prometheus /metrics + OAuth 回调
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from core.context import BaizeContext
from core.fiber import FiberManager
from core.plugin_loader import PluginLoader

logger = logging.getLogger("echoseve.main")

# ─── P0 插件 ──────────────────────────────────────
from plugins.auth.plugin import AuthPlugin
from plugins.audit.plugin import AuditPlugin
from plugins.retriever.plugin import RetrieverPlugin
from plugins.llm.plugin import LLMPlugin
from plugins.knowledge.plugin import KnowledgePlugin
from plugins.chat.plugin import ChatPlugin
from plugins.channel_wechat.plugin import WeChatChannelPlugin
from plugins.channel_wechat_kf.plugin import WeChatKFPlugin

# ─── P1 插件 ──────────────────────────────────────
from plugins.model_manager.plugin import ModelManagerPlugin
from plugins.evolve.plugin import ModelEvolvePlugin
from plugins.monitoring.plugin import MonitoringPlugin

# ─── P2 插件（新增）──────────────────────────────
from plugins.auth_enterprise.plugin import EnterpriseAuthPlugin    # LDAP + OAuth2
from plugins.channel_whatsapp.plugin import WhatsAppChannelPlugin  # WhatsApp

# ─── 路由导入 ──────────────────────────────────────
from api.routers import auth as auth_router
from api.routers import audit as audit_router
from api.routers import knowledge as knowledge_router
from api.routers import chat as chat_router
from api.routers import model as model_router
from api.routers import evolve as evolve_router
from api.routers import metrics as metrics_router
from api.routers import settings as settings_router

# ─── 全局状态 ──────────────────────────────────────
ctx: BaizeContext = None
fiber_manager: FiberManager = None
loader: PluginLoader = None

# 认证依赖（在 ctx 定义后导入，避免循环引用）
from api.deps import verify_token  # noqa: E402


# ─── 生命周期 ──────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ctx, fiber_manager, loader

    logging.basicConfig(
        level=getattr(logging, settings.log.level.upper(), logging.INFO),
        format="%(levelname)s %(name)s - %(message)s",
    )
    logger = logging.getLogger("echoseve.main")

    # 安全校验：JWT Secret、CORS 等
    settings.validate_security()

    logger.info("=" * 60)
    logger.info("  EchoServe V0.1.0 — Starting...")
    logger.info("  Features: Auth | Audit | RAG+Rerank | Chat | WeChat |")
    logger.info("            ModelMgr | Evolve | Monitor | WhatsApp | EnterpriseAuth")
    logger.info("=" * 60)

    # 1. 创建 Context
    ctx = BaizeContext(settings)
    logger.info("[Main] BaizeContext created")

    # 1.5 创建插件共享路由器，供插件 on_init 中注册 webhook 等路由
    plugin_router = APIRouter()
    ctx.provide("http_router", plugin_router)
    logger.info("[Main] Provided http_router to context")

    # 2. 创建 FiberManager
    fiber_manager = FiberManager(ctx)

    # 3. 注册插件（顺序即依赖顺序）
    loader = PluginLoader(ctx, fiber_manager)

    # P0 插件
    loader.register(AuthPlugin)              # 认证（无依赖）
    loader.register(AuditPlugin)             # 审计（无依赖）
    loader.register(RetrieverPlugin)         # 检索（依赖 config）
    loader.register(LLMPlugin)               # LLM（依赖 config）
    loader.register(KnowledgePlugin)         # 知识库（依赖 retriever）
    loader.register(ChatPlugin)              # 对话（依赖 llm/knowledge/retriever）
    loader.register(WeChatChannelPlugin)     # 企业微信（依赖 chat/auth）
    loader.register(WeChatKFPlugin)          # 微信客服（依赖 chat/auth）

    # P1 插件
    loader.register(ModelManagerPlugin)      # 模型管理（依赖 config）
    loader.register(MonitoringPlugin)        # 监控（依赖 config）
    loader.register(ModelEvolvePlugin)       # 进化引擎（依赖 model/knowledge/llm）

    # P2 插件（新增）
    loader.register(EnterpriseAuthPlugin)    # 企业认证 LDAP/OAuth（依赖 auth）
    loader.register(WhatsAppChannelPlugin)  # WhatsApp（依赖 chat/auth）

    loader.load_all()
    logger.info(f"[Main] Plugins registered: {loader.get_plugin_ids()}")

    # 4. 启动所有插件
    await fiber_manager.start_all()

    # 4.5 将插件注册的路由挂载到 FastAPI app
    app.include_router(plugin_router)
    logger.info("[Main] Plugin routes mounted to app")
    
    # 4.6 注册 P1 设置路由（必须在 SPA catch-all 之前注册，否则会被 catch-all 拦截）
    app.include_router(settings_router.router, prefix="/api", tags=["系统设置"])
    logger.info("[Main] Settings router mounted to /api")
    
    # 4.7 将 SPA catch-all 移到路由列表末尾，确保插件 webhook 路由优先匹配
    for i, route in enumerate(app.routes):
        if hasattr(route, 'name') and route.name == 'spa_catch_all':
            catch_all = app.routes.pop(i)
            app.routes.append(catch_all)
            logger.info("[Main] SPA catch-all moved to end of route list")
            break
    
    # 5. 注入到 app state
    app.state.ctx = ctx
    app.state.fiber_manager = fiber_manager

    logger.info("=" * 60)
    logger.info("  EchoServe V0.1.0 — Ready!")
    logger.info(f"  API: http://{settings.api.host}:{settings.api.port}")
    logger.info(f"  Docs: http://{settings.api.host}:{settings.api.port}/docs")
    logger.info(f"  Metrics: http://{settings.api.host}:{settings.api.port}/metrics")
    logger.info("=" * 60)

    yield

    # ─── 关闭流程 ────────────────────────────────
    logger.info("[Main] Shutting down...")
    await fiber_manager.stop_all()
    await fiber_manager.destroy_all()
    logger.info("[Main] Shutdown complete")


# ─── FastAPI 应用 ──────────────────────────────────────

app = FastAPI(
    title="EchoServe V0.1.0",
    description="企业级本地知识库问答系统 — P2 完整版（含 WhatsApp + LDAP/OAuth + DPO + 等保合规）",
    version="0.1.2",
    lifespan=lifespan,
)

# CORS 中间件 — 生产环境必须配置明确白名单
_cors_raw = settings.api.cors_origins.strip()
if _cors_raw == "*" or not _cors_raw:
    # 开发模式：允许所有源，但不携带凭证（安全折中）
    _cors_origins = ["*"]
    _cors_credentials = False
    logger.warning(
        "[Main] CORS allow_origins=* (dev mode), "
        "credentials disabled. Set CORS_ORIGINS for production."
    )
else:
    _cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    _cors_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def security_headers_middleware(request, call_next):
    """添加安全响应头"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    return response

# ─── 注册路由 ──────────────────────────────────────

# P0 路由
app.include_router(auth_router.router,        prefix="/api", tags=["认证"])
app.include_router(audit_router.router,       prefix="/api", tags=["审计"])
app.include_router(knowledge_router.router,   prefix="/api", tags=["知识库"])
app.include_router(chat_router.router,        prefix="/api", tags=["对话"])

# P1 路由
app.include_router(model_router.router,        prefix="/api", tags=["模型管理"])
app.include_router(evolve_router.router,       prefix="/api", tags=["模型进化"])
app.include_router(metrics_router.router,      prefix="",      tags=["监控"])

# P2 路由（新增）
# 企业认证路由（OAuth2 回调、LDAP 同步）由 EnterpriseAuthPlugin 内部注册
# WhatsApp 路由由 WhatsAppChannelPlugin 内部注册

# ─── 健康检查 ──────────────────────────────────────

@app.get("/health")
async def health_check():
    """健康检查端点"""
    if not ctx:
        return {"status": "starting", "plugins": {}}

    health = {
        "status": "healthy",
        "version": "0.1.2",
        "plugins": fiber_manager.health_check(),
    }
    return health


@app.get("/ready")
async def readiness_check():
    """就绪检查"""
    if not fiber_manager:
        raise HTTPException(status_code=503, detail="Service not ready")

    states = fiber_manager.health_check()
    all_started = all(s == "started" for s in states.values())

    if not all_started:
        raise HTTPException(status_code=503, detail=f"Plugins not ready: {states}")

    return {"status": "ready", "plugins": states}


# ─── P2 合规检查端点 ──────────────────────────────────────

@app.get("/api/compliance/check")
async def compliance_check_endpoint(user_id: str = Depends(verify_token)):
    """
    触发等保 2.0 三级合规检查。
    返回合规评分和详细结果。
    """
    import sys
    from pathlib import Path

    # 动态导入合规检查器
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    try:
        from compliance_check import ComplianceChecker

        checker = ComplianceChecker(
            project_root=str(Path(__file__).parent.parent),
            output_dir=str(Path(__file__).parent.parent / "reports"),
        )
        report = checker.run_full_check()

        # 生成报告文件
        paths = checker.generate_report(report)

        return {
            "status": "success",
            "overall_score": report["overall_score"],
            "grade": report["grade"],
            "summary": report["summary"],
            "recommendations_count": len(report["recommendations"]),
            "reports": paths,
        }
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Compliance module error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Compliance check failed: {e}")


# ─── DPO 偏好反馈端点 ──────────────────────────────────────

@app.post("/api/feedback")
async def record_feedback(request: dict, user_id: str = Depends(verify_token)):
    """
    记录用户对回答的偏好反馈（用于 DPO 训练）。

    Body:
        prompt: 用户原始问题
        response: 系统给出的回答
        feedback_type: "like" | "dislike" | "edit"
        edited_response: 编辑后的回答（仅 feedback_type="edit" 时）

    V0.2.0: 使用插件持有的 PreferenceStore 单例，
            偏好数据达阈值时自动触发 DPO 数据构建（P1-A）。
    """
    from api.deps import get_context
    ctx = get_context()
    store = ctx.inject("preference_store", None) if ctx else None

    if store is None:
        # 降级：创建临时实例（无自动触发能力）
        from pathlib import Path
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "evolve"))
        from dpo_trainer import PreferenceStore
        store = PreferenceStore(
            store_path=str(Path(__file__).parent.parent / "data" / "training" / "preferences.jsonl")
        )

    try:
        pref_id = store.record_feedback(
            prompt=request.get("prompt", ""),
            response=request.get("response", ""),
            feedback_type=request.get("feedback_type", "like"),
            user_id=request.get("user_id", "anonymous"),
            edited_response=request.get("edited_response"),
        )

        return {
            "status": "success",
            "preference_id": pref_id,
            "stats": store.get_stats(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/feedback/stats")
async def feedback_stats(user_id: str = Depends(verify_token)):
    """获取偏好数据统计"""
    from api.deps import get_context
    ctx = get_context()
    store = ctx.inject("preference_store", None) if ctx else None

    if store is None:
        from pathlib import Path
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "evolve"))
        from dpo_trainer import PreferenceStore
        store = PreferenceStore(
            store_path=str(Path(__file__).parent.parent / "data" / "training" / "preferences.jsonl")
        )

    try:
        return store.get_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evolve/dpo/build")
async def build_dpo_dataset(request: dict = None, user_id: str = Depends(verify_token)):
    """
    构建 DPO 训练数据集。
    将收集的偏好数据转换为 (prompt, chosen, rejected) 格式。
    """
    from api.deps import get_context
    ctx = get_context()
    store = ctx.inject("preference_store", None) if ctx else None

    if store is None:
        from pathlib import Path
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "evolve"))
        from dpo_trainer import PreferenceStore
        store = PreferenceStore(
            store_path=str(Path(__file__).parent.parent / "data" / "training" / "preferences.jsonl")
        )

    from pathlib import Path
    output_path = str(Path(__file__).parent.parent / "data" / "training" / "dpo_dataset.jsonl")
    try:
        result = store.build_dpo_dataset(output_path=output_path)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evolve/dpo/train")
async def trigger_dpo_train(request: dict = None, user_id: str = Depends(verify_token)):
    """
    触发 DPO 训练。
    生成训练脚本并返回执行命令。
    """
    from pathlib import Path
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "evolve"))
    try:
        from dpo_trainer import DPOTrainer

        base_model = (request or {}).get("base_model", "./models/qwen3-14b-q4")
        output_dir = (request or {}).get(
            "output_dir",
            str(Path(__file__).parent.parent / "models" / "adapters" / f"dpo-{int(time.time())}")
        )

        trainer = DPOTrainer(
            base_model=base_model,
            dpo_data=str(Path(__file__).parent.parent / "data" / "training" / "dpo_dataset.jsonl"),
            output_dir=output_dir,
        )

        result = trainer.train()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Windows 安装包构建端点 ──────────────────────────────────────

@app.post("/api/admin/build-windows-installer")
async def build_windows_installer(request: dict = None, user_id: str = Depends(verify_token)):
    """
    生成 Windows 安装包构建文件。
    返回 Inno Setup 脚本、启动脚本等文件路径。
    """
    from pathlib import Path
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    try:
        from build_windows_installer import WindowsInstallerBuilder

        params = request or {}
        builder = WindowsInstallerBuilder(
            version=params.get("version", "0.1.0"),
            output_dir=str(Path(__file__).parent.parent / "build"),
            app_id=params.get("app_id", "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"),
        )

        result = builder.build_all()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 前端静态资源 ──────────────────────────────────────

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

WEB_DIST = Path(__file__).parent.parent / "web" / "dist"

if WEB_DIST.exists():
    # Mount assets directory for static resources (JS, CSS, images)
    assets_dir = WEB_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    
    # Mount other static files (favicon.ico, logo.jpg, etc.)
    app.mount("/static", StaticFiles(directory=str(WEB_DIST)), name="static")
    
    # SPA catch-all: return index.html for all non-API paths
    # This enables BrowserRouter to work on page refresh
    @app.get("/{full_path:path}", response_class=FileResponse)
    async def spa_catch_all(full_path: str):
        """
        SPA catch-all route.
        Returns index.html for all non-API paths so that BrowserRouter works on refresh.
        API routes (/api/*, /docs, /openapi.json, /metrics) are registered before
        this catch-all and have higher priority due to exact path matching.
        """
        index_html = WEB_DIST / "index.html"
        if index_html.exists():
            return FileResponse(str(index_html))
        raise HTTPException(status_code=404, detail="Not Found")
    
    logger.info(f"[Main] Serving static files from {WEB_DIST}")
else:
    logger.warning("[Main] web/dist not found - frontend not served")
    
    @app.get("/")
    async def root_fallback():
        return {"detail": "Frontend not built. Run 'cd web && npm run build' first."}

# ─── 入口 ──────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=settings.api.debug,
    )
