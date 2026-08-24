"""
EchoServe P1 — 集成测试脚本

测试覆盖：
  T01 — Cross-Encoder 重排序器初始化与降级
  T02 — 重排序对检索结果的重排效果
  T03 — VLLMClient 健康检查（降级模式）
  T04 — MetricsCollector 指标采集与 Prometheus 导出
  T05 — MonitoringPlugin 后台采集循环
  T06 — ModelManagerPlugin 模型扫描与状态管理
  T07 — ModelEvolvePlugin 进化策略检查
  T08 — TrainingDataBuilder 数据构建与验证
  T09 — EvaluationPipeline 评分与 A/B 测试
  T10 — LoRATrainer 训练流程模拟
  T11 — Backup/Restore 脚本语法检查
  T12 — Prometheus 配置文件验证
  T13 — Grafana 仪表盘 JSON 验证
  T14 — Docker Compose P1 配置验证
  T15 — 全插件加载与依赖解析

运行：python scripts/test_p1.py
"""
from __future__ import annotations

import sys
import json
import asyncio
import tempfile
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s - %(message)s")

# 颜色输出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"

passed = 0
failed = 0
errors = []

def test(name: str):
    """测试装饰器"""
    def wrapper(func):
        global passed, failed
        print(f"\n{BLUE}▶{RESET} {name}...")
        try:
            result = func()
            if result is False:
                failed += 1
                errors.append(name)
                print(f"  {RED}✗ 失败{RESET}")
            else:
                passed += 1
                print(f"  {GREEN}✓ 通过{RESET}")
        except Exception as e:
            failed += 1
            errors.append(f"{name}: {e}")
            print(f"  {RED}✗ 异常: {e}{RESET}")
        return func
    return wrapper


# ═══════════════════════════════════════════════════════════
# T01 — Cross-Encoder 重排序器
# ═══════════════════════════════════════════════════════════

@test("T01: Cross-Encoder 重排序器初始化与降级")
def test_reranker_init():
    from plugins.retriever.reranker import CrossEncoderReranker, RerankerFactory

    # 1. 禁用模式
    r = CrossEncoderReranker(enabled=False)
    assert r.enabled == False
    assert r.is_available == False

    # 2. 工厂创建（standard 默认不可用，会降级）
    r2 = RerankerFactory.create(tier="standard", device="cpu")
    # 在测试环境中 sentence-transformers 可能不可用，应能优雅降级
    assert r2 is not None
    assert hasattr(r2, "rerank")
    assert hasattr(r2, "is_available")

    # 3. light 模式 = 禁用
    r3 = RerankerFactory.create(tier="light")
    assert r3.enabled == False

    # 4. rerank 方法在不可用时返回原始排序
    r4 = CrossEncoderReranker(enabled=False)
    candidates = [
        {"id": "1", "content": "退货政策是7天", "score": 0.8},
        {"id": "2", "content": "发货需要3天", "score": 0.6},
        {"id": "3", "content": "客服电话400", "score": 0.4},
    ]
    result = r4.rerank("退货", candidates, top_k=2)
    assert len(result) == 2
    assert result[0]["id"] == "1"  # 原始排序保持
    return True


@test("T02: 重排序对检索结果的重排效果")
def test_rerank_effect():
    from plugins.retriever.reranker import CrossEncoderReranker

    # 模拟已有关键词命中的候选
    candidates = [
        {"id": "a", "content": "退货需要7个工作日处理完毕", "score": 0.5},
        {"id": "b", "content": "退货政策：7天内无理由退货", "score": 0.7},
        {"id": "c", "content": "我们的退货流程很简单", "score": 0.3},
    ]

    # 禁用重排序 → 保持原序（按输入顺序取 top_k）
    r = CrossEncoderReranker(enabled=False)
    result = r.rerank("退货政策是什么", candidates, top_k=2)
    assert len(result) == 2
    # 禁用时保持原始输入顺序，取前 top_k 个
    assert result[0]["id"] == "a"  # 原始第一个
    assert result[1]["id"] == "b"  # 原始第二个
    # 但每个结果应有 rerank_score 字段（等于原 score）
    assert result[0]["rerank_score"] == 0.5
    assert result[1]["rerank_score"] == 0.7

    # 启用但不可用 → 也应保持原序
    r2 = CrossEncoderReranker(enabled=True, model_name="BAAI/nonexistent-model-xyz")
    result2 = r2.rerank("退货政策", candidates, top_k=2)
    assert len(result2) == 2
    return True


# ═══════════════════════════════════════════════════════════
# T03 — VLLMClient（降级模式测试）
# ═══════════════════════════════════════════════════════════

