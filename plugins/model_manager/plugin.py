"""
EchoServe P1 — 模型管理插件

功能：
- 管理多个模型版本（基础模型 + LoRA adapters）
- 支持模型热切换（通过 vLLM API）
- 监控模型状态（加载中/就绪/错误）
- 提供模型管理 API
"""
from __future__ import annotations

import logging
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from core.plugin import BaizePlugin
from core.context import BaizeContext
from core.fiber import Fiber

from .vllm_client import VLLMClient

logger = logging.getLogger("echoseve.model.manager")


class ModelManagerPlugin(BaizePlugin):
    """
    模型管理插件。

    注册服务：
    - "model_manager" → ModelManagerPlugin 实例
    - "vllm_client" → VLLMClient 实例

    支持：
    - 模型列表查询
    - 模型热切换（无需重启）
    - LoRA adapter 动态加载/卸载
    - 模型状态监控
    """

    plugin_id = "core.model_manager"
    plugin_name = "模型管理器"
    plugin_version = "0.1.0"
    dependencies = ["core.config"]

    def __init__(self):
        self.ctx: Optional[BaizeContext] = None
        self.vllm: Optional[VLLMClient] = None
        self.models_dir: str = "./models"
        self.adapters_dir: str = "./models/adapters"
        self._models_registry: Dict[str, Dict[str, Any]] = {}
        self._current_model_id: Optional[str] = None
        self._load_history: List[Dict[str, Any]] = []

    # ─── 生命周期 ──────────────────────────────────

    async def on_init(self, ctx: BaizeContext, fiber: Fiber):
        """初始化模型管理器"""
        self.ctx = ctx
        settings = ctx.settings

        # 创建 vLLM 客户端
        self.vllm = VLLMClient(
            host=settings.vllm.host,
            api_key=settings.vllm.api_key,
            timeout=60.0,
        )

        # 设置路径
        self.models_dir = str(ctx.root_dir / "models")
        self.adapters_dir = str(ctx.root_dir / "models" / "adapters")

        # 扫描可用模型
        self._scan_models()

        # 注册服务
        ctx.provide("model_manager", self)
        ctx.provide("vllm_client", self.vllm)

        logger.info(f"[{self.plugin_id}] 模型管理器初始化完成")
        logger.info(f"  发现 {len(self._models_registry)} 个可用模型")

    async def on_destroy(self, ctx: BaizeContext, fiber: Fiber):
        """释放资源"""
        if self.vllm:
            self.vllm.close()
        logger.info(f"[{self.plugin_id}] 已销毁")

    # ─── 模型扫描 ──────────────────────────────────

    def _scan_models(self):
        """扫描 models/ 目录，注册可用模型"""
        models_path = Path(self.models_dir)
        if not models_path.exists():
            logger.warning(f"  模型目录不存在: {self.models_dir}")
            return

        # 扫描基础模型（目录形式）
        for item in models_path.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                # 检查是否有模型文件
                has_model = any(
                    (item / f).exists() for f in ["config.json", "pytorch_model.bin", "model.safetensors"]
                )
                if has_model:
                    self._models_registry[item.name] = {
                        "name": item.name,
                        "path": str(item),
                        "type": "base",
                        "status": "available",
                        "size_mb": self._dir_size_mb(item),
                    }
                    logger.info(f"  发现基础模型: {item.name}")

        # 扫描 LoRA adapters
        adapters_path = Path(self.adapters_dir)
        if adapters_path.exists():
            for item in adapters_path.iterdir():
                if item.is_dir():
                    adapter_cfg = item / "adapter_config.json"
                    if adapter_cfg.exists():
                        info = {"name": item.name, "path": str(item), "type": "lora"}
                        # 读取 adapter 信息
                        try:
                            with open(adapter_cfg, "r") as f:
                                cfg = json.load(f)
                            info["base_model"] = cfg.get("base_model", "unknown")
                            info["lora_r"] = cfg.get("lora_r", 0)
                            info["created_at"] = cfg.get("timestamp", "unknown")
                        except Exception as e:
                            logger.debug(f"Failed to read LoRA adapter config for {item.name}: {e}")
                        info["status"] = "available"
                        self._models_registry[item.name] = info
                        logger.info(f"  发现 LoRA adapter: {item.name}")

    def _dir_size_mb(self, path: Path) -> float:
        """计算目录大小（MB）"""
        total = 0
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return round(total / (1024 * 1024), 1)

    # ─── 模型切换 ──────────────────────────────────

    async def switch_model(
        self,
        model_id: str,
        use_lora: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        切换当前模型（热切换，无需重启）。

        Args:
            model_id: 模型 ID（在注册表中的名称）
            use_lora: 可选，挂载的 LoRA adapter 名称

        Returns:
            {"status": "success"|"failed", ...}
        """
        if model_id not in self._models_registry:
            return {
                "status": "failed",
                "reason": f"模型不存在: {model_id}",
                "available": list(self._models_registry.keys()),
            }

        model_info = self._models_registry[model_id]
        model_path = model_info["path"]

        logger.info(f"[{self.plugin_id}] 切换模型 → {model_id}")

        # 1. 加载基础模型
        result = self.vllm.load_model(model_path, model_id=model_id)

        if result.get("status") == "success":
            self._current_model_id = model_id
            model_info["status"] = "loaded"

            # 2. 可选：挂载 LoRA
            lora_result = None
            if use_lora:
                lora_result = self.load_adapter(use_lora)
                if lora_result.get("status") != "success":
                    logger.warning(f"  LoRA 加载失败: {lora_result.get('reason')}")

            # 3. 记录历史
            self._load_history.append({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "model_id": model_id,
                "lora": use_lora,
                "load_time_s": result.get("load_time_s", 0),
            })

            # 4. 通知
            self._notify_switch(model_id, use_lora)

            return {
                "status": "success",
                "model_id": model_id,
                "model_path": model_path,
                "lora": use_lora,
                "load_time_s": result.get("load_time_s", 0),
                "lora_result": lora_result,
            }
        else:
            model_info["status"] = "error"
            return {
                "status": "failed",
                "reason": result.get("reason", "未知错误"),
                "instruction": result.get("instruction", ""),
            }

    def load_adapter(self, adapter_name: str) -> Dict[str, Any]:
        """
        加载 LoRA adapter 到当前模型。
        """
        if adapter_name not in self._models_registry:
            return {"status": "failed", "reason": f"Adapter 不存在: {adapter_name}"}

        adapter_info = self._models_registry[adapter_name]
        if adapter_info.get("type") != "lora":
            return {"status": "failed", "reason": f"不是 LoRA adapter: {adapter_name}"}

        return self.vllm.load_lora_adapter(
            adapter_path=adapter_info["path"],
            adapter_name=adapter_name,
        )

    def unload_adapter(self, adapter_name: str) -> Dict[str, Any]:
        """卸载 LoRA adapter"""
        return self.vllm.unload_lora_adapter(adapter_name)

    # ─── 状态查询 ──────────────────────────────────

    def list_models(self) -> List[Dict[str, Any]]:
        """列出所有可用模型"""
        return [
            {"id": name, **info}
            for name, info in self._models_registry.items()
        ]

    def get_status(self) -> Dict[str, Any]:
        """获取模型管理完整状态"""
        vllm_info = self.vllm.get_server_info() if self.vllm else {}

        return {
            "current_model": self._current_model_id,
            "models": self.list_models(),
            "vllm": vllm_info,
            "load_history": self._load_history[-10:],  # 最近 10 次
            "adapters_loaded": self.vllm.list_lora_adapters() if self.vllm else [],
        }

    async def aget_status(self) -> Dict[str, Any]:
        """异步获取状态（含健康检查）"""
        status = self.get_status()
        if self.vllm:
            health = await self.vllm.ahealth_check()
            status["vllm"]["health_check"] = health
        return status

    # ─── 推理代理 ──────────────────────────────────

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model_id: Optional[str] = None,
        lora_name: Optional[str] = None,
        **kwargs,
    ) -> str:
        """代理到 vLLM 推理"""
        target_model = model_id or self._current_model_id
        return await self.vllm.achat(
            messages=messages,
            model=target_model,
            lora_name=lora_name,
            **kwargs,
        )

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model_id: Optional[str] = None,
        lora_name: Optional[str] = None,
        **kwargs,
    ):
        """代理到 vLLM 流式推理"""
        target_model = model_id or self._current_model_id
        async for token in self.vllm.achat_stream(
            messages=messages,
            model=target_model,
            lora_name=lora_name,
            **kwargs,
        ):
            yield token

    # ─── 内部方法 ──────────────────────────────────

    def _notify_switch(self, model_id: str, lora: Optional[str]):
        """记录模型切换通知"""
        msg = f"模型已切换至: {model_id}"
        if lora:
            msg += f" (LoRA: {lora})"

        # 写入审计
        audit = self.ctx.inject("audit_logger")
        if audit:
            try:
                audit.log_sync(
                    action="model_switch",
                    user_id="system",
                    query=f"switch_to={model_id}",
                    response_summary=msg,
                    sources=[],
                    latency_ms=0,
                    channel="system",
                )
            except Exception as e:
                logger.debug(f"Failed to log model switch audit: {e}")

        logger.info(f"[{self.plugin_id}] {msg}")
