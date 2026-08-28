#!/usr/bin/env python3
"""
EchoServe Evolution Module End-to-End Test with Ollama qwen2.5:0.5b
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# sys.path 由根目录 conftest.py 统一管理，此处不再重复设置

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("e2e.evolution")


async def run_e2e():
    print("\n" + "=" * 70)
    print("  EchoServe Evolution E2E - Ollama qwen2.5:0.5b")
    print("=" * 70)
    results = []

    # ─── Step 0: Ollama ──────────────────────────────────────
    print("\n[Step 0] Ollama service check...")
    import httpx
    ollama_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{ollama_url}/api/tags")
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            assert ollama_model in names, f"{ollama_model} not in {names}"
        print(f"  [PASS] Ollama ok, model={ollama_model}")
        results.append(True)
    except Exception as e:
        print(f"  [FAIL] {e}")
        return False

    # ─── Step 1: LLM inference ───────────────────────────────
    print("\n[Step 1] LLM inference test...")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{ollama_url}/api/chat", json={
                "model": ollama_model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Say hello in one word."},
                ],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 20},
            })
            r.raise_for_status()
            reply = r.json().get("message", {}).get("content", "")
            assert reply.strip(), "Empty response"
        print(f"  [PASS] LLM reply: '{reply.strip()[:60]}'")
        results.append(True)
    except Exception as e:
        print(f"  [FAIL] {e}")
        return False

    # ─── Step 2: Init + Start plugin ─────────────────────────
    print("\n[Step 2] EvolutionPlugin lifecycle...")
    from config.settings import settings
    from core.context import BaizeContext
    from core.fiber import FiberManager
    from core.plugin_loader import PluginLoader
    from plugins.evolution.plugin import EvolutionPlugin

    ctx = BaizeContext(settings)
    fm = FiberManager(ctx)
    loader = PluginLoader(ctx, fm)
    loader.register(EvolutionPlugin)
    loader.load_all()

    fiber = fm._fibers.get("core.evolution")
    plugin: EvolutionPlugin = fiber.plugin

    try:
        await fm.start_all()
        print(f"  [PASS] Plugin started: {plugin.plugin_id} v{plugin.plugin_version}")
        results.append(True)
    except Exception as e:
        print(f"  [FAIL] Start failed: {e}")
        import traceback; traceback.print_exc()
        return False

    status = plugin.get_status()
    print(f"  [PASS] Status: degradation={status['degradation_level']}, "
          f"store.init={status['store']['initialized']}, "
          f"experiments={len(status['experiments'])}")

    # ─── Step 3: Data collection ─────────────────────────────
    print("\n[Step 3] Data collection (skill + feedback events)...")
    try:
        for i in range(5):
            plugin._on_skill_execute({
                "session_id": f"sess_{i}",
                "skill_id": "rag_retrieval",
                "input": {"query": f"question {i}"},
                "output": {"answer": f"answer {i}"},
                "success": True,
                "latency_ms": 50 + i * 10,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            plugin._on_user_feedback({
                "session_id": f"sess_{i}",
                "feedback_type": "like" if i < 3 else "dislike",
                "comment": f"feedback {i}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        await asyncio.sleep(2)

        stats = plugin.collector.get_stats()
        print(f"  [PASS] Collector: received={stats['total_received']}, "
              f"written={stats['total_written']}, backlog={stats['backlog_size']}")
        assert stats["total_received"] > 0, "No records collected"
        results.append(True)
    except Exception as e:
        print(f"  [FAIL] {e}")
        import traceback; traceback.print_exc()
        results.append(False)

    # ─── Step 4: A/B experiment ──────────────────────────────
    print("\n[Step 4] A/B experiment lifecycle...")
    try:
        # Register param in pool first (required before creating experiment)
        from plugins.evolution.phase2.param_pool import ParamDefinition
        plugin._param_pool.register(ParamDefinition(
            name="top_k", description="Retrieval top K",
            current_value=5, candidate_values=[3, 5, 10], value_type="int",
        ))
        exp_id = await plugin.create_experiment(
            param_name="top_k",
            candidate_values=[3, 5, 10],
            eval_metric="retrieval_hit_rate",
            min_samples=5,
            max_samples=20,
        )
        print(f"  [PASS] Experiment created: {exp_id}")

        for i in range(20):
            user = f"ab_user_{i:03d}"
            assignment = plugin._experimenter.assign_user(user, "top_k")
            assigned = assignment.assigned_value
            if isinstance(assigned, int):
                metric = assigned / 10.0 + (i % 5) * 0.02
            else:
                metric = 0.3 + (i % 5) * 0.02
            plugin._experimenter.record_metric(user, "top_k", metric)

        stats = plugin._experimenter.get_assignment_stats(exp_id)
        print(f"  [PASS] Assignment stats: {json.dumps(stats, default=str)}")

        if plugin._evaluator:
            result = plugin._evaluator.evaluate(exp_id)
            if result:
                print(f"  [PASS] Evaluation: significant={result.is_significant}, "
                      f"t={result.t_statistic:.3f}, p={result.p_value:.3f}, "
                      f"d={result.cohens_d:.3f}")
                if result.is_significant:
                    plugin._evaluator.commit(exp_id, result)
                    print(f"  [PASS] Winner committed")
            else:
                print(f"  [PASS] Evaluation: insufficient samples (None returned)")
        results.append(True)
    except Exception as e:
        print(f"  [FAIL] {e}")
        import traceback; traceback.print_exc()
        results.append(False)

    # ─── Step 5: Pattern mining ──────────────────────────────
    print("\n[Step 5] Pattern mining...")
    try:
        traces = []
        for i in range(10):
            traces.append({
                "session_id": f"trace_{i}",
                "skill_id": "rag_retrieval",
                "input": {"query": f"reset password variant {i}"},
                "output": {"answer": f"settings > security > reset variant {i}"},
                "success": True,
                "latency_ms": 40 + i * 5,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        patterns = plugin._pattern_miner.mine_from_dicts(traces)
        print(f"  [PASS] Patterns found: {len(patterns) if patterns else 0}")
        results.append(True)
    except Exception as e:
        print(f"  [FAIL] {e}")
        import traceback; traceback.print_exc()
        results.append(False)

    # ─── Step 6: Failover / degradation ──────────────────────
    print("\n[Step 6] Failover / degradation...")
    try:
        from plugins.evolution.shared.failover import DegradationLevel
        level = plugin.failover.current_level
        print(f"  [PASS] Current level: {level}")

        await plugin.failover.manual_degrade(DegradationLevel.LEVEL_1, "e2e_test")
        print(f"  [PASS] After manual degrade L1: {plugin.failover.current_level}")

        await plugin.failover.recover(DegradationLevel.NORMAL)
        print(f"  [PASS] After recover: {plugin.failover.current_level}")
        results.append(True)
    except Exception as e:
        print(f"  [FAIL] {e}")
        import traceback; traceback.print_exc()
        results.append(False)

    # ─── Step 7: Final status ────────────────────────────────
    print("\n[Step 7] Final status...")
    final = plugin.get_status()
    print(f"  [PASS] degradation={final['degradation_level']}, "
          f"experiments={len(final['experiments'])}, "
          f"store.writes={final['store']['write_count']}")
    results.append(True)

    # ─── Stop ────────────────────────────────────────────────
    print("\n[Step 8] Stopping plugin...")
    try:
        await fm.stop_all()
        await fm.destroy_all()
        print(f"  [PASS] Stopped & destroyed")
    except Exception as e:
        print(f"  [WARN] {e}")

    # ─── Summary ─────────────────────────────────────────────
    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    labels = ["Ollama", "LLM inference", "Plugin lifecycle", "Data collection",
              "A/B experiment", "Pattern mining", "Failover", "Final status"]
    for i, ok in enumerate(results):
        print(f"  {'[PASS]' if ok else '[FAIL]'} {labels[i]}")
    print(f"\n  {passed}/{total} passed")
    if passed == total:
        print("=" * 70)
        print("  ALL TESTS PASSED")
    else:
        print("=" * 70)
        print("  SOME TESTS FAILED")
    print("=" * 70 + "\n")

    return passed == total


if __name__ == "__main__":
    ok = asyncio.run(run_e2e())
    sys.exit(0 if ok else 1)
