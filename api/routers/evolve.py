"""
EchoServe P1 — 模型进化引擎 API 路由
"""
from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_context, verify_token
from core.context import BaizeContext

logger = logging.getLogger("echoseve.api.evolve")

router = APIRouter()


# ─── 请求模型 ──────────────────────────────────

class TriggerLoraRequest(BaseModel):
    train_data_path: Optional[str] = None
    output_dir: Optional[str] = None


class TriggerFullRequest(BaseModel):
    train_data_path: Optional[str] = None
    output_dir: Optional[str] = None


# ─── 进化状态 ──────────────────────────────────

@router.get("/evolve/status")
async def get_evolve_status(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """获取进化引擎完整状态"""
    evolver = ctx.inject("evolver")
    if not evolver:
        raise HTTPException(status_code=503, detail="进化引擎未就绪")
    return evolver.get_status()


@router.get("/evolve/check")
async def check_evolve(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """
    检查知识库规模，返回进化建议。

    自动判断当前应处于哪个阶段（纯RAG / LoRA / 全参数微调）。
    """
    evolver = ctx.inject("evolver")
    if not evolver:
        raise HTTPException(status_code=503, detail="进化引擎未就绪")
    return evolver.check_and_evolve()


# ─── 触发训练 ──────────────────────────────────

@router.post("/evolve/trigger/lora")
async def trigger_lora(
    req: TriggerLoraRequest = TriggerLoraRequest(),
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """
    触发离线 LoRA 微调。

    训练在独立进程中执行，不阻塞推理服务。
    完成后通过审计日志记录结果。
    """
    evolver = ctx.inject("evolver")
    if not evolver:
        raise HTTPException(status_code=503, detail="进化引擎未就绪")

    result = evolver.trigger_offline_lora(
        train_data_path=req.train_data_path or "./data/training/train.jsonl",
        output_dir=req.output_dir,
    )

    if result.get("status") == "rejected":
        raise HTTPException(status_code=409, detail=result.get("reason"))
    if result.get("status") == "failed":
        raise HTTPException(status_code=500, detail=result.get("reason"))

    return result


@router.post("/evolve/trigger/full")
async def trigger_full(
    req: TriggerFullRequest = TriggerFullRequest(),
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """
    触发全参数微调（阶段三）。

    需要专用 GPU 节点（推荐 A100 40GB+）。
    返回训练命令，需在生产环境手动执行。
    """
    evolver = ctx.inject("evolver")
    if not evolver:
        raise HTTPException(status_code=503, detail="进化引擎未就绪")

    result = evolver.trigger_offline_full_finetune(
        train_data_path=req.train_data_path or "./data/training/train_full.jsonl",
        output_dir=req.output_dir,
    )

    if result.get("status") == "failed":
        raise HTTPException(status_code=400, detail=result.get("reason"))

    return result


# ─── 评估 ──────────────────────────────────

@router.get("/evolve/eval-report")
async def get_eval_report(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """
    获取最新评估报告。

    评估 Pipeline 每周自动运行，此接口返回最近一次结果。
    """
    evolver = ctx.inject("evolver")
    if not evolver:
        raise HTTPException(status_code=503, detail="进化引擎未就绪")

    status = evolver.get_status()
    report = status.get("last_report")
    if not report:
        return {"message": "暂无评估报告，请先运行评估"}
    return report


@router.post("/evolve/evaluate")
async def run_evaluation(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """
    手动触发评估。

    使用当前模型在测试集上运行评估，返回准确率报告。
    """
    evolver = ctx.inject("evolver")
    if not evolver:
        raise HTTPException(status_code=503, detail="进化引擎未就绪")

    # 获取当前推理函数
    chat = ctx.inject("chat_manager")
    if not chat:
        raise HTTPException(status_code=503, detail="对话管理器未就绪")

    import time

    def predict(question: str) -> str:
        """同步预测包装"""
        try:
            loop = __import__("asyncio").new_event_loop()
            __import__("asyncio").set_event_loop(loop)
            result = loop.run_until_complete(
                chat.chat(session_id=f"eval_{int(time.time())}", user_message=question)
            )
            return result.get("reply", "")
        except Exception as e:
            logger.warning(f"Evaluation predict() failed for question '{question[:50]}': {e}")
            return ""

    report = evolver.run_evaluation(predict)
    return report


# ─── A/B 测试 ──────────────────────────────────

@router.get("/evolve/ab-test")
async def get_ab_test_result(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """获取最近一次 A/B 测试结果"""
    evolver = ctx.inject("evolver")
    if not evolver:
        raise HTTPException(status_code=503, detail="进化引擎未就绪")

    # 从报告目录读取最新的 A/B 报告
    import json
    from pathlib import Path

    report_dir = Path("./data/training/reports")
    if not report_dir.exists():
        return {"message": "暂无 A/B 测试报告"}

    ab_files = sorted(report_dir.glob("ab_test_*.json"))
    if not ab_files:
        return {"message": "暂无 A/B 测试报告"}

    latest = ab_files[-1]
    with open(latest, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── 数据构建 ──────────────────────────────────

@router.post("/evolve/build-data")
async def build_training_data(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """
    从知识库构建训练数据。

    提取 QA 对 → LLM 生成同义变体 → 混入通用数据 → 输出 JSONL
    """
    evolver = ctx.inject("evolver")
    if not evolver:
        raise HTTPException(status_code=503, detail="进化引擎未就绪")

    kb = ctx.inject("knowledge_base")
    llm = ctx.inject("llm")

    from plugins.evolve.data_builder import TrainingDataBuilder

    builder = TrainingDataBuilder(
        knowledge_base=kb,
        llm_client=llm,
        output_path="./data/training/train.jsonl",
    )

    output_path = builder.build()
    validation = builder.validate(output_path)

    return {
        "output_path": output_path,
        "validation": validation,
    }


# ─── P2: 闭环监控 ──────────────────────────────────

@router.get("/loop/status")
async def get_loop_status(
    ctx: BaizeContext = Depends(get_context),
    _user: str = Depends(verify_token),
):
    """
    获取数据回流闭环的完整状态。

    聚合以下组件的状态：
      - AuditToTrainingConverter: 审计日志 → 训练数据转换
      - SessionMiner: 会话历史 → SFT 数据挖掘
      - PreferenceStore: 偏好反馈 → DPO 数据
      - EvaluationPipeline: 模型评估
      - ModelEvolvePlugin: 训练 + promote 状态

    返回各组件的统计信息和整体闭环健康度。
    """
    import json
    import os
    from pathlib import Path

    root_dir = Path(ctx.root_dir) if hasattr(ctx, "root_dir") else Path(".")
    training_dir = root_dir / "data" / "training"

    status: dict = {
        "loop_version": "0.2.0",
        "components": {},
        "pipeline_health": "unknown",
    }

    # 1. AuditToTrainingConverter 状态
    audit_state_path = training_dir / "audit_converter_state.json"
    audit_pool_path = training_dir / "training_pool.jsonl"
    audit_stats = {
        "enabled": audit_state_path.exists() or audit_pool_path.exists(),
        "checkpoint": None,
        "training_pool_count": 0,
    }
    if audit_state_path.exists():
        try:
            with open(audit_state_path, "r", encoding="utf-8") as f:
                audit_stats["checkpoint"] = json.load(f)
        except Exception:
            pass
    if audit_pool_path.exists():
        try:
            with open(audit_pool_path, "r", encoding="utf-8") as f:
                audit_stats["training_pool_count"] = sum(1 for line in f if line.strip())
        except Exception:
            pass
    status["components"]["audit_to_training"] = audit_stats

    # 2. SessionMiner 状态
    miner_state_path = training_dir / "session_miner_state.json"
    mined_path = training_dir / "session_mined.jsonl"
    miner_stats = {
        "enabled": miner_state_path.exists() or mined_path.exists(),
        "processed_sessions": 0,
        "mined_samples_count": 0,
    }
    if miner_state_path.exists():
        try:
            with open(miner_state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
                processed = state.get("processed_sessions", [])
                miner_stats["processed_sessions"] = len(processed) if isinstance(processed, list) else 0
        except Exception:
            pass
    if mined_path.exists():
        try:
            with open(mined_path, "r", encoding="utf-8") as f:
                miner_stats["mined_samples_count"] = sum(1 for line in f if line.strip())
        except Exception:
            pass
    status["components"]["session_miner"] = miner_stats

    # 3. PreferenceStore 状态
    pref_store = ctx.inject("preference_store", None)
    if pref_store is not None:
        status["components"]["preference_store"] = pref_store.get_stats()
    else:
        pref_path = training_dir / "preferences.jsonl"
        pref_count = 0
        if pref_path.exists():
            try:
                with open(pref_path, "r", encoding="utf-8") as f:
                    pref_count = sum(1 for line in f if line.strip())
            except Exception:
                pass
        status["components"]["preference_store"] = {
            "enabled": False,
            "total": pref_count,
            "note": "PreferenceStore 未注入，仅读取文件计数",
        }

    # 4. 评估器状态
    evolver = ctx.inject("evolver", None)
    if evolver and evolver.evaluator:
        status["components"]["evaluator"] = {
            "evaluation_history_count": len(evolver.evaluator.get_history()),
            "last_report": evolver.last_report.get("notification", None) if evolver.last_report else None,
            "threshold": evolver.evaluator.threshold,
        }
    else:
        status["components"]["evaluator"] = {"enabled": False}

    # 5. 训练 + promote 状态
    if evolver:
        status["components"]["training"] = {
            "status": evolver.training_status,
            "adapters_count": len(evolver.adapters),
            "last_promote_result": evolver.last_promote_result,
            "last_result_status": evolver.last_result.get("status") if evolver.last_result else None,
        }
    else:
        status["components"]["training"] = {"enabled": False}

    # 6. 整体健康度判定
    comp_status = []
    for name, comp in status["components"].items():
        if isinstance(comp, dict):
            comp_status.append(comp.get("enabled", comp.get("status") is not None))
    enabled_count = sum(1 for s in comp_status if s)
    total_count = len(comp_status)

    if enabled_count == 0:
        status["pipeline_health"] = "inactive"
    elif enabled_count == total_count:
        status["pipeline_health"] = "healthy"
    else:
        status["pipeline_health"] = "partial"

    status["enabled_components"] = enabled_count
    status["total_components"] = total_count

    return status