@test("T03: VLLMClient 健康检查（降级模式）")
def test_vllm_client():
    from plugins.model_manager.vllm_client import VLLMClient

    # 连接到一个不存在的地址，测试降级
    client = VLLMClient(host="http://127.0.0.1:19999", timeout=0.5)

    # 健康检查应返回 healthy=False
    health = client.health_check()
    assert health["healthy"] == False
    assert "error" in health or health["status_code"] != 200

    # list_models 应返回空列表
    models = client.list_models()
    assert models == []

    # load_model 应返回 failed
    result = client.load_model("nonexistent/model")
    assert result["status"] == "failed"

    client.close()
    return True


# ═══════════════════════════════════════════════════════════
# T04 — MetricsCollector
# ═══════════════════════════════════════════════════════════

@test("T04: MetricsCollector 指标采集与 Prometheus 导出")
def test_metrics_collector():
    from plugins.monitoring.metrics import MetricsCollector

    mc = MetricsCollector(namespace="echoseve_test")

    # 1. Counter
    mc.inc_counter("requests", {"method": "chat", "status": "success"})
    mc.inc_counter("requests", {"method": "chat", "status": "success"})
    mc.inc_counter("requests", {"method": "chat", "status": "error"})

    # label_key 是 sorted items 的元组 → (('method','chat'), ('status','success'))
    req_key = "echoseve_test_requests_total"
    # 获取第一个匹配的 key
    success_key = None
    error_key = None
    for k in mc._counters[req_key]:
        d = dict(k)
        if d.get("status") == "success":
            success_key = k
        elif d.get("status") == "error":
            error_key = k
    assert success_key is not None
    assert error_key is not None
    assert mc._counters[req_key][success_key] == 2
    assert mc._counters[req_key][error_key] == 1

    # 2. Gauge
    mc.set_gauge("gpu_utilization", 75.5)
    assert mc._gauges["echoseve_test_gpu_utilization"][()] == 75.5

    # 3. Histogram
    mc.record_retrieval(50.0, 5)
    mc.record_retrieval(150.0, 10)
    mc.record_retrieval(500.0, 3)
    hist_key = "echoseve_test_retrieval_duration_ms"
    assert hist_key in mc._histograms
    assert mc._histograms[hist_key]["count"] == 3
    assert mc._histograms[hist_key]["sum"] == 700.0

    # 4. Prometheus 导出
    prom_text = mc.export_prometheus()
    assert "# HELP" in prom_text
    assert "# TYPE" in prom_text
    assert "echoseve_test_requests_total" in prom_text
    assert "echoseve_test_gpu_utilization" in prom_text
    assert "echoseve_test_retrieval_duration_ms_bucket" in prom_text
    assert "echoseve_test_uptime_seconds" in prom_text

    # 5. Snapshot
    snap = mc.snapshot()
    assert "counters" in snap
    assert "gauges" in snap
    assert "histograms" in snap
    assert snap["uptime_seconds"] >= 0
    return True


@test("T05: MonitoringPlugin 后台采集循环")
def test_monitoring_plugin():
    from plugins.monitoring.plugin import MonitoringPlugin
    from core.context import BaizeContext
    from core.fiber import Fiber

    ctx = BaizeContext()
    plugin = MonitoringPlugin()
    fiber = Fiber(plugin, ctx)

    # on_init
    asyncio.run(plugin.on_init(ctx, fiber))
    assert ctx.inject("metrics") is not None
    assert ctx.inject("monitoring") is plugin

    # collect_system_metrics（不依赖 psutil）
    collector = plugin.collector
    ok = collector.collect_system_metrics()
    # 可能成功（有 psutil）或失败（无 psutil），都不应抛异常
    assert ok in (True, False)

    # get_prometheus_metrics
    text = plugin.get_prometheus_metrics()
    assert isinstance(text, str)
    assert len(text) > 0

    # get_snapshot
    snap = plugin.get_snapshot()
    assert "uptime_seconds" in snap

    # get_dashboard_data
    dash = plugin.get_dashboard_data()
    assert "system" in dash
    assert "business" in dash
    assert "plugins" in dash

    # 清理
    asyncio.run(plugin.on_destroy(ctx, fiber))
    return True


# ═══════════════════════════════════════════════════════════
# T06 — ModelManagerPlugin
# ═══════════════════════════════════════════════════════════

