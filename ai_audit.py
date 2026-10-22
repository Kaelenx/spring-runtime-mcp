#!/usr/bin/env python3
"""
AI Codebase Auditor: 针对 AI 生成的 Python 项目进行反“表面合规”与隐患扫描。
用法: python3 ai_audit.py [src_dir] [test_dir] (默认: src tests)
"""

import ast
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# 终端颜色
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GREEN = "\033[92m"
RESET = "\033[0m"
BOLD = "\033[1m"

BLOCKING_CALLS = {
    "time.sleep",
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.request",
    "urllib.request.urlopen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_output",
}

SUSPICIOUS_KEYWORDS = {"fixture", "mock", "dummy", "sample", "fake", "hardcode"}


class ASTAnalyzer(ast.NodeVisitor):
    def __init__(self, filepath: Path, is_test: bool = False):
        self.filepath = filepath
        self.is_test = is_test
        self.findings: list[tuple[str, int, str]] = []
        self.in_async_func = False
        self.in_protocol = False
        self.defined_funcs: dict[str, int] = {}
        self.used_names: set[str] = set()

    def add_finding(self, category: str, lineno: int, msg: str):
        self.findings.append((category, lineno, msg))

    def _get_call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            prefix = self._get_call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    # 1. 检查生产代码导入测试/Mock模块
    def visit_Import(self, node: ast.Import):
        if not self.is_test:
            for alias in node.names:
                if any(
                    k in alias.name.lower()
                    for k in ("pytest", "unittest", "mock", "tests", "fixture")
                ):
                    self.add_finding(
                        "FAKE_WIRING",
                        node.lineno,
                        f"生产代码导入了测试/模拟模块: '{alias.name}'",
                    )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if not self.is_test and node.module:
            if any(
                k in node.module.lower()
                for k in ("pytest", "unittest", "mock", "tests", "fixture")
            ):
                self.add_finding(
                    "FAKE_WIRING",
                    node.lineno,
                    f"生产代码从测试模块导入: 'from {node.module} import ...'",
                )
        self.generic_visit(node)

    # 2. 检查吞异常 (Exception Swallowing)
    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if not self.is_test:
            exc_name = self._get_call_name(node.type) if node.type else "裸 except:"
            is_broad = exc_name in ("Exception", "BaseException", "裸 except:")

            # 判断异常块是否只有 pass / ... / return 常量或空容器
            if len(node.body) == 1:
                stmt = node.body[0]
                if isinstance(stmt, ast.Pass) or (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value is Ellipsis
                ):
                    self.add_finding(
                        "SWALLOW_ERR",
                        node.lineno,
                        f"捕获 [{exc_name}] 后直接 pass/... 静默吞错",
                    )
                elif isinstance(stmt, ast.Return) and is_broad:
                    val = stmt.value
                    if val is None or isinstance(
                        val, (ast.Constant, ast.Dict, ast.List, ast.Set, ast.Tuple)
                    ):
                        self.add_finding(
                            "SWALLOW_ERR",
                            node.lineno,
                            f"捕获宽泛异常 [{exc_name}] 后直接返回静态/空值 ({ast.unparse(stmt)})，可能掩盖底层故障",
                        )
        self.generic_visit(node)

    # 3. 检查伪异步与空壳函数
    def visit_ClassDef(self, node: ast.ClassDef):
        previous = self.in_protocol
        bases = [self._get_call_name(base) for base in node.bases]
        self.in_protocol = any(base.rsplit(".", 1)[-1] == "Protocol" for base in bases)
        self.generic_visit(node)
        self.in_protocol = previous

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        prev_async = self.in_async_func
        self.in_async_func = True
        self._check_function_body(node)
        self.generic_visit(node)
        self.in_async_func = prev_async

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._check_function_body(node)
        self.generic_visit(node)

    def _check_function_body(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        if not self.is_test and not node.name.startswith("__"):
            self.defined_funcs[node.name] = node.lineno

            # 剥离 docstring 后看函数体是否为空壳
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]

            if len(body) == 1:
                stmt = body[0]
                if isinstance(stmt, ast.Pass) or (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value is Ellipsis
                ):
                    # Protocol 方法和 @overload 声明没有运行时函数体，不是占位实现。
                    decorators = [self._get_call_name(d) for d in node.decorator_list]
                    is_overload = any(
                        d.rsplit(".", 1)[-1] == "overload" for d in decorators
                    )
                    if (
                        not self.in_protocol
                        and not is_overload
                        and not any("abstract" in d for d in decorators)
                    ):
                        self.add_finding(
                            "HOLLOW_IMPL",
                            node.lineno,
                            f"空壳函数 '{node.name}' (仅包含 pass 或 ...)",
                        )
                elif isinstance(stmt, ast.Raise):
                    exc_str = ast.unparse(stmt.exc) if stmt.exc else ""
                    if "NotImplementedError" in exc_str:
                        self.add_finding(
                            "HOLLOW_IMPL",
                            node.lineno,
                            f"未实现函数 '{node.name}' (抛出 NotImplementedError)",
                        )

        # 如果是测试函数，检查是否没有任何 assert
        if self.is_test and node.name.startswith("test_"):
            has_assert = any(isinstance(sub, ast.Assert) for sub in ast.walk(node))
            has_pytest_raises = any(
                isinstance(sub, ast.Attribute) and sub.attr in ("raises", "warns")
                for sub in ast.walk(node)
            )
            has_mock_assert = any(
                isinstance(sub, ast.Attribute) and sub.attr.startswith("assert_")
                for sub in ast.walk(node)
            )
            if not (has_assert or has_pytest_raises or has_mock_assert):
                self.add_finding(
                    "WEAK_TEST",
                    node.lineno,
                    f"测试函数 '{node.name}' 内部未发现直接的 assert 或 pytest.raises",
                )

    def visit_Call(self, node: ast.Call):
        call_name = self._get_call_name(node.func)
        if isinstance(node.func, ast.Attribute):
            # self._helper() / obj._helper() is a real reference even when the
            # helper is never referenced as a bare Name node elsewhere.
            self.used_names.add(node.func.attr)
        if self.in_async_func and not self.is_test:
            if call_name in BLOCKING_CALLS:
                self.add_finding(
                    "FAKE_ASYNC",
                    node.lineno,
                    f"async 函数内调用了同步阻塞方法: '{call_name}()'",
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if isinstance(node.ctx, ast.Load):
            # Method references passed as callables (asyncio.to_thread(self._x))
            # are real uses too, not dead code.
            self.used_names.add(node.attr)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load):
            self.used_names.add(node.id)
        elif isinstance(node.ctx, ast.Store) and not self.is_test:
            if any(k in node.id.lower() for k in SUSPICIOUS_KEYWORDS):
                self.add_finding(
                    "FAKE_WIRING",
                    node.lineno,
                    f"生产代码中存在可疑假数据变量命名: '{node.id}'",
                )
        self.generic_visit(node)

    # 4. 检查测试断言作弊 (恒真或过度宽泛断言)
    def visit_Assert(self, node: ast.Assert):
        if self.is_test:
            if isinstance(node.test, ast.Constant) and bool(node.test.value) is True:
                self.add_finding(
                    "WEAK_TEST", node.lineno, f"恒真断言作弊: '{ast.unparse(node)}'"
                )
            elif isinstance(node.test, ast.Compare):
                # 检查 assert status in ["ok", "error", "partial"] 这类放宽断言
                for op, comp in zip(node.test.ops, node.test.comparators):
                    if isinstance(op, ast.In) and isinstance(
                        comp, (ast.List, ast.Tuple, ast.Set)
                    ):
                        elts_str = [ast.unparse(e).lower() for e in comp.elts]
                        # "partial" is a valid evidence state in this project, not a
                        # failure. Only success+failure mixes are suspicious.
                        failure_markers = (
                            "err",
                            "fail",
                            "denied",
                            "invalid",
                            "unavailable",
                        )
                        if any("ok" in s or "success" in s for s in elts_str) and any(
                            marker in s for s in elts_str for marker in failure_markers
                        ):
                            self.add_finding(
                                "WEAK_TEST",
                                node.lineno,
                                f"疑似放宽的混合状态断言: '{ast.unparse(node)}'",
                            )
        self.generic_visit(node)


