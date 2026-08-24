"""
EchoServe V0.1.0 — 系统设置 API 路由

端点：
    GET    /api/settings/wechat-kf     读取企业微信客服配置
    POST   /api/settings/wechat-kf     保存企业微信客服配置（管理员权限）
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_token, require_permission
from config.settings import settings

logger = logging.getLogger("echoseve.api.settings")

router = APIRouter()

# ─── 微信客服配置模型 ─────────────────────────────────

class WechatKFConfig(BaseModel):
    url: str = Field(default="", description="回调 URL")
    token: str = Field(default="", description="Token")
    aesKey: str = Field(default="", description="EncodingAESKey")
    corpId: str = Field(default="", description="CorpID")
    secret: str = Field(default="", description="Secret（可选）")


# ─── 辅助函数 ──────────────────────────────────────

def _get_env_path() -> Path:
    """获取 .env 文件路径"""
    # 优先使用项目根目录下的 .env
    project_root = Path(__file__).parent.parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        return env_path
    # 回退到当前工作目录
    return Path.cwd() / ".env"


def _read_env_file() -> Dict[str, str]:
    """读取 .env 文件为字典"""
    env_path = _get_env_path()
    config = {}
    if not env_path.exists():
        return config
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip()
    except Exception as e:
        logger.warning(f"[Settings] 读取 .env 失败: {e}")
    return config


def _write_env_file(config: Dict[str, str]) -> bool:
    """将字典写回 .env 文件，保留注释"""
    env_path = _get_env_path()
    try:
        lines = []
        existing_keys = set()
        
        # 如果文件存在，先读取保留注释和结构
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        lines.append(line.rstrip("\n"))
                        continue
                    if "=" in stripped:
                        key = stripped.split("=", 1)[0].strip()
                        if key in config:
                            lines.append(f"{key}={config[key]}")
                            existing_keys.add(key)
                        else:
                            lines.append(line.rstrip("\n"))
                    else:
                        lines.append(line.rstrip("\n"))
        
        # 追加新键
        for key, value in config.items():
            if key not in existing_keys:
                lines.append(f"{key}={value}")
        
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        
        return True
    except Exception as e:
        logger.error(f"[Settings] 写入 .env 失败: {e}")
        return False


def _get_wechat_config() -> Dict[str, str]:
    """从环境变量和 .env 读取微信客服配置"""
    return {
        "url": os.getenv("WECHAT_KF_WEBHOOK_PATH", ""),
        "token": os.getenv("WECHAT_KF_TOKEN", ""),
        "aesKey": os.getenv("WECHAT_KF_AES_KEY", ""),
        "corpId": os.getenv("WECHAT_KF_CORP_ID", ""),
        "secret": os.getenv("WECHAT_KF_SECRET", ""),
    }


def _build_webhook_url(webhook_path: str) -> str:
    """构建完整的回调 URL"""
    if not webhook_path:
        return ""
    if webhook_path.startswith("http"):
        return webhook_path
    # 默认使用环境变量中的 WEBHOOK_BASE_URL 或空
    base_url = os.getenv("WEBHOOK_BASE_URL", "")
    if base_url:
        return f"{base_url}{webhook_path}"
    return webhook_path


# ─── API 端点 ──────────────────────────────────────

@router.get("/settings/wechat-kf")
async def get_wechat_kf_config(
    _: str = Depends(verify_token),
) -> Dict[str, Any]:
    """
    读取当前企业微信客服配置。
    任何已登录用户可读取。
    """
    cfg = _get_wechat_config()
    # 将 webhook path 转为完整 URL
    if cfg.get("url") and not cfg["url"].startswith("http"):
        cfg["url"] = _build_webhook_url(cfg["url"])
    
    return {
        "status": "ok",
        "config": cfg,
        "note": "修改后需重启 EchoServe 生效",
    }


@router.post("/settings/wechat-kf")
async def save_wechat_kf_config(
    request: WechatKFConfig,
    user_id: str = Depends(require_permission("system.write")),
) -> Dict[str, Any]:
    """
    保存企业微信客服配置到 .env 文件。
    需要 system.write 权限（admin / super_admin）。
    """
    env_updates = {}
    
    # 提取 webhook path（如果传的是完整 URL，只保留 path 部分）
    url_val = request.url.strip()
    if url_val:
        # 移除已知的本地/开发域名前缀，保留 path 部分
        local_prefixes = ["http://localhost:8080", "https://localhost:8080"]
        for prefix in local_prefixes:
            if url_val.startswith(prefix):
                url_val = url_val.replace(prefix, "")
                break
        if not url_val.startswith("/"):
            # 自定义域名，保留完整 URL
            pass
    env_updates["WECHAT_KF_WEBHOOK_PATH"] = url_val
    
    # 所有字段都写入（包括空值），确保用户清空字段时 .env 同步更新
    env_updates["WECHAT_KF_TOKEN"] = request.token.strip()
    env_updates["WECHAT_KF_AES_KEY"] = request.aesKey.strip()
    env_updates["WECHAT_KF_CORP_ID"] = request.corpId.strip()
    env_updates["WECHAT_KF_SECRET"] = request.secret.strip()

    # 写入 .env
    success = _write_env_file(env_updates)
    
    if not success:
        raise HTTPException(status_code=500, detail="写入配置文件失败")
    
    # 同时更新当前进程的环境变量（部分配置可热加载）
    for key, value in env_updates.items():
        os.environ[key] = value
    
    logger.info(f"[Settings] 用户 {user_id} 更新了微信客服配置")
    
    return {
        "status": "saved",
        "message": "配置已保存到 .env，建议重启 EchoServe 使所有变更生效",
        "updated_keys": list(env_updates.keys()),
    }


@router.get("/settings/system")
async def get_system_info(
    _: str = Depends(verify_token),
) -> Dict[str, Any]:
    """
    读取系统运行状态与当前模型信息。
    """
    # --- 模型信息 ---
    # 当前部署在 AutoDL 服务器上的模型：Qwen3-8B-Instruct（Alibaba Cloud）
    model_name = "Qwen3-8B-Instruct"
    model_full = "/root/autodl-tmp/models/Qwen3-8B"

    # --- 运行环境 ---
    gpu_info = "CPU"
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                gpu_info = f"{parts[0].strip()} {parts[1].strip()}"
    except Exception:
        pass

    ram = "Unknown"
    try:
        import psutil
        mem = psutil.virtual_memory()
        ram = f"{mem.total // (1024**3)}GB"
    except Exception:
        pass

    # --- 版本号 ---
    version = "0.1.2"
    build = "P2"

    return {
        "status": "ok",
        "system": {
            "name": "EchoServe",
            "version": version,
            "build": build,
            "env": gpu_info,
            "ram": ram,
            "base_model": model_name,
            "model_path": model_full,
            "embedding": os.getenv("EMBEDDING_MODEL", getattr(settings, "embedding_model", "bge-small-zh-v1.5")),
            "data_localization": True,
            "security_compliance": "等保2.0 三级（目标）",
        }
    }