@test("T06: ModelManagerPlugin 模型扫描与状态管理")
def test_model_manager():
    from plugins.model_manager.plugin import ModelManagerPlugin
    from core.context import BaizeContext
    from core.fiber import Fiber

    ctx = BaizeContext()
    plugin = ModelManagerPlugin()
    fiber = Fiber(plugin, ctx)

    asyncio.run(plugin.on_init(ctx, fiber))

    # 注册服务
    assert ctx.inject("model_manager") is plugin
    assert ctx.inject("vllm_client") is not None

    # 扫描模型（在没有模型文件的测试环境中，应优雅处理）
    models = plugin.list_models()
    assert isinstance(models, list)

    # get_status
    status = plugin.get_status()
    assert "current_model" in status
    assert "models" in status
    assert "vllm" in status

    # switch_model 在 vLLM 不可用时返回 failed
    result = asyncio.run(plugin.switch_model("nonexistent-model"))
    assert result["status"] == "failed"

    asyncio.run(plugin.on_destroy(ctx, fiber))
    return True


# ═══════════════════════════════════════════════════════════
# T07 — ModelEvolvePlugin 进化策略
# ═══════════════════════════════════════════════════════════

@test("T07: ModelEvolvePlugin 进化策略检查")
def test_evolve_plugin():
    from plugins.evolve.plugin import ModelEvolvePlugin
    from core.context import BaizeContext
    from core.fiber import Fiber

    ctx = BaizeContext()
    plugin = ModelEvolvePlugin()
    fiber = Fiber(plugin, ctx)

    # Mock 知识库（返回空文档列表）
    class MockKnowledgeBase:
        def count_documents(self):
            return 0
        def get_all_documents(self):
            return []
        def get_all_qa_pairs(self):
            return []

    # 注册 mock knowledge_base 服务（check_and_evolve 需要）
    ctx.provide("knowledge_base", MockKnowledgeBase())

    asyncio.run(plugin.on_init(ctx, fiber))

    # 注册服务
    assert ctx.inject("evolver") is plugin
    assert ctx.inject("evaluator") is not None
    assert ctx.inject("ab_tester") is not None

    # check_and_evolve（kb 为空时应返回 stage 1）
    check = plugin.check_and_evolve()
    assert check["stage"] == 1
    assert check["can_train"] == False
    assert "纯 RAG" in check["recommendation"]

    # get_status
    status = plugin.get_status()
    assert "training_status" in status
    assert "adapters" in status
    assert "evolution" in status

    # trigger_offline_lora 在没有数据时返回 failed
    result = plugin.trigger_offline_lora(train_data_path="/nonexistent/path.jsonl")
    assert result["status"] == "failed"

    asyncio.run(plugin.on_destroy(ctx, fiber))
    return True


@test("T08: TrainingDataBuilder 数据构建与验证")
def test_training_data_builder():
    from plugins.evolve.data_builder import TrainingDataBuilder

    # 创建临时知识库
    tmpdir = tempfile.mkdtemp()
    kb_path = Path(tmpdir) / "documents.jsonl"
    with open(kb_path, "w") as f:
        for i in range(5):
            f.write(json.dumps({
                "question": f"测试问题 {i}？",
                "answer": f"这是测试答案 {i}，包含一些关键词如退货、退款、发货。",
            }, ensure_ascii=False) + "\n")

    # Mock 知识库对象
    class MockKB:
        def get_all_qa_pairs(self):
            items = []
            with open(kb_path, "r") as f:
                for line in f:
                    items.append(json.loads(line))
            return items

    output_path = Path(tmpdir) / "train.jsonl"
    builder = TrainingDataBuilder(
        knowledge_base=MockKB(),
        llm_client=None,  # 无 LLM → 使用模板变体
        output_path=str(output_path),
        variants_per_q=2,
        generic_ratio=0.1,
    )

    # 构建
    result_path = builder.build()
    assert Path(result_path).exists()

    # 验证
    validation = builder.validate(result_path)
    assert validation["total"] > 0
    assert validation["valid"] > 0
    assert validation["avg_input_len"] > 0

    # 检查 Alpaca 格式
    with open(result_path, "r") as f:
        first = json.loads(f.readline())
        assert "instruction" in first
        assert "input" in first
        assert "output" in first

    return True


