"""
EchoServe - Design Validation Script
======================================
End-to-end validation of the LoRA training pipeline design.
No PyTorch/Transformers required - validates structure, configs, and data flow.

Usage:
    python scripts/design_validator.py [--verbose]

Returns exit code 0 if all checks pass, 1 otherwise.
"""

import json
import sys
import re
import ast
import logging
import argparse
from pathlib import Path


logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ─── Paths ─────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_DIR = PROJECT_ROOT / "data"
PLUGINS_DIR = PROJECT_ROOT / "plugins"

# ─── Test Results ──────────────────────────────────────────
CHECKS_PASSED = []
CHECKS_FAILED = []


def record(check_name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    if passed:
        CHECKS_PASSED.append((check_name, detail))
        logger.info(f"[{status}] {check_name}")
    else:
        CHECKS_FAILED.append((check_name, detail))
        logger.error(f"[{status}] {check_name}: {detail}")


# ═══════════════════════════════════════════════════════════
# 1. 文件结构与存在性检查
# ═══════════════════════════════════════════════════════════

def check_file_structure():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 1: File Structure & Existence")
    logger.info("=" * 60)

    required_files = {
        "Data Prep Script": SCRIPTS_DIR / "prepare_training_data.py",
        "LoRA Training Script": SCRIPTS_DIR / "train_lora.py",
        "Merge/Export Script": SCRIPTS_DIR / "merge_and_export.py",
        "GPU Rental Guide": SCRIPTS_DIR / "GPU_RENTAL_GUIDE.md",
        "Knowledge Base": DATA_DIR / "knowledge" / "documents.jsonl",
        "Train Data": DATA_DIR / "training" / "train.jsonl",
        "Test Data": DATA_DIR / "training" / "test.jsonl",
        "Train Summary": DATA_DIR / "training" / "train_summary.json",
        "Knowledge Plugin": PLUGINS_DIR / "knowledge" / "plugin.py",
    }

    all_exist = True
    for name, path in required_files.items():
        exists = path.exists()
        if not exists:
            all_exist = False
        record(f"File: {name}", exists, f"{path} (size: {path.stat().st_size if exists else 'N/A'} bytes)")

    return all_exist


# ═══════════════════════════════════════════════════════════
# 2. 数据文件内容质量检查
# ═══════════════════════════════════════════════════════════

def check_data_quality():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 2: Data Quality (train.jsonl)")
    logger.info("=" * 60)

    train_path = DATA_DIR / "training" / "train.jsonl"
    if not train_path.exists():
        record("Data exists", False, "train.jsonl not found")
        return False

    total = 0
    valid = 0
    issues = []
    sample_sizes = []
    instruction_lengths = []
    input_lengths = []
    output_lengths = []

    with open(train_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                issues.append(f"Line {i}: invalid JSON")
                continue

            # Check required fields
            for field in ["instruction", "input", "output"]:
                if field not in item:
                    issues.append(f"Line {i}: missing field '{field}'")
                    break
            else:
                valid += 1
                sample_sizes.append(len(line))
                instruction_lengths.append(len(item["instruction"]))
                input_lengths.append(len(item["input"]))
                output_lengths.append(len(item["output"]))

    record("JSON format valid", valid == total, f"{valid}/{total} valid")
    record("Non-empty samples", total > 0, f"Total: {total}")

    if valid > 0:
        avg_input = sum(input_lengths) / len(input_lengths)
        avg_output = sum(output_lengths) / len(output_lengths)
        record("Input length reasonable", 0 < avg_input < 500, f"avg: {avg_input:.1f} chars")
        record("Output length reasonable", 0 < avg_output < 10000, f"avg: {avg_output:.1f} chars")
        record("Instruction non-empty", all(len(i) > 0 for i in instruction_lengths), "All have system prompt")

    if issues:
        record("No issues", False, f"{len(issues)} issues: {issues[:3]}")
    else:
        record("No issues", True, "Clean dataset")

    return valid == total and total > 0


# ═══════════════════════════════════════════════════════════
# 3. 数据解析逻辑验证 (模拟 prepare_training_data.py 核心逻辑)
# ═══════════════════════════════════════════════════════════

def check_qa_extraction():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 3: QA Extraction Logic (from Knowledge Base)")
    logger.info("=" * 60)

    kb_path = DATA_DIR / "knowledge" / "documents.jsonl"
    if not kb_path.exists():
        record("KB accessible", False, "documents.jsonl not found")
        return False

    # Parse first 10 documents and test extraction
    qa_pairs = []
    pattern = re.compile(r'问题[:：]\s*(.+?)(?:\n|$)', re.DOTALL)
    pattern_a = re.compile(r'回答[:：]\s*(.+)', re.DOTALL)

    with open(kb_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 20:  # Test first 20
                break
            try:
                doc = json.loads(line.strip())
                content = doc.get("content", "")

                q_match = pattern.search(content)
                a_match = pattern_a.search(content)

                if q_match and a_match:
                    q = q_match.group(1).strip()
                    a = a_match.group(1).strip()
                    if q and a and len(q) > 1 and len(a) > 1:
                        qa_pairs.append({"question": q, "answer": a})
            except (json.JSONDecodeError, AttributeError):
                continue

    record("QA pattern matching works", len(qa_pairs) > 0, f"Extracted {len(qa_pairs)} from 20 samples")

    if qa_pairs:
        record("Question non-empty", all(len(q["question"]) > 0 for q in qa_pairs), "All questions valid")
        record("Answer non-empty", all(len(q["answer"]) > 0 for q in qa_pairs), "All answers valid")

    return len(qa_pairs) > 0


# ═══════════════════════════════════════════════════════════
# 4. 训练脚本语法与结构检查 (AST解析)
# ═══════════════════════════════════════════════════════════

def check_script_syntax(script_name: str):
    path = SCRIPTS_DIR / script_name
    if not path.exists():
        return False, "File not found"

    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)

        # Check for required top-level structures
        has_main = False
        has_train_fn = False
        has_argparse = False
        functions = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions.append(node.name)
                if node.name == "train":
                    has_train_fn = True
            if isinstance(node, ast.If):
                if isinstance(node.test, ast.Compare):
                    for comp in ast.walk(node.test):
                        if isinstance(comp, ast.Constant) and comp.value == "__main__":
                            has_main = True
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "ArgumentParser":
                        has_argparse = True

        details = f"functions={functions}, has_main={has_main}, has_argparse={has_argparse}"
        return has_main, details
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    except Exception as e:
        return False, f"Parse error: {e}"


def check_train_lora_script():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 4: train_lora.py Structure & Syntax")
    logger.info("=" * 60)

    ok, detail = check_script_syntax("train_lora.py")
    record("Valid Python syntax", ok, detail)

    # Read and check key patterns
    path = SCRIPTS_DIR / "train_lora.py"
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()

    checks = {
        "Has LoRA config": "LoraConfig" in source,
        "Has PEFT": "get_peft_model" in source,
        "Has Trainer": "Trainer(" in source,
        "Has TrainingArguments": "TrainingArguments(" in source,
        "Has tokenizer loading": "AutoTokenizer" in source,
        "Has model loading": "AutoModelForCausalLM" in source,
        "Has QLoRA path": "load_in_4bit" in source,
        "Has save model": "save_model" in source,
        "Has training info": "training_info.json" in source,
        "Supports CPU test": "cpu_test" in source,
    }

    for name, result in checks.items():
        record(name, result, "Code pattern found" if result else "MISSING")

    return all(checks.values())


def check_merge_script():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 5: merge_and_export.py Structure & Syntax")
    logger.info("=" * 60)

    ok, detail = check_script_syntax("merge_and_export.py")
    record("Valid Python syntax", ok, detail)

    path = SCRIPTS_DIR / "merge_and_export.py"
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()

    checks = {
        "Has merge": "merge_and_unload" in source,
        "Has PEFT loading": "PeftModel" in source,
        "Has GGUF export": "gguf" in source.lower() or "GGUF" in source,
        "Has vLLM export": "vllm" in source.lower(),
        "Has training_info load": "training_info.json" in source,
        "Has Modelfile creation": "Modelfile" in source,
    }

    for name, result in checks.items():
        record(name, result, "Code pattern found" if result else "MISSING")

    return all(checks.values())


def check_data_prep_script():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 6: prepare_training_data.py Structure & Syntax")
    logger.info("=" * 60)

    ok, detail = check_script_syntax("prepare_training_data.py")
    record("Valid Python syntax", ok, detail)

    path = SCRIPTS_DIR / "prepare_training_data.py"
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()

    checks = {
        "Has KB loading": "load_kb" in source,
        "Has variant generation": "generate_variants" in source,
        "Has Alpaca format": "to_alpaca" in source,
        "Has dataset split": "split_dataset" in source,
        "Has validation": "validate_dataset" in source,
        "Has anti-forgetting": "GENERIC_DATA" in source,
        "Has JSONL save": "save_jsonl" in source,
    }

    for name, result in checks.items():
        record(name, result, "Code pattern found" if result else "MISSING")

    return all(checks.values())


# ═══════════════════════════════════════════════════════════
# 5. 超参数与配置合理性检查
# ═══════════════════════════════════════════════════════════

def check_hyperparameters():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 7: Hyperparameter Configuration")
    logger.info("=" * 60)

    # Read train_lora.py defaults
    path = SCRIPTS_DIR / "train_lora.py"
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()

    # Extract default values using regex
    def get_default(param, expected, comparison="eq"):
        pattern = rf'--{param}.*?default=([^,\)]+)'
        match = re.search(pattern, source)
        if match:
            val = match.group(1).strip()
            try:
                val_eval = ast.literal_eval(val)
            except:
                val_eval = val

            if comparison == "eq":
                return val_eval == expected, f"{param}={val_eval} (expected {expected})"
            elif comparison == "in_range":
                min_v, max_v = expected
                return min_v <= val_eval <= max_v, f"{param}={val_eval} (expected {min_v}-{max_v})"
            elif comparison == "gte":
                return val_eval >= expected, f"{param}={val_eval} (expected >= {expected})"
        return False, f"{param} not found"

    record("LoRA rank (r=8)", *get_default("lora-r", 8))
    record("LoRA alpha (16)", *get_default("lora-alpha", 16))
    record("Learning rate (2e-4)", *get_default("learning-rate", 2e-4))
    record("Epochs (3)", *get_default("epochs", 3))
    record("Batch size >=1", *get_default("batch-size", 1, "gte"))
    record("Gradient accumulation (4)", *get_default("gradient-accumulation", 4))
    record("Max length (1024)", *get_default("max-length", 1024))

    # Check target modules
    if "target_modules" in source:
        match = re.search(r'--target-modules.*?default="([^"]+)"', source)
        if match:
            modules = match.group(1).split(",")
            expected = ["q_proj", "k_proj", "v_proj", "o_proj"]
            all_present = all(m in modules for m in expected)
            record("Target modules (attention)", all_present, f"Found: {modules}")

    return True


# ═══════════════════════════════════════════════════════════
# 6. 路径依赖完整性检查
# ═══════════════════════════════════════════════════════════

def check_path_dependencies():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 8: Path Dependencies")
    logger.info("=" * 60)

    # Check that train_lora.py references correct data paths
    train_path = SCRIPTS_DIR / "train_lora.py"
    with open(train_path, "r", encoding="utf-8") as f:
        source = f.read()

    record("References train.jsonl", "train.jsonl" in source, "Default data path set")
    record("References test.jsonl", "test.jsonl" in source, "Default test path set")
    record("Output dir configurable", "output_dir" in source, "Output directory configurable")
    record("Handles missing data", "No training data found" in source, "Error handling present")

    # Check merge script references training_info.json
    merge_path = SCRIPTS_DIR / "merge_and_export.py"
    with open(merge_path, "r", encoding="utf-8") as f:
        source = f.read()

    record("References training_info.json", "training_info.json" in source, "Can read base model info")

    # Check data prep references knowledge base
    prep_path = SCRIPTS_DIR / "prepare_training_data.py"
    with open(prep_path, "r", encoding="utf-8") as f:
        source = f.read()

    record("References documents.jsonl", "documents.jsonl" in source, "KB path configured")

    return True


# ═══════════════════════════════════════════════════════════
# 7. 模拟端到端流程检查
# ═══════════════════════════════════════════════════════════

def check_end_to_end_flow():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 9: Simulated End-to-End Flow")
    logger.info("=" * 60)

    # Step 1: Data preparation produces expected outputs
    train_path = DATA_DIR / "training" / "train.jsonl"
    test_path = DATA_DIR / "training" / "test.jsonl"
    summary_path = DATA_DIR / "training" / "train_summary.json"

    record("Step 1a: train.jsonl exists", train_path.exists(), f"{train_path.stat().st_size if train_path.exists() else 'N/A'} bytes")
    record("Step 1b: test.jsonl exists", test_path.exists(), f"{test_path.stat().st_size if test_path.exists() else 'N/A'} bytes")
    record("Step 1c: summary exists", summary_path.exists(), "Metadata captured")

    # Step 2: Verify data flow into train script
    train_script = SCRIPTS_DIR / "train_lora.py"
    with open(train_script, "r", encoding="utf-8") as f:
        source = f.read()

    record("Step 2a: Loads JSONL", "load_jsonl" in source, "Data loading function defined")
    record("Step 2b: Parses Alpaca", "instruction" in source and "input" in source and "output" in source, "Alpaca format consumed")

    # Step 3: Verify merge script expects training output
    merge_script = SCRIPTS_DIR / "merge_and_export.py"
    with open(merge_script, "r", encoding="utf-8") as f:
        source = f.read()

    record("Step 3a: Reads LoRA path", "lora_path" in source, "Accepts LoRA output")
    record("Step 3b: Loads training_info", "training_info.json" in source, "Reads metadata for base model")
    record("Step 3c: Has merge logic", "merge_and_unload" in source, "Can merge adapter")
    record("Step 3d: Has export options", "export_gguf" in source or "export_vllm" in source, "Multiple export paths")

    # Step 4: Verify KnowledgePlugin integration
    kb_plugin = PLUGINS_DIR / "knowledge" / "plugin.py"
    with open(kb_plugin, "r", encoding="utf-8") as f:
        source = f.read()

    record("Step 4a: get_all_qa_pairs exists", "get_all_qa_pairs" in source, "Data source method available")
    record("Step 4b: get_all_documents exists", "get_all_documents" in source, "Document access method available")

    return True


# ═══════════════════════════════════════════════════════════
# 8. GPU Rental Guide 完整性检查
# ═══════════════════════════════════════════════════════════

def check_gpu_guide():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 10: GPU Rental Guide Completeness")
    logger.info("=" * 60)

    guide_path = SCRIPTS_DIR / "GPU_RENTAL_GUIDE.md"
    if not guide_path.exists():
        record("Guide exists", False, "Not found")
        return False

    with open(guide_path, "r", encoding="utf-8") as f:
        content = f.read()

    checks = {
        "Has platform recommendations": "AutoDL" in content or "RunPod" in content,
        "Has pricing info": "元" in content or "$" in content,
        "Has training commands": "python scripts/train_lora.py" in content,
        "Has export commands": "merge_and_export" in content,
        "Has hardware requirements": "GPU" in content or "显存" in content,
        "Has preparation steps": "pip install" in content or "依赖" in content,
    }

    for name, result in checks.items():
        record(name, result, "Content present" if result else "MISSING")

    return all(checks.values())


# ═══════════════════════════════════════════════════════════
# 9. 数据格式一致性检查（训练脚本期望 vs 实际数据）
# ═══════════════════════════════════════════════════════════

def check_format_compatibility():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 11: Data Format Compatibility")
    logger.info("=" * 60)

    # Read actual data
    train_path = DATA_DIR / "training" / "train.jsonl"
    if not train_path.exists():
        record("Train data available", False, "Not found")
        return False

    # Read first sample
    with open(train_path, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()

    try:
        sample = json.loads(first_line)
    except:
        record("Sample parseable", False, "Invalid JSON")
        return False

    # Check fields train_lora.py expects
    expected_fields = ["instruction", "input", "output"]
    has_all = all(f in sample for f in expected_fields)
    record("Has all Alpaca fields", has_all, f"Fields: {list(sample.keys())}")

    # Check field types
    types_ok = all(isinstance(sample.get(f, ""), str) for f in expected_fields)
    record("Fields are strings", types_ok, "All text fields")

    # Check train_lora.py reads these fields
    script_path = SCRIPTS_DIR / "train_lora.py"
    with open(script_path, "r", encoding="utf-8") as f:
        source = f.read()

    reads_instruction = '"instruction"' in source or 'get("instruction"' in source
    reads_input = '"input"' in source or 'get("input"' in source
    reads_output = '"output"' in source or 'get("output"' in source

    record("Script reads instruction", reads_instruction, "Field access pattern found")
    record("Script reads input", reads_input, "Field access pattern found")
    record("Script reads output", reads_output, "Field access pattern found")

    return has_all and types_ok and reads_instruction and reads_input and reads_output


# ═══════════════════════════════════════════════════════════
# 10. 错误处理与降级策略检查
# ═══════════════════════════════════════════════════════════

def check_error_handling():
    logger.info("\n" + "=" * 60)
    logger.info("CHECK 12: Error Handling & Resilience")
    logger.info("=" * 60)

    # Check train_lora.py
    train_path = SCRIPTS_DIR / "train_lora.py"
    with open(train_path, "r", encoding="utf-8") as f:
        source = f.read()

    record("Missing data check", "No training data found" in source, "Guards empty dataset")
    record("Dependency check", "Missing dependency" in source, "Checks imports")
    record("KeyboardInterrupt", "KeyboardInterrupt" in source, "Handles Ctrl+C gracefully")
    record("Save on interrupt", "interrupted" in source, "Saves checkpoint on interrupt")

    # Check merge_and_export.py
    merge_path = SCRIPTS_DIR / "merge_and_export.py"
    with open(merge_path, "r", encoding="utf-8") as f:
        source = f.read()

    record("Missing info check", "Training info not found" in source, "Guards missing metadata")
    record("Missing llama.cpp check", "llama.cpp not found" in source, "Handles missing converter")

    # Check prepare_training_data.py
    prep_path = SCRIPTS_DIR / "prepare_training_data.py"
    with open(prep_path, "r", encoding="utf-8") as f:
        source = f.read()

    record("KB missing check", "not found" in source or "No QA pairs" in source, "Guards missing KB")
    record("Invalid JSON skip", "Skip invalid JSON" in source, "Skips bad lines")
    record("Empty QA guard", "No QA pairs loaded" in source, "Prevents empty dataset")

    return True


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════

def run_all_checks(verbose: bool = False):
    logger.info("=" * 60)
    logger.info("EchoServe LoRA Pipeline - Design Validation")
    logger.info("=" * 60)
    logger.info("This script validates the entire training pipeline design")
    logger.info("without requiring PyTorch/Transformers/CUDA.")
    logger.info("=" * 60)

    # Run all checks
    results = []
    results.append(check_file_structure())
    results.append(check_data_quality())
    results.append(check_qa_extraction())
    results.append(check_train_lora_script())
    results.append(check_merge_script())
    results.append(check_data_prep_script())
    results.append(check_hyperparameters())
    results.append(check_path_dependencies())
    results.append(check_end_to_end_flow())
    results.append(check_gpu_guide())
    results.append(check_format_compatibility())
    results.append(check_error_handling())

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("VALIDATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total checks passed: {len(CHECKS_PASSED)}")
    logger.info(f"Total checks failed: {len(CHECKS_FAILED)}")

    if CHECKS_FAILED:
        logger.info("\nFailed checks:")
        for name, detail in CHECKS_FAILED:
            logger.info(f"  - {name}: {detail}")

    if verbose and CHECKS_PASSED:
        logger.info("\nPassed checks:")
        for name, detail in CHECKS_PASSED:
            logger.info(f"  + {name}: {detail}")

    logger.info("=" * 60)

    if len(CHECKS_FAILED) == 0:
        logger.info("ALL CHECKS PASSED - Pipeline design is valid!")
        logger.info("=" * 60)
        return 0
    else:
        logger.error(f"{len(CHECKS_FAILED)} CHECK(S) FAILED - Review above")
        logger.info("=" * 60)
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Design Validator")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show passed checks too")
    args = parser.parse_args()
    sys.exit(run_all_checks(verbose=args.verbose))
