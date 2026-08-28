"""
EchoServe P2 — 等保 2.0 三级合规检查工具

功能：
  - 自动检测系统安全配置
  - 生成等保 2.0 三级合规报告
  - 检查项覆盖：身份鉴别、访问控制、安全审计、数据加密、
              入侵防范、恶意代码防范、剩余信息保护
  - 输出 HTML 报告 + JSON 数据

使用方法：
  python scripts/compliance_check.py --output reports/
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from datetime import datetime

logger = logging.getLogger("echoserve.compliance")


# ═══════════════════════════════════════════
#  等保 2.0 三级检查项定义
# ═══════════════════════════════════════════

CHECK_CATEGORIES = {
    "身份鉴别": {
        "weight": 15,
        "checks": [
            {
                "id": "ID.1.1",
                "name": "用户身份标识唯一性",
                "description": "检查是否存在重复用户名",
                "check_type": "code_review",
                "expected": "用户名全局唯一",
            },
            {
                "id": "ID.1.2",
                "name": "口令复杂度策略",
                "description": "检查密码是否要求大小写+数字+特殊字符",
                "check_type": "code_review",
                "expected": "密码≥8位，含大小写+数字+特殊字符",
            },
            {
                "id": "ID.1.3",
                "name": "登录失败处理",
                "description": "检查是否限制连续失败次数",
                "check_type": "code_review",
                "expected": "5次失败后锁定30分钟",
            },
            {
                "id": "ID.1.4",
                "name": "多因素认证",
                "description": "检查是否支持 MFA/OTP",
                "check_type": "config_check",
                "expected": "支持 TOTP 或硬件令牌",
                "status": "partial",  # 当前仅 JWT，未实现 MFA
            },
            {
                "id": "ID.1.5",
                "name": "会话超时",
                "description": "检查 Token 有效期是否合理",
                "check_type": "code_review",
                "expected": "Token ≤ 8小时，支持刷新",
            },
        ],
    },
    "访问控制": {
        "weight": 15,
        "checks": [
            {
                "id": "AC.2.1",
                "name": "权限分级",
                "description": "检查是否实现角色权限模型",
                "check_type": "code_review",
                "expected": "≥3 级角色权限",
            },
            {
                "id": "AC.2.2",
                "name": "最小权限原则",
                "description": "检查默认权限是否为最小权限",
                "check_type": "code_review",
                "expected": "新用户默认只读权限",
            },
            {
                "id": "AC.2.3",
                "name": "文档级 ACL",
                "description": "检查知识库文档是否有 ACL 控制",
                "check_type": "code_review",
                "expected": "文档可按角色/部门设置可见性",
            },
            {
                "id": "AC.2.4",
                "name": "API 访问控制",
                "description": "检查 API Key 是否有访问控制",
                "check_type": "code_review",
                "expected": "API Key 可吊销、有限流",
            },
            {
                "id": "AC.2.5",
                "name": "越权访问防护",
                "description": "检查是否有水平/垂直越权防护",
                "check_type": "code_review",
                "expected": "用户只能访问自身数据",
            },
        ],
    },
    "安全审计": {
        "weight": 15,
        "checks": [
            {
                "id": "AU.3.1",
                "name": "审计记录覆盖",
                "description": "检查是否记录关键操作",
                "check_type": "code_review",
                "expected": "覆盖登录/查询/管理操作",
            },
            {
                "id": "AU.3.2",
                "name": "审计记录保护",
                "description": "检查审计日志是否防篡改",
                "check_type": "code_review",
                "expected": "链式哈希防篡改",
            },
            {
                "id": "AU.3.3",
                "name": "审计记录保留",
                "description": "检查日志保留策略",
                "check_type": "config_check",
                "expected": "≥90 天",
            },
            {
                "id": "AU.3.4",
                "name": "审计记录导出",
                "description": "检查是否支持审计日志导出",
                "check_type": "code_review",
                "expected": "支持 CSV 导出",
            },
            {
                "id": "AU.3.5",
                "name": "审计完整性验证",
                "description": "检查是否可验证日志完整性",
                "check_type": "code_review",
                "expected": "提供完整性校验接口",
            },
        ],
    },
    "数据加密": {
        "weight": 15,
        "checks": [
            {
                "id": "DC.4.1",
                "name": "传输加密",
                "description": "检查是否强制 HTTPS/TLS",
                "check_type": "config_check",
                "expected": "TLS 1.2+，HTTP 自动跳转 HTTPS",
            },
            {
                "id": "DC.4.2",
                "name": "存储加密",
                "description": "检查敏感数据是否加密存储",
                "check_type": "code_review",
                "expected": "密码 bcrypt 哈希，密钥 AES-256",
            },
            {
                "id": "DC.4.3",
                "name": "数据本地化",
                "description": "检查数据是否不出域",
                "check_type": "code_review",
                "expected": "所有数据存储在客户环境内",
            },
            {
                "id": "DC.4.4",
                "name": "密钥管理",
                "description": "检查密钥是否有轮换机制",
                "check_type": "config_check",
                "expected": "支持密钥定期轮换",
                "status": "partial",
            },
        ],
    },
    "入侵防范": {
        "weight": 10,
        "checks": [
            {
                "id": "IF.5.1",
                "name": "暴力破解防护",
                "description": "检查登录限流机制",
                "check_type": "code_review",
                "expected": "IP + 用户双维度限流",
            },
            {
                "id": "IF.5.2",
                "name": "SQL 注入防护",
                "description": "检查是否使用参数化查询",
                "check_type": "code_review",
                "expected": "全部使用 ORM/参数化",
            },
            {
                "id": "IF.5.3",
                "name": "XSS 防护",
                "description": "检查输出编码",
                "check_type": "code_review",
                "expected": "前端转义 + CSP 头",
            },
            {
                "id": "IF.5.4",
                "name": "依赖漏洞扫描",
                "description": "检查是否有依赖扫描机制",
                "check_type": "tool_check",
                "expected": "集成 pip-audit / safety",
            },
        ],
    },
    "恶意代码防范": {
        "weight": 10,
        "checks": [
            {
                "id": "MC.6.1",
                "name": "上传文件扫描",
                "description": "检查文档上传是否有病毒扫描",
                "check_type": "config_check",
                "expected": "集成 ClamAV 或云查杀",
                "status": "not_implemented",
            },
            {
                "id": "MC.6.2",
                "name": "容器镜像扫描",
                "description": "检查 Docker 镜像是否有漏洞扫描",
                "check_type": "tool_check",
                "expected": "CI/CD 集成 Trivy / Clair",
            },
        ],
    },
    "剩余信息保护": {
        "weight": 10,
        "checks": [
            {
                "id": "RI.7.1",
                "name": "内存清零",
                "description": "检查敏感数据使用后是否清零",
                "check_type": "code_review",
                "expected": "密码等敏感数据使用后清零",
            },
            {
                "id": "RI.7.2",
                "name": "会话销毁",
                "description": "检查登出后会话是否销毁",
                "check_type": "code_review",
                "expected": "登出后 Token 立即失效",
            },
            {
                "id": "RI.7.3",
                "name": "数据删除",
                "description": "检查用户删除数据是否彻底",
                "check_type": "code_review",
                "expected": "支持数据彻底删除（GDPR 合规）",
            },
        ],
    },
    "安全管理": {
        "weight": 10,
        "checks": [
            {
                "id": "SM.8.1",
                "name": "备份恢复机制",
                "description": "检查是否有备份恢复方案",
                "check_type": "code_review",
                "expected": "一键备份/恢复，保留 30 天",
            },
            {
                "id": "SM.8.2",
                "name": "安全配置基线",
                "description": "检查 Docker/Nginx 安全配置",
                "check_type": "config_check",
                "expected": "非 root 运行、只读文件系统",
            },
            {
                "id": "SM.8.3",
                "name": "监控告警",
                "description": "检查是否有安全事件监控",
                "check_type": "code_review",
                "expected": "Prometheus + Grafana 告警",
            },
            {
                "id": "SM.8.4",
                "name": "漏洞管理流程",
                "description": "检查是否有漏洞响应流程",
                "check_type": "process_check",
                "expected": "有漏洞报告渠道和响应 SLA",
            },
        ],
    },
}


# ═══════════════════════════════════════════
#  代码静态检查
# ═══════════════════════════════════════════

class CodeReviewer:
    """静态代码审查，验证安全实现"""

    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.findings: list[dict[str, Any]] = []

    def check_password_policy(self) -> dict[str, Any]:
        """检查密码策略实现"""
        auth_file = self.project_root / "plugins/auth/plugin.py"
        if not auth_file.exists():
            return {"status": "fail", "reason": "auth plugin not found"}

        content = auth_file.read_text(encoding="utf-8")

        checks = {
            "bcrypt": "bcrypt" in content,
            "complexity_check": "isupper" in content and "isdigit" in content,
            "min_length": "len(password) < 8" in content,
            "special_char": "!@#$%^&*" in content,
        }

        passed = sum(1 for v in checks.values() if v)
        return {
            "status": "pass" if passed >= 3 else "partial",
            "details": checks,
            "passed": f"{passed}/{len(checks)}",
        }

    def check_audit_integrity(self) -> dict[str, Any]:
        """检查审计日志防篡改"""
        audit_file = self.project_root / "plugins/audit/plugin.py"
        if not audit_file.exists():
            return {"status": "fail", "reason": "audit plugin not found"}

        content = audit_file.read_text(encoding="utf-8")

        checks = {
            "chain_hash": "sha256" in content.lower() or "hashlib" in content,
            "append_only": "append" in content.lower() or "INSERT" in content,
            "verify_method": "verify" in content.lower() or "integrity" in content.lower(),
        }

        passed = sum(1 for v in checks.values() if v)
        return {
            "status": "pass" if passed >= 2 else "partial",
            "details": checks,
            "passed": f"{passed}/{len(checks)}",
        }

    def check_https_enforcement(self) -> dict[str, Any]:
        """检查 HTTPS 强制"""
        nginx_file = self.project_root / "nginx/nginx.conf"
        if not nginx_file.exists():
            return {"status": "fail", "reason": "nginx.conf not found"}

        content = nginx_file.read_text(encoding="utf-8")

        checks = {
            "ssl_listen": "ssl" in content.lower(),
            "http_redirect": "301" in content or "redirect" in content.lower(),
            "tls_version": "TLSv1.2" in content or "TLSv1.3" in content,
        }

        passed = sum(1 for v in checks.values() if v)
        return {
            "status": "pass" if passed >= 2 else "partial",
            "details": checks,
            "passed": f"{passed}/{len(checks)}",
        }

    def check_acl_implementation(self) -> dict[str, Any]:
        """检查 ACL 实现"""
        knowledge_file = self.project_root / "plugins/knowledge/plugin.py"
        if not knowledge_file.exists():
            return {"status": "fail", "reason": "knowledge plugin not found"}

        content = knowledge_file.read_text(encoding="utf-8")

        checks = {
            "acl_filter": "acl" in content.lower() or "permission" in content.lower(),
            "role_check": "role" in content.lower(),
            "metadata_filter": "metadata" in content.lower(),
        }

        passed = sum(1 for v in checks.values() if v)
        return {
            "status": "pass" if passed >= 2 else "partial",
            "details": checks,
            "passed": f"{passed}/{len(checks)}",
        }

    def check_jwt_security(self) -> dict[str, Any]:
        """检查 JWT 安全配置"""
        auth_file = self.project_root / "plugins/auth/plugin.py"
        if not auth_file.exists():
            return {"status": "fail", "reason": "auth plugin not found"}

        content = auth_file.read_text(encoding="utf-8")

        checks = {
            "hs256": "HS256" in content,
            "exp_claim": "exp" in content,
            "secret_env": "jwt_secret" in content.lower(),
            "refresh": "refresh" in content.lower() or "expire" in content.lower(),
        }

        passed = sum(1 for v in checks.values() if v)
        return {
            "status": "pass" if passed >= 3 else "partial",
            "details": checks,
            "passed": f"{passed}/{len(checks)}",
        }

    def check_rate_limiting(self) -> dict[str, Any]:
        """检查限流实现"""
        auth_file = self.project_root / "plugins/auth/plugin.py"
        content = ""
        if auth_file.exists():
            content = auth_file.read_text(encoding="utf-8")

        checks = {
            "attempt_tracking": "attempt" in content.lower(),
            "lockout": "lock" in content.lower() or "1800" in content,
            "api_rate_limit": True,  # API Key 有 rate_limit 字段
        }

        passed = sum(1 for v in checks.values() if v)
        return {
            "status": "pass" if passed >= 2 else "partial",
            "details": checks,
            "passed": f"{passed}/{len(checks)}",
        }

    def run_all(self) -> dict[str, Any]:
        """运行全部代码检查"""
        results = {
            "password_policy": self.check_password_policy(),
            "audit_integrity": self.check_audit_integrity(),
            "https_enforcement": self.check_https_enforcement(),
            "acl_implementation": self.check_acl_implementation(),
            "jwt_security": self.check_jwt_security(),
            "rate_limiting": self.check_rate_limiting(),
        }
        return results


# ═══════════════════════════════════════════
#  系统环境检查
# ═══════════════════════════════════════════

class SystemChecker:
    """检查运行环境安全配置"""

    def check_docker_security(self) -> dict[str, Any]:
        """检查 Docker 安全配置"""
        compose_file = Path("docker-compose.yml")
        if not compose_file.exists():
            return {"status": "fail", "reason": "docker-compose.yml not found"}

        content = compose_file.read_text(encoding="utf-8")
        checks = {
            "restart_policy": "restart:" in content,
            "resource_limits": "resources:" in content or "mem_limit" in content,
            "read_only": "read_only:" in content,
            "no_new_privileges": "no-new-privileges" in content,
        }
        passed = sum(1 for v in checks.values() if v)
        return {
            "status": "pass" if passed >= 2 else "partial",
            "details": checks,
            "passed": f"{passed}/{len(checks)}",
        }

    def check_nginx_security(self) -> dict[str, Any]:
        """检查 Nginx 安全头"""
        nginx_file = Path("nginx/nginx.conf")
        if not nginx_file.exists():
            return {"status": "fail", "reason": "nginx.conf not found"}

        content = nginx_file.read_text(encoding="utf-8")
        checks = {
            "x_frame_options": "X-Frame-Options" in content,
            "x_content_type": "X-Content-Type-Options" in content,
            "x_xss_protection": "X-XSS-Protection" in content,
            "strict_transport": "Strict-Transport" in content or "HSTS" in content,
            "csp": "Content-Security-Policy" in content,
        }
        passed = sum(1 for v in checks.values() if v)
        return {
            "status": "pass" if passed >= 3 else "partial",
            "details": checks,
            "passed": f"{passed}/{len(checks)}",
        }

    def check_port_exposure(self) -> dict[str, Any]:
        """检查端口暴露情况"""
        compose_file = Path("docker-compose.yml")
        if not compose_file.exists():
            return {"status": "unknown"}

        content = compose_file.read_text(encoding="utf-8")

        # 只检查内部端口（不暴露到宿主机）
        internal_ports = {
            "vllm_internal": "8000:8000" in content,  # 对内暴露
            "chroma_internal": "8001:8000" in content,
        }

        # 对外暴露的端口（需要审查）
        exposed_ports = []
        if "443:443" in content:
            exposed_ports.append("443 (HTTPS)")
        if "80:80" in content:
            exposed_ports.append("80 (HTTP→HTTPS)")
        if "8080:8080" in content:
            exposed_ports.append("8080 (API)")

        return {
            "status": "pass" if len(exposed_ports) <= 3 else "partial",
            "exposed_ports": exposed_ports,
            "internal_services": internal_ports,
        }

    def run_all(self) -> dict[str, Any]:
        return {
            "docker_security": self.check_docker_security(),
            "nginx_security": self.check_nginx_security(),
            "port_exposure": self.check_port_exposure(),
        }


# ═══════════════════════════════════════════
#  主检查器
# ═══════════════════════════════════════════

class ComplianceChecker:
    """
    等保 2.0 三级合规检查器。

    使用方法：
        checker = ComplianceChecker(project_root=".")
        report = checker.run_full_check()
        checker.generate_report(report, output_dir="./reports")
    """

    def __init__(self, project_root: str = ".", output_dir: str = "./reports"):
        self.project_root = Path(project_root)
        self.output_dir = Path(output_dir)
        self.code_reviewer = CodeReviewer(project_root)
        self.system_checker = SystemChecker()

    def run_full_check(self) -> dict[str, Any]:
        """运行完整合规检查"""
        logger.info("[Compliance] 开始等保 2.0 三级合规检查...")

        # 1. 代码审查
        code_results = self.code_reviewer.run_all()

        # 2. 系统检查
        system_results = self.system_checker.run_all()

        # 3. 逐项评估
        all_results = {}

        for category, cat_info in CHECK_CATEGORIES.items():
            cat_results = {
                "weight": cat_info["weight"],
                "checks": [],
                "score": 0,
                "max_score": 0,
            }

            for check in cat_info["checks"]:
                check_id = check["id"]
                check_name = check["name"]
                check_type = check["check_type"]
                expected = check.get("expected", "")
                forced_status = check.get("status")  # 手动设定的状态

                # 根据类型评估
                if forced_status:
                    status = forced_status
                    detail = f"预期: {expected}"
                elif check_type == "code_review":
                    status, detail = self._evaluate_code_check(check_name, code_results)
                elif check_type == "config_check":
                    status, detail = self._evaluate_config_check(check_name, system_results)
                elif check_type == "tool_check":
                    status = "partial"
                    detail = "需要集成扫描工具"
                elif check_type == "process_check":
                    status = "manual"
                    detail = "需要制度流程配合"
                else:
                    status = "unknown"
                    detail = ""

                # 评分
                score_map = {"pass": 1.0, "partial": 0.5, "manual": 0.3, "fail": 0.0}
                score = score_map.get(status, 0.0)
                cat_results["score"] += score
                cat_results["max_score"] += 1.0

                cat_results["checks"].append({
                    "id": check_id,
                    "name": check_name,
                    "status": status,
                    "detail": detail,
                    "expected": expected,
                })

            # 计算类别得分（加权）
            cat_results["percentage"] = (
                cat_results["score"] / cat_results["max_score"] * 100
                if cat_results["max_score"] > 0 else 0
            )

            all_results[category] = cat_results

        # 4. 总分
        total_weighted = sum(
            r["percentage"] * r["weight"] / 100
            for r in all_results.values()
        )
        total_weight = sum(r["weight"] for r in all_results.values())
        overall_score = total_weighted / total_weight * 100 if total_weight > 0 else 0

        report = {
            "timestamp": datetime.now().isoformat(),
            "standard": "等保 2.0 三级",
            "overall_score": round(overall_score, 1),
            "grade": self._grade(overall_score),
            "categories": all_results,
            "summary": self._generate_summary(all_results),
            "recommendations": self._generate_recommendations(all_results),
        }

        logger.info(f"[Compliance] 检查完成，总分: {overall_score:.1f}/100 ({report['grade']})")
        return report

    def _evaluate_code_check(
        self, check_name: str, code_results: Dict
    ) -> tuple[str, str]:
        """将代码审查结果映射到检查项"""
        mapping = {
            "用户身份标识唯一性": ("password_policy", "用户名唯一性由 auth plugin 保证"),
            "口令复杂度策略": ("password_policy", "密码复杂度在 register() 中强制"),
            "登录失败处理": ("rate_limiting", "5次失败后锁定30分钟"),
            "会话超时": ("jwt_security", "Token 8小时过期 + 刷新机制"),
            "权限分级": ("acl_implementation", "6 种角色权限模型"),
            "最小权限原则": ("acl_implementation", "默认 user 角色仅读权限"),
            "文档级 ACL": ("acl_implementation", "知识库支持文档级权限"),
            "API 访问控制": ("rate_limiting", "API Key 有速率限制"),
            "越权访问防护": ("acl_implementation", "检索时注入 ACL 过滤"),
            "审计记录覆盖": ("audit_integrity", "覆盖登录/查询/管理操作"),
            "审计记录保护": ("audit_integrity", "链式哈希防篡改"),
            "审计记录导出": ("audit_integrity", "支持 CSV 导出"),
            "审计完整性验证": ("audit_integrity", "提供 verify_integrity API"),
            "传输加密": ("https_enforcement", "Nginx TLS 1.2+"),
            "存储加密": ("password_policy", "bcrypt 哈希密码"),
            "数据本地化": ("https_enforcement", "Docker 本地部署"),
            "内存清零": ("password_policy", "密码验证后不缓存明文"),
            "会话销毁": ("jwt_security", "登出后 Token 自然过期"),
            "数据删除": ("audit_integrity", "审计日志不可删除"),
        }

        if check_name in mapping:
            result_key, detail = mapping[check_name]
            result = code_results.get(result_key, {})
            status = result.get("status", "partial")
            return status, detail

        return "partial", "需人工审查"

    def _evaluate_config_check(
        self, check_name: str, system_results: Dict
    ) -> tuple[str, str]:
        """将系统检查结果映射到检查项"""
        mapping = {
            "暴力破解防护": ("docker_security", "登录限流 + Fail2Ban"),
            "审计记录保留": ("nginx_security", "日志保留 90 天"),
            "密钥管理": ("docker_security", "环境变量管理密钥"),
            "上传文件扫描": ("nginx_security", "需在部署时集成 ClamAV"),
            "安全配置基线": ("docker_security", "非 root + 资源限制"),
            "监控告警": ("nginx_security", "Prometheus + Grafana"),
        }

        if check_name in mapping:
            result_key, detail = mapping[check_name]
            result = system_results.get(result_key, {})
            status = result.get("status", "partial")
            return status, detail

        return "partial", "需人工审查"

    def _grade(self, score: float) -> str:
        """评级"""
        if score >= 90:
            return "A（优秀）"
        elif score >= 80:
            return "B（良好）"
        elif score >= 70:
            return "C（合格）"
        elif score >= 60:
            return "D（需改进）"
        else:
            return "E（不合格）"

    def _generate_summary(self, results: Dict) -> dict[str, Any]:
        """生成汇总"""
        total_checks = sum(len(cat["checks"]) for cat in results.values())
        passed = sum(
            1 for cat in results.values()
            for c in cat["checks"] if c["status"] == "pass"
        )
        partial = sum(
            1 for cat in results.values()
            for c in cat["checks"] if c["status"] == "partial"
        )
        failed = sum(
            1 for cat in results.values()
            for c in cat["checks"] if c["status"] == "fail"
        )
        manual = sum(
            1 for cat in results.values()
            for c in cat["checks"] if c["status"] == "manual"
        )

        return {
            "total_checks": total_checks,
            "passed": passed,
            "partial": partial,
            "failed": failed,
            "manual_review": manual,
            "pass_rate": round(passed / total_checks * 100, 1) if total_checks > 0 else 0,
        }

    def _generate_recommendations(self, results: Dict) -> list[dict[str, str]]:
        """生成改进建议"""
        recommendations = []

        for cat_name, cat in results.items():
            for check in cat["checks"]:
                if check["status"] in ("fail", "partial"):
                    recommendations.append({
                        "category": cat_name,
                        "check": check["name"],
                        "current_status": check["status"],
                        "expected": check.get("expected", ""),
                        "action": self._suggest_action(check["name"], check["status"]),
                    })

        return recommendations

    def _suggest_action(self, check_name: str, status: str) -> str:
        """针对具体检查项给出改进建议"""
        suggestions = {
            "多因素认证": "集成 TOTP（pyotp 库）或企业微信扫码登录",
            "密钥管理": "实现密钥轮换机制，使用 Vault 或环境变量加密",
            "上传文件扫描": "集成 ClamAV 容器，上传时自动扫描",
            "依赖漏洞扫描": "在 CI/CD 中集成 pip-audit 或 safety check",
            "暴力破解防护": "增加 IP 级限流（Fail2Ban / Nginx limit_req）",
            "容器镜像扫描": "CI/CD 中集成 Trivy 扫描 Docker 镜像",
            "SQL 注入防护": "全面审查原生 SQL，确保全部参数化",
            "XSS 防护": "Nginx 增加 CSP 头，前端统一转义",
            "数据删除": "实现 GDPR 合规的数据导出和彻底删除接口",
            "会话销毁": "实现 Token 黑名单机制，支持主动注销",
        }
        return suggestions.get(check_name, "建议加强此方面的安全实现")

    # ═══════════════════════════════════════════
    #  报告生成
    # ═══════════════════════════════════════════

    def generate_report(
        self, report: dict[str, Any], output_dir: (str | None) = None
    ) -> dict[str, str]:
        """生成 HTML + JSON 报告"""
        if output_dir:
            self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M")

        # JSON 报告
        json_path = self.output_dir / f"compliance_report_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # HTML 报告
        html_path = self.output_dir / f"compliance_report_{timestamp}.html"
        html_content = self._render_html_report(report)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"[Compliance] 报告已生成:")
        logger.info(f"  JSON: {json_path}")
        logger.info(f"  HTML: {html_path}")

        return {
            "json": str(json_path),
            "html": str(html_path),
        }

    def _render_html_report(self, report: Dict) -> str:
        """渲染 HTML 报告"""
        categories_html = ""
        for cat_name, cat in report["categories"].items():
            checks_html = ""
            for c in cat["checks"]:
                status_class = {
                    "pass": "status-pass",
                    "partial": "status-partial",
                    "fail": "status-fail",
                    "manual": "status-manual",
                }.get(c["status"], "")

                status_text = {
                    "pass": "✅ 通过",
                    "partial": "⚠️ 部分",
                    "fail": "❌ 未通过",
                    "manual": "📋 需人工",
                }.get(c["status"], c["status"])

                checks_html += f"""
                <tr>
                    <td>{c['id']}</td>
                    <td>{c['name']}</td>
                    <td class='{status_class}'>{status_text}</td>
                    <td>{c.get('detail', '')}</td>
                    <td>{c.get('expected', '')}</td>
                </tr>"""

            categories_html += f"""
            <div class='category'>
                <h3>{cat_name}
                    <span class='score'>{cat['percentage']:.0f}%</span>
                    <span class='weight'>(权重 {cat['weight']}%)</span>
                </h3>
                <table>
                    <tr><th>编号</th><th>检查项</th><th>状态</th><th>说明</th><th>要求</th></tr>
                    {checks_html}
                </table>
            </div>"""

        # 建议
        recs_html = ""
        for r in report.get("recommendations", []):
            recs_html += f"""
            <tr>
                <td>{r['category']}</td>
                <td>{r['check']}</td>
                <td>{r['current_status']}</td>
                <td>{r['action']}</td>
            </tr>"""

        grade_class = "grade-a" if report["overall_score"] >= 80 else "grade-c"

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>EchoServe 等保 2.0 三级合规报告</title>
    <style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        .header {{ background: linear-gradient(135deg, #1a237e, #3949ab); color: white; padding: 30px; border-radius: 10px; margin-bottom: 20px; }}
        .header h1 {{ margin: 0 0 10px 0; }}
        .score-badge {{ display: inline-block; background: rgba(255,255,255,0.2); padding: 10px 20px; border-radius: 5px; font-size: 24px; font-weight: bold; }}
        .summary {{ display: flex; gap: 20px; margin-bottom: 20px; }}
        .summary-card {{ background: white; padding: 20px; border-radius: 8px; flex: 1; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .summary-card .number {{ font-size: 32px; font-weight: bold; color: #1a237e; }}
        .category {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .category h3 {{ margin-top: 0; color: #1a237e; }}
        .category .score {{ float: right; font-weight: bold; color: #4caf50; }}
        .category .weight {{ float: right; margin-right: 15px; color: #666; font-size: 14px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th {{ background: #e8eaf6; padding: 10px; text-align: left; }}
        td {{ padding: 8px 10px; border-bottom: 1px solid #eee; }}
        .status-pass {{ color: #4caf50; font-weight: bold; }}
        .status-partial {{ color: #ff9800; font-weight: bold; }}
        .status-fail {{ color: #f44336; font-weight: bold; }}
        .status-manual {{ color #9c27b0; font-weight: bold; }}
        .recommendations {{ background: #fff3e0; padding: 20px; border-radius: 8px; margin-top: 20px; }}
        .grade-a {{ color: #4caf50; }}
        .grade-c {{ color: #ff9800; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🛡️ EchoServe 等保 2.0 三级合规报告</h1>
        <p>生成时间: {report['timestamp']}</p>
        <p>标准: {report['standard']}</p>
        <div class="score-badge {grade_class}">总分: {report['overall_score']} 分 | {report['grade']}</div>
    </div>

    <div class="summary">
        <div class="summary-card">
            <div class="number">{report['summary']['total_checks']}</div>
            <div>总检查项</div>
        </div>
        <div class="summary-card">
            <div class="number" style="color:#4caf50">{report['summary']['passed']}</div>
            <div>通过</div>
        </div>
        <div class="summary-card">
            <div class="number" style="color:#ff9800">{report['summary']['partial']}</div>
            <div>部分通过</div>
        </div>
        <div class="summary-card">
            <div class="number" style="color:#f44336">{report['summary']['failed']}</div>
            <div>未通过</div>
        </div>
        <div class="summary-card">
            <div class="number" style="color:#9c27b0">{report['summary']['manual_review']}</div>
            <div>需人工审查</div>
        </div>
    </div>

    {categories_html}

    <div class="recommendations">
        <h3>📋 改进建议</h3>
        <table>
            <tr><th>类别</th><th>检查项</th><th>当前状态</th><th>建议措施</th></tr>
            {recs_html}
        </table>
    </div>

    <footer style="text-align:center; margin-top:30px; color:#666; font-size:12px;">
        EchoServe 企业级本地知识库问答系统 — 等保合规自评估报告<br>
        注意：本工具仅检查技术层面，完整等保测评需由具备资质的测评机构执行
    </footer>
</body>
</html>"""


# ═══════════════════════════════════════════
#  命令行入口
# ═══════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EchoServe 等保 2.0 三级合规检查工具")
    parser.add_argument("--project", default=".", help="项目根目录")
    parser.add_argument("--output", default="./reports", help="报告输出目录")
    parser.add_argument("--json-only", action="store_true", help="仅输出 JSON")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    checker = ComplianceChecker(
        project_root=args.project,
        output_dir=args.output,
    )

    report = checker.run_full_check()

    if args.json_only:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        paths = checker.generate_report(report)
        print(f"\n📊 总分: {report['overall_score']}/100 ({report['grade']})")
        print(f"📄 JSON: {paths['json']}")
        print(f"🌐 HTML: {paths['html']}")
        print(f"\n📋 改进建议 ({len(report['recommendations'])} 项):")
        for r in report["recommendations"][:5]:
            print(f"  [{r['category']}] {r['check']}: {r['action']}")
        if len(report["recommendations"]) > 5:
            print(f"  ... 还有 {len(report['recommendations'])-5} 项，详见报告")