@test("T09: EvaluationPipeline 评分与 A/B 测试")
def test_evaluator():
    from plugins.evolve.evaluator import EvaluationPipeline

    # 创建测试集
    tmpdir = tempfile.mkdtemp()
    test_path = Path(tmpdir) / "test_set.jsonl"
    with open(test_path, "w") as f:
        f.write(json.dumps({
            "question": "退货政策是什么？",
            "expected": "7天内无理由退货，需要保留原始包装",
        }, ensure_ascii=False) + "\n")
        f.write(json.dumps({
            "question": "多久能发货？",
            "expected": "一般3-5个工作日发货",
        }, ensure_ascii=False) + "\n")
        f.write(json.dumps({
            "question": "客服电话多少？",
            "expected": "客服热线400-888-9999",
        }, ensure_ascii=False) + "\n")

    ev = EvaluationPipeline(test_set_path=str(test_path))

    # Mock 预测函数
    def good_predict(q):
        if "退货" in q: return "7天内无理由退货"
        if "发货" in q: return "3-5个工作日发货"
        if "客服" in q: return "400-888-9999"
        return "不知道"

    def bad_predict(q):
        return "抱歉，我不知道"

    # 评估好模型
    report = ev.evaluate(good_predict)
    assert report["total"] == 3
    assert report["correct"] >= 2  # 至少 2/3 正确
    assert report["accuracy"] > 0.5

    # 评估坏模型
    report_bad = ev.evaluate(bad_predict)
    assert report_bad["accuracy"] < report["accuracy"]

    # A/B 测试 — 注意：run_ab_test 中 improvement = b_score - a_score
    # 所以让 A=坏模型, B=好模型，这样 improvement > 0
    ab = ev.run_ab_test(bad_predict, good_predict, label_a="坏模型", label_b="好模型")
    assert "improvement" in ab
    assert ab["improvement"] > 0  # B（好模型）应该比 A（坏模型）好
    assert ab["好模型_accuracy"] > ab["坏模型_accuracy"]

    return True


@test("T10: LoRATrainer 训练流程模拟")
def test_lora_trainer():
    from plugins.evolve.trainer import LoRATrainer

    # 创建临时训练数据
    tmpdir = tempfile.mkdtemp()
    train_path = Path(tmpdir) / "train.jsonl"
    with open(train_path, "w") as f:
        for i in range(10):
            f.write(json.dumps({
                "instruction": "请根据公司知识库回答以下问题：",
                "input": f"问题 {i}",
                "output": f"答案 {i}，包含关键词测试。",
            }, ensure_ascii=False) + "\n")

    output_dir = Path(tmpdir) / "adapter"

    # 使用模拟模式（无 GPU）
    trainer = LoRATrainer(
        base_model="/nonexistent/model",  # 不存在，应失败
        train_data=str(train_path),
        output_dir=str(output_dir),
        num_epochs=1,
        batch_size=1,
    )

    # 训练应失败（模型不存在）
    result = trainer.train()
    assert result["status"] == "failed"
    assert "base_model_not_found" in result.get("reason", "")

    # 无训练数据也应失败
    empty_trainer = LoRATrainer(
        base_model="/tmp",
        train_data=str(Path(tmpdir) / "nonexist.jsonl"),
        output_dir=str(output_dir),
    )
    result2 = empty_trainer.train()
    assert result2["status"] == "failed"
    return True


# ═══════════════════════════════════════════════════════════
# T11-T15 — 配置文件验证
# ═══════════════════════════════════════════════════════════

