"""
EchoServe V0.1.0 — 全局配置
通过环境变量或 .env 文件注入，使用 Pydantic 校验。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger("echoserve.config")

# 加载 .env 文件
load_dotenv()

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent

# JWT Secret 默认值哨兵 — None 表示未设置，生产环境必须显式配置
_JWT_SECRET_DEFAULT: str | None = None


class ModelConfig(BaseModel):
    """大模型推理配置（P1 更新：适配 RTX 4090 48GB）"""
    path: str = Field(default=os.getenv("MODEL_PATH", "./models/qwen3-14b-q4"))
    name: str = Field(default=os.getenv("MODEL_NAME", "qwen3-14b-q4"))
    max_ctx: int = Field(default=int(os.getenv("MODEL_MAX_CTX", "32768")))
    gpu_mem_util: float = Field(default=float(os.getenv("MODEL_GPU_MEM_UTIL", "0.90")))


class VLLMConfig(BaseModel):
    """vLLM 推理服务配置"""
    host: str = Field(default=os.getenv("VLLM_HOST", "http://vllm:8000"))
    api_key: str = Field(default=os.getenv("VLLM_API_KEY", ""))
    max_model_len: int = Field(default=int(os.getenv("VLLM_MAX_MODEL_LEN", "32768")))
    tensor_parallel_size: int = Field(default=int(os.getenv("VLLM_TENSOR_PARALLEL_SIZE", "1")))
    prefix_cache_enabled: bool = Field(default=os.getenv("VLLM_PREFIX_CACHE", "true").lower() == "true")


class ChromaConfig(BaseModel):
    """Chroma 向量数据库配置"""
    host: str = Field(default=os.getenv("CHROMA_HOST", "http://chroma:8000"))
    collection: str = Field(default=os.getenv("CHROMA_COLLECTION", "echoseve_kb"))
    persist_dir: str = Field(default=os.getenv("CHROMA_PERSIST_DIR", "./data/chroma"))


class EmbeddingConfig(BaseModel):
    """嵌入模型配置"""
    model: str = Field(default=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"))
    dim: int = Field(default=int(os.getenv("EMBEDDING_DIM", "384")))


class RetrievalConfig(BaseModel):
    """检索融合配置（P1 增强：增加重排序配置）"""
    top_k: int = Field(default=int(os.getenv("RETRIEVAL_TOP_K", "10")))
    rrf_k: int = Field(default=int(os.getenv("RRF_K", "60")))
    bm25_weight: float = Field(default=float(os.getenv("BM25_WEIGHT", "0.4")))
    vector_weight: float = Field(default=float(os.getenv("VECTOR_WEIGHT", "0.6")))

    # P1 新增：Cross-Encoder 重排序
    rerank_enabled: bool = Field(default=os.getenv("RERANK_ENABLED", "true").lower() == "true")
    rerank_tier: str = Field(default=os.getenv("RERANK_TIER", "standard"))
    rerank_device: str = Field(default=os.getenv("RERANK_DEVICE", "cpu"))
    rerank_max_length: int = Field(default=int(os.getenv("RERANK_MAX_LEN", "512")))
    rerank_top_n: int = Field(default=int(os.getenv("RERANK_TOP_N", "20")))
    rerank_final_k: int = Field(default=int(os.getenv("RERANK_FINAL_K", "5")))
    rerank_threshold: float = Field(default=float(os.getenv("RERANK_THRESHOLD", "0.0")))


class APIConfig(BaseModel):
    """FastAPI 服务配置"""
    host: str = Field(default=os.getenv("API_HOST", "0.0.0.0"))
    port: int = Field(default=int(os.getenv("API_PORT", "8080")))
    debug: bool = Field(default=os.getenv("API_DEBUG", "false").lower() == "true")
    cors_origins: str = Field(
        default=os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        )
    )


class SecurityConfig(BaseModel):
    """安全配置"""
    jwt_secret: str | None = Field(default=os.getenv("JWT_SECRET", _JWT_SECRET_DEFAULT))
    token_expire_minutes: int = Field(default=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480")))
    bcrypt_cost: int = Field(default=int(os.getenv("BCRYPT_COST", "12")))
    max_login_attempts: int = Field(default=int(os.getenv("MAX_LOGIN_ATTEMPTS", "5")))
    lockout_minutes: int = Field(default=int(os.getenv("LOCKOUT_MINUTES", "30")))

    def validate_jwt_secret(self) -> None:
        """启动时校验 JWT Secret 安全性。

        非 debug 模式下未设置或为空将拒绝启动。
        debug 模式下自动生成临时密钥（仅用于本地开发）。
        应在应用 lifespan 的最前面调用。
        """
        import secrets as _secrets

        is_debug = os.getenv("API_DEBUG", "false").lower() == "true"
        is_default = not self.jwt_secret or self.jwt_secret == _JWT_SECRET_DEFAULT

        if is_default and not is_debug:
            raise RuntimeError(
                "JWT_SECRET 未设置，生产环境不安全。"
                "请在 .env 中设置随机密钥，例如: "
                "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        if is_default and is_debug:
            # debug 模式自动生成临时密钥，避免开发环境启动失败
            self.jwt_secret = _secrets.token_urlsafe(32)
            logger.warning(
                "JWT_SECRET 未设置（debug 模式），已自动生成临时密钥。"
                "生产环境务必在 .env 中设置固定随机密钥"
            )


class LogConfig(BaseModel):
    """日志配置"""
    level: str = Field(default=os.getenv("LOG_LEVEL", "INFO"))
    file: str = Field(default=os.getenv("LOG_FILE", "./data/logs/echoserve.log"))


class MonitoringConfig(BaseModel):
    """P1 新增：监控配置"""
    enabled: bool = Field(default=os.getenv("MONITORING_ENABLED", "true").lower() == "true")
    metrics_port: int = Field(default=int(os.getenv("METRICS_PORT", "9090")))
    collect_interval: int = Field(default=int(os.getenv("METRICS_INTERVAL", "15")))
    grafana_dashboard: bool = Field(default=os.getenv("GRAFANA_DASHBOARD", "true").lower() == "true")


class EvolveConfig(BaseModel):
    """模型进化引擎配置（V0.2.0 更新：新增 DPO + LLM-Judge 参数）"""
    enabled: bool = Field(default=os.getenv("EVOLVE_ENABLED", "true").lower() == "true")
    train_data_path: str = Field(default=os.getenv("TRAIN_DATA_PATH", "./data/training/train.jsonl"))
    test_data_path: str = Field(default=os.getenv("TEST_DATA_PATH", "./data/training/test_set.jsonl"))
    report_dir: str = Field(default=os.getenv("EVAL_REPORT_DIR", "./data/training/reports"))
    adapters_dir: str = Field(default=os.getenv("ADAPTERS_DIR", "./models/adapters"))
    lora_r: int = Field(default=int(os.getenv("LORA_R", "8")))
    lora_alpha: int = Field(default=int(os.getenv("LORA_ALPHA", "16")))
    lora_dropout: float = Field(default=float(os.getenv("LORA_DROPOUT", "0.05")))
    train_epochs: int = Field(default=int(os.getenv("TRAIN_EPOCHS", "3")))
    train_batch_size: int = Field(default=int(os.getenv("TRAIN_BATCH_SIZE", "4")))
    train_learning_rate: float = Field(default=float(os.getenv("TRAIN_LR", "2e-4")))
    variants_per_q: int = Field(default=int(os.getenv("VARIANTS_PER_Q", "3")))
    generic_ratio: float = Field(default=float(os.getenv("GENERIC_RATIO", "0.15")))
    # V0.2.0 新增 — DPO 训练参数
    base_model_path: str = Field(default=os.getenv("BASE_MODEL_PATH", "./models/qwen3-14b-q4"))
    dpo_data_path: str = Field(default=os.getenv("DPO_DATA_PATH", "./data/training/dpo_dataset.jsonl"))
    dpo_beta: float = Field(default=float(os.getenv("DPO_BETA", "0.1")))
    dpo_learning_rate: float = Field(default=float(os.getenv("DPO_LR", "5e-5")))
    dpo_epochs: int = Field(default=int(os.getenv("DPO_EPOCHS", "2")))
    # V0.2.0 新增 — LLM-as-Judge 评估
    llm_judge_enabled: bool = Field(default=os.getenv("LLM_JUDGE_ENABLED", "false").lower() == "true")


class EvolutionConfig(BaseModel):
    """进化系统配置（Phase 1-3 统一参数）"""
    # Phase 1: 存储 & 采集
    db_path: str = Field(default=os.getenv("EVOLUTION_DB_PATH", "./data/evolution.db"))
    archive_dir: str = Field(default=os.getenv("EVOLUTION_ARCHIVE_DIR", "./data/evolution_archive"))
    fallback_dir: str = Field(default=os.getenv("EVOLUTION_FALLBACK_DIR", "./data/evolution_fallback"))
    max_batch: int = Field(default=int(os.getenv("EVOLUTION_MAX_BATCH", "50")))
    flush_interval: float = Field(default=float(os.getenv("EVOLUTION_FLUSH_INTERVAL", "5")))
    max_backlog: int = Field(default=int(os.getenv("EVOLUTION_MAX_BACKLOG", "10000")))
    collector_enabled: bool = Field(default=os.getenv("EVOLUTION_COLLECTOR_ENABLED", "true").lower() == "true")
    store_hot_days: int = Field(default=int(os.getenv("EVOLUTION_HOT_DAYS", "7")))
    store_cleanup_days: int = Field(default=int(os.getenv("EVOLUTION_CLEANUP_DAYS", "90")))

    # Phase 2: 实验 & 评估
    eval_interval: int = Field(default=int(os.getenv("EVOLUTION_EVAL_INTERVAL", "3600")))
    ab_test_alpha: float = Field(default=float(os.getenv("EVOLUTION_AB_ALPHA", "0.05")))
    ab_test_min_samples: int = Field(default=int(os.getenv("EVOLUTION_AB_MIN_SAMPLES", "500")))
    experiment_default_traffic: float = Field(default=float(os.getenv("EVOLUTION_EXPERIMENT_TRAFFIC", "0.1")))
    experiment_min_sample: int = Field(default=int(os.getenv("EVOLUTION_MIN_SAMPLE", "100")))

    # Phase 3: 挖掘
    min_mining_samples: int = Field(default=int(os.getenv("EVOLUTION_MINING_MIN_SAMPLES", "2000")))
    max_patterns_per_day: int = Field(default=int(os.getenv("EVOLUTION_MAX_PATTERNS_DAY", "10")))
    mining_min_support: int = Field(default=int(os.getenv("EVOLUTION_MINING_MIN_SUPPORT", "10")))
    mining_min_success_rate: float = Field(default=float(os.getenv("EVOLUTION_MINING_MIN_RATE", "0.9")))
    mining_max_sequence_length: int = Field(default=int(os.getenv("EVOLUTION_MINING_MAX_SEQ", "5")))

    # Phase 3: 模板
    canary_percent: float = Field(default=float(os.getenv("EVOLUTION_CANARY_PERCENT", "10.0")))
    full_rollout_threshold: float = Field(default=float(os.getenv("EVOLUTION_FULL_ROLLOUT_THRESHOLD", "95.0")))
    template_auto_promote: bool = Field(default=os.getenv("EVOLUTION_AUTO_PROMOTE", "false").lower() == "true")
    template_promote_threshold: float = Field(default=float(os.getenv("EVOLUTION_PROMOTE_THRESHOLD", "0.95")))
    template_canary_percent: float = Field(default=float(os.getenv("EVOLUTION_CANARY_PERCENT_RATIO", "0.1")))

    # 通知
    notifier_webhook: str | None = Field(default=os.getenv("EVOLUTION_NOTIFIER_WEBHOOK") or None)

    # Metrics
    metrics_capacity: int = Field(default=int(os.getenv("EVOLUTION_METRICS_CAPACITY", "10000")))


class BackupConfig(BaseModel):
    """P1 新增：备份配置"""
    enabled: bool = Field(default=os.getenv("BACKUP_ENABLED", "true").lower() == "true")
    backup_dir: str = Field(default=os.getenv("BACKUP_DIR", "./data/backups"))
    retention_count: int = Field(default=int(os.getenv("BACKUP_RETENTION", "30")))
    schedule_cron: str = Field(default=os.getenv("BACKUP_SCHEDULE", "0 3 * * *"))  # 每天 3:00
    include_models: bool = Field(default=os.getenv("BACKUP_MODELS", "false").lower() == "true")


class RedisConfig(BaseModel):
    """V0.1.6 新增：Redis 配置（会话持久化 / 缓存）"""
    url: str = Field(default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    host: str = Field(default=os.getenv("REDIS_HOST", "localhost"))
    port: int = Field(default=int(os.getenv("REDIS_PORT", "6379")))
    db: int = Field(default=int(os.getenv("REDIS_DB", "0")))
    password: str = Field(default=os.getenv("REDIS_PASSWORD", ""))
    session_ttl: int = Field(default=int(os.getenv("REDIS_SESSION_TTL", "1800")))  # 会话过期秒数
    key_prefix: str = Field(default=os.getenv("REDIS_KEY_PREFIX", "echoseve:session:"))


class PostgreSQLConfig(BaseModel):
    """V0.1.7 新增：PostgreSQL 配置（用户 / API Key 持久化）"""
    host: str = Field(default=os.getenv("POSTGRES_HOST", "localhost"))
    port: int = Field(default=int(os.getenv("POSTGRES_PORT", "5432")))
    database: str = Field(default=os.getenv("POSTGRES_DB", "echoseve"))
    user: str = Field(default=os.getenv("POSTGRES_USER", "echoseve"))
    password: str = Field(default=os.getenv("POSTGRES_PASSWORD", ""))
    pool_min_size: int = Field(default=int(os.getenv("PG_POOL_MIN", "2")))
    pool_max_size: int = Field(default=int(os.getenv("PG_POOL_MAX", "10")))


class Settings(BaseModel):
    """全局配置聚合"""
    model: ModelConfig = ModelConfig()
    vllm: VLLMConfig = VLLMConfig()
    chroma: ChromaConfig = ChromaConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    api: APIConfig = APIConfig()
    security: SecurityConfig = SecurityConfig()
    log: LogConfig = LogConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    evolve: EvolveConfig = EvolveConfig()
    evolution: EvolutionConfig = EvolutionConfig()
    backup: BackupConfig = BackupConfig()
    redis: RedisConfig = RedisConfig()
    postgres: PostgreSQLConfig = PostgreSQLConfig()

    @property
    def root_dir(self) -> Path:
        return ROOT_DIR

    def validate_security(self) -> None:
        """启动时执行安全校验（由 lifespan 调用）。"""
        self.security.validate_jwt_secret()


settings = Settings()
