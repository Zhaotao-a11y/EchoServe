"""
EchoServe V0.1.0 — 知识库导入脚本

用法：
    # 导入 JSONL 文件
    python scripts/ingest.py --file data/faq.jsonl

    # 导入目录下的所有 .txt 文件
    python scripts/ingest.py --dir data/docs --category 产品手册

JSONL 格式（每行一个 JSON）：
    {"id": "faq-1", "content": "退货政策：7天内...", "metadata": {"category": "售后"}}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import List, Dict, Any

# 将项目根目录加入 path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings
from core.context import BaizeContext
from core.fiber import FiberManager
from core.plugin_loader import PluginLoader

from plugins.retriever.plugin import RetrieverPlugin
from plugins.knowledge.plugin import KnowledgePlugin

logger = logging.getLogger("echoseve.ingest")


async def load_documents(file_path: Path) -> List[Dict[str, Any]]:
    """从 JSONL 文件加载文档"""
    documents = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
                if "content" not in doc:
                    logger.warning(f"Line {line_num}: missing 'content', skipping")
                    continue
                if "id" not in doc:
                    doc["id"] = f"doc-{uuid.uuid4().hex[:8]}"
                if "metadata" not in doc:
                    doc["metadata"] = {}
                documents.append(doc)
            except json.JSONDecodeError as e:
                logger.error(f"Line {line_num}: JSON error - {e}")

    return documents


async def load_directory(dir_path: Path, category: str = "") -> List[Dict[str, Any]]:
    """从目录加载所有 .txt 文件"""
    documents = []
    for txt_file in sorted(dir_path.glob("*.txt")):
        with open(txt_file, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            continue

        doc = {
            "id": f"doc-{txt_file.stem}",
            "content": content,
            "metadata": {
                "source": txt_file.name,
                "category": category or "uncategorized",
            },
        }
        documents.append(doc)

    return documents


async def main_async(args):
    """异步主函数"""
    # 初始化核心
    ctx = BaizeContext(settings)
    fiber_manager = FiberManager(ctx)
    loader = PluginLoader(ctx, fiber_manager)

    # 只注册需要的插件
    loader.register(RetrieverPlugin)
    loader.register(KnowledgePlugin)
    loader.load_all()

    # 启动
    await fiber_manager.start_all()

    try:
        documents = []

        if args.file:
            file_path = Path(args.file)
            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                return
            documents = await load_documents(file_path)
            logger.info(f"Loaded {len(documents)} docs from {file_path}")

        elif args.dir:
            dir_path = Path(args.dir)
            if not dir_path.exists():
                logger.error(f"Directory not found: {dir_path}")
                return
            documents = await load_directory(dir_path, args.category)
            logger.info(f"Loaded {len(documents)} docs from {dir_path}")

        else:
            logger.error("Must specify --file or --dir")
            return

        if not documents:
            logger.warning("No documents to ingest")
            return

        # 批量添加
        kb = ctx.inject("knowledge_base")
        await kb.add_documents_batch(documents)

        logger.info(f"✅ Ingest complete! Total docs: {kb.count_documents()}")

    finally:
        await fiber_manager.stop_all()
        await fiber_manager.destroy_all()


def main():
    parser = argparse.ArgumentParser(
        description="EchoServe MVP — Knowledge Base Ingestion Script"
    )
    parser.add_argument("--file", "-f", help="JSONL 文件路径")
    parser.add_argument("--dir", "-d", help="目录路径（导入所有 .txt）")
    parser.add_argument("--category", "-c", default="", help="文档分类标签")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s - %(message)s",
    )

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