@test("T11: Backup/Restore 脚本语法检查")
def test_scripts_syntax():
    import subprocess

    # 检查 backup.sh 语法
    result = subprocess.run(
        ["bash", "-n", str(PROJECT_ROOT / "scripts" / "backup.sh")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"backup.sh 语法错误: {result.stderr}"

    # 检查 restore.sh 语法
    result2 = subprocess.run(
        ["bash", "-n", str(PROJECT_ROOT / "scripts" / "restore.sh")],
        capture_output=True, text=True,
    )
    assert result2.returncode == 0, f"restore.sh 语法错误: {result2.stderr}"
    return True


@test("T12: Prometheus 配置文件验证")
def test_prometheus_config():
    prom_path = PROJECT_ROOT / "monitoring" / "prometheus.yml"
    assert prom_path.exists(), "prometheus.yml 不存在"

    content = prom_path.read_text()
    assert "global:" in content
    assert "scrape_configs:" in content
    assert "echoseve-api" in content
    assert "metrics_path" in content
    return True


@test("T13: Grafana 仪表盘 JSON 验证")
def test_grafana_dashboard():
    dash_path = PROJECT_ROOT / "monitoring" / "grafana-provisioning" / "dashboards" / "echoseve.json"
    assert dash_path.exists(), "Grafana 仪表盘 JSON 不存在"

    with open(dash_path) as f:
        data = json.load(f)

    assert "title" in data
    assert "panels" in data
    assert len(data["panels"]) >= 5  # 至少 5 个面板
    assert "GPU 利用率" in [p["title"] for p in data["panels"]]
    return True


@test("T14: Docker Compose P1 配置验证")
def test_docker_compose():
    compose_path = PROJECT_ROOT / "docker-compose.yml"
    assert compose_path.exists()

    content = compose_path.read_text()
    # P1 新增服务
    assert "prometheus:" in content, "缺少 Prometheus 服务"
    assert "grafana:" in content, "缺少 Grafana 服务"
    assert "trainer:" in content, "缺少 trainer 服务"
    assert "profiles:" in content, "缺少 profile 配置"

    # P1 新增卷
    assert "prometheus-data:" in content
    assert "grafana-data:" in content

    # Dockerfile.trainer
    trainer_df = PROJECT_ROOT / "Dockerfile.trainer"
    assert trainer_df.exists(), "Dockerfile.trainer 不存在"
    df_content = trainer_df.read_text()
    assert "peft" in df_content.lower()
    assert "transformers" in df_content.lower()
    return True


@test("T15: 全插件加载与依赖解析")
def test_full_plugin_loading():
    """测试所有 P1 插件的导入和依赖解析"""
    from core.context import BaizeContext
    from core.plugin_loader import PluginLoader
    from core.fiber import FiberManager

    # 所有插件类（含 ConfigPlugin）
    from plugins.config import ConfigPlugin
    from plugins.auth.plugin import AuthPlugin
    from plugins.audit.plugin import AuditPlugin
    from plugins.retriever.plugin import RetrieverPlugin
    from plugins.llm.plugin import LLMPlugin
    from plugins.knowledge.plugin import KnowledgePlugin
    from plugins.chat.plugin import ChatPlugin
    from plugins.channel_wechat.plugin import WeChatChannelPlugin
    from plugins.model_manager.plugin import ModelManagerPlugin
    from plugins.monitoring.plugin import MonitoringPlugin
    from plugins.evolve.plugin import ModelEvolvePlugin

    ctx = BaizeContext()
    fm = FiberManager(ctx)
    loader = PluginLoader(ctx, fm)

    # 注册所有插件（ConfigPlugin 必须在最前）
    loader.register(ConfigPlugin)
    loader.register(AuthPlugin)
    loader.register(AuditPlugin)
    loader.register(RetrieverPlugin)
    loader.register(LLMPlugin)
    loader.register(KnowledgePlugin)
    loader.register(ChatPlugin)
    loader.register(WeChatChannelPlugin)
    loader.register(ModelManagerPlugin)
    loader.register(MonitoringPlugin)
    loader.register(ModelEvolvePlugin)

    # 验证依赖解析
    ids = loader.get_plugin_ids()
    assert len(ids) == 11, f"期望 11 个插件，实际 {len(ids)}"

    # 验证依赖顺序：依赖项应在前面
    order = ids
    assert "core.config" in order, "core.config 必须存在"
    assert order.index("core.retriever") < order.index("core.knowledge")
    assert order.index("core.llm") < order.index("core.chat")
    assert order.index("core.knowledge") < order.index("core.chat")
    assert order.index("core.config") < order.index("core.model_manager")
    assert order.index("core.config") < order.index("core.monitoring")
    assert order.index("core.model_manager") < order.index("core.evolve")
    assert order.index("core.knowledge") < order.index("core.evolve")
    assert order.index("core.llm") < order.index("core.evolve")
    # core.config 应在所有依赖它的插件之前
    cfg_idx = order.index("core.config")
    assert cfg_idx < order.index("core.model_manager")
    assert cfg_idx < order.index("core.monitoring")

    # load_all 不应抛异常
    loader.load_all()
    assert len(ctx._plugins) == 11
    return True


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n{BOLD}EchoServe P1 — 集成测试{RESET}")
    print(f"{'=' * 50}")

    # 运行所有测试
    test_reranker_init()
    test_rerank_effect()
    test_vllm_client()
    test_metrics_collector()
    test_monitoring_plugin()
    test_model_manager()
    test_evolve_plugin()
    test_training_data_builder()
    test_evaluator()
    test_lora_trainer()
    test_scripts_syntax()
    test_prometheus_config()
    test_grafana_dashboard()
    test_docker_compose()
    test_full_plugin_loading()

    # 汇总
    total = passed + failed
    print(f"\n{'=' * 50}")
    print(f"  {BOLD}测试结果{RESET}")
    print(f"  {GREEN}通过: {passed}/{total}{RESET}")
    if failed > 0:
        print(f"  {RED}失败: {failed}/{total}{RESET}")
        for e in errors:
            print(f"    - {e}")
    else:
        print(f"  {GREEN}全部通过!{RESET}")
    print(f"{'=' * 50}\n")

    sys.exit(0 if failed == 0 else 1)