def run_rg_checks(src_dir: Path, test_dir: Path):
    print(f"\n{BOLD}{CYAN}=== [阶段 1/2] Ripgrep 快速文本与注释门禁扫描 ==={RESET}")
    if not shutil.which("rg"):
        print(f"{YELLOW}未检测到 ripgrep (rg)，跳过高速正则扫描阶段。{RESET}")
        return

    checks = [
        (
            "门禁绕过 (type: ignore / noqa / pragma)",
            r"#\s*(type:\s*ignore|noqa|pragma:\s*no\s*cover)",
            src_dir,
        ),
        (
            "遗留标记 (TODO / FIXME / HACK / XXX)",
            r"#\s*(TODO|FIXME|HACK|XXX|PLACEHOLDER)\b",
            src_dir,
        ),
        (
            "生产代码硬编码可疑测试路径或字面量",
            r"['\"].*(fixtures?|mock_data|dummy|sample_).*['\"]",
            src_dir,
        ),
        (
            "测试代码跳过标记 (skip / xfail)",
            r"pytest\.mark\.(skip|skipif|xfail)|unittest\.skip",
            test_dir,
        ),
    ]

    for title, pattern, target in checks:
        if not target.exists():
            continue
        cmd = [
            "rg",
            "-n",
            "--no-heading",
            "--color=never",
            "-g",
            "*.py",
            pattern,
            str(target),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        lines = [line for line in res.stdout.strip().splitlines() if line]
        if lines:
            print(f"\n{YELLOW}▶ {title} (共 {len(lines)} 处):{RESET}")
            for line in lines[:15]:
                print(f"  {line}")
            if len(lines) > 15:
                print(f"  ... 其余 {len(lines) - 15} 处已折叠")
        else:
            print(f"{GREEN}✓ {title}: 0 命中{RESET}")


def run_ast_checks(src_dir: Path, test_dir: Path):
    print(f"\n{BOLD}{CYAN}=== [阶段 2/2] AST 抽象语法树深度结构审计 ==={RESET}")
    all_findings: dict[str, list[str]] = defaultdict(list)
    global_defined_private_funcs: dict[str, list[tuple[Path, int]]] = defaultdict(list)
    global_used_names: set[str] = set()

    # 扫描源码与测试目录
    targets = [(src_dir, False), (test_dir, True)]
    for base_dir, is_test in targets:
        if not base_dir.exists():
            continue
        for py_file in sorted(base_dir.rglob("*.py")):
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except Exception as e:
                all_findings["SYNTAX_ERR"].append(f"{py_file}: 无法解析 AST ({e})")
                continue

            analyzer = ASTAnalyzer(py_file, is_test=is_test)
            analyzer.visit(tree)

            for cat, lineno, msg in analyzer.findings:
                all_findings[cat].append(f"{py_file}:{lineno} -> {msg}")

            global_used_names.update(analyzer.used_names)
            if not is_test:
                for func_name, lineno in analyzer.defined_funcs.items():
                    # 重点追踪单下划线开头的内部辅助函数是否沦为死代码
                    if func_name.startswith("_"):
                        global_defined_private_funcs[func_name].append(
                            (py_file, lineno)
                        )

    # 汇总跨文件死代码（定义了私有辅助函数但全仓从未引用）
    for func_name, locations in global_defined_private_funcs.items():
        if func_name not in global_used_names:
            for py_file, lineno in locations:
                all_findings["DEAD_CODE"].append(
                    f"{py_file}:{lineno} -> 内部函数 '{func_name}' 在全仓(含测试)均未被调用"
                )

    labels = {
        "FAKE_WIRING": "🚨 假数据回退与测试耦合 (Fake Wiring / Fixture Fallback)",
        "SWALLOW_ERR": "🚨 静默吞异常与掩盖故障 (Silent Exception Swallowing)",
        "HOLLOW_IMPL": "⚠️ 空壳实现与占位符 (Hollow Functions)",
        "FAKE_ASYNC": "⚠️ 伪异步中的同步阻塞调用 (Blocking Call in Async)",
        "WEAK_TEST": "⚠️ 脆弱/作弊测试断言 (Tautological or Relaxed Tests)",
        "DEAD_CODE": "🧹 疑似遗留死代码 (Unreferenced Private Helpers)",
    }

    total_issues = 0
    for key, title in labels.items():
        items = all_findings.get(key, [])
        total_issues += len(items)
        if items:
            print(
                f"\n{RED if '🚨' in title else YELLOW}{BOLD}{title} ({len(items)} 处):{RESET}"
            )
            for item in items[:20]:
                print(f"  {item}")
            if len(items) > 20:
                print(f"  ... 其余 {len(items) - 20} 处已折叠")
        else:
            print(f"{GREEN}✓ {title}: 通过{RESET}")

    return total_issues


if __name__ == "__main__":
    src_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("src")
    test_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("tests")

    print(
        f"{BOLD}开始扫描项目 -> 源码目录: [{src_path}] | 测试目录: [{test_path}]{RESET}"
    )
    run_rg_checks(src_path, test_path)
    issues = run_ast_checks(src_path, test_path)

    print("\n" + "=" * 60)
    if issues > 0:
        print(
            f"{YELLOW}{BOLD}审计完成: AST 共发现 {issues} 处可疑点，请结合上方明细逐一排查。{RESET}"
        )
        sys.exit(1)
    else:
        print(f"{GREEN}{BOLD}审计完成: AST 结构层未发现典型 AI 偷懒与作弊模式！{RESET}")
        sys.exit(0)
