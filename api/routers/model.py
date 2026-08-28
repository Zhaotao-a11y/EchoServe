"""
EchoServe P1 — 模型管理 API 路由
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_context, verify_token
from core.context import BaizeContext

logger = logging.getLogger("echoserve.api.model")

router = APIRouter()


# ─── 请求/响应模型 ──────────────────────────────────

class SwitchModelRequest(BaseModel):
    model_id: str
    lora_adapter: (str | None) = None


class LoadAdapterRequest(BaseModel):
    adapter_name: str


# ─── 模型列表 ────────────────────────────────────────

@router.get("/model/list")
async def list_models(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """列出所有可用模型（基础模型 + LoRA adapters）"""
    manager = ctx.inject("model_manager")
    if not manager:
        raise HTTPException(status_code=503, detail="模型管理器未就绪")
    return {"models": manager.list_models()}


@router.get("/model/status")
async def get_model_status(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """获取当前模型状态"""
    manager = ctx.inject("model_manager")
    if not manager:
        raise HTTPException(status_code=503, detail="模型管理器未就绪")
    return manager.get_status()


# ─── 模型切换 ────────────────────────────────────────

@router.post("/model/switch")
async def switch_model(
    req: SwitchModelRequest,
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """
    切换模型（热切换，无需重启）。

    - 如果目标模型已在 vLLM 中加载，直接切换
    - 否则触发 vLLM 加载新模型
    """
    manager = ctx.inject("model_manager")
    if not manager:
        raise HTTPException(status_code=503, detail="模型管理器未就绪")

    result = await manager.switch_model(
        model_id=req.model_id,
        use_lora=req.lora_adapter,
    )

    if result.get("status") == "failed":
        raise HTTPException(
            status_code=400,
            detail=result.get("reason", "模型切换失败"),
        )

    return result


# ─── LoRA Adapter 管理 ───────────────────────────────

@router.post("/model/adapter/load")
async def load_adapter(
    req: LoadAdapterRequest,
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """加载指定的 LoRA adapter"""
    manager = ctx.inject("model_manager")
    if not manager:
        raise HTTPException(status_code=503, detail="模型管理器未就绪")

    result = manager.load_adapter(req.adapter_name)
    if result.get("status") == "failed":
        raise HTTPException(status_code=400, detail=result.get("reason"))
    return result


@router.post("/model/adapter/unload")
async def unload_adapter(
    req: LoadAdapterRequest,
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """卸载指定的 LoRA adapter"""
    manager = ctx.inject("model_manager")
    if not manager:
        raise HTTPException(status_code=503, detail="模型管理器未就绪")

    result = manager.unload_adapter(req.adapter_name)
    if result.get("status") == "failed":
        raise HTTPException(status_code=400, detail=result.get("reason"))
    return result


@router.get("/model/adapter/list")
async def list_adapters(ctx: BaizeContext = Depends(get_context)):
    """列出所有已注册的 LoRA adapters"""
    manager = ctx.inject("model_manager")
    if not manager:
        raise HTTPException(status_code=503, detail="模型管理器未就绪")
    adapters = manager.list_models()
    lora_only = [a for a in adapters if a.get("type") == "lora"]
    return {"adapters": lora_only}
