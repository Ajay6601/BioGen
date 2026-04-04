from dataclasses import dataclass, field

from biogen.generation.planner import WorkflowPlan
from biogen.verification.api_validator import validate_api_calls
from biogen.verification.ast_checker import check_ast
from biogen.verification.dep_graph import check_dependencies
from biogen.verification.order_checker import check_operation_order
from biogen.verification.param_constraints import check_params
from biogen.verification.sandbox import execute_in_sandbox
from biogen.utils.logger import get_logger

log = get_logger("biogen.verifier")


@dataclass
class VerificationResult:
    passed: bool = True
    issues: list[str] = field(default_factory=list)
    ast_ok: bool = False
    deps_ok: bool = False
    params_ok: bool = False
    api_ok: bool = False
    order_ok: bool = False
    execution_ok: bool = False
    execution_output: str = ""

    def fail(self, msg: str) -> None:
        self.passed = False
        self.issues.append(msg)


def verify_script(
    script: str,
    plan: WorkflowPlan,
    data_path: str,
    output_dir: str,
) -> VerificationResult:
    """Run all 6 verification checks on a generated script."""
    result = VerificationResult()

    log.info("  Check 1/6: AST validation...")
    ast_issues = check_ast(script)
    if ast_issues:
        for issue in ast_issues:
            result.fail(f"[AST] {issue}")
    else:
        result.ast_ok = True
        log.info("    ? AST valid")

    log.info("  Check 2/6: API signature validation...")
    api_issues = validate_api_calls(script)
    if api_issues:
        for issue in api_issues:
            result.fail(f"[API] {issue}")
    else:
        result.api_ok = True
        log.info("    ? API signatures valid")

    log.info("  Check 3/6: Operation order...")
    order_issues = check_operation_order(script, plan.analysis_type)
    errors = [i for i in order_issues if "[ERROR]" in i]
    warnings = [i for i in order_issues if "[WARNING]" in i]
    if errors:
        for issue in errors:
            result.fail(f"[ORDER] {issue}")
    else:
        result.order_ok = True
        if warnings:
            for w in warnings:
                log.warning(f"    ? {w}")
        log.info("    ? Operation order valid")

    log.info("  Check 4/6: Dependency graph...")
    dep_issues = check_dependencies(script, plan)
    if dep_issues:
        for issue in dep_issues:
            result.fail(f"[DEP] {issue}")
    else:
        result.deps_ok = True
        log.info("    ? Dependencies valid")

    log.info("  Check 5/6: Parameter constraints...")
    param_issues = check_params(script, plan.analysis_type)
    if param_issues:
        for issue in param_issues:
            result.fail(f"[PARAM] {issue}")
    else:
        result.params_ok = True
        log.info("    ? Parameters valid")

    if result.ast_ok and result.api_ok:
        log.info("  Check 6/6: Sandbox execution...")
        exec_ok, exec_output = execute_in_sandbox(script, data_path, output_dir)
        result.execution_output = exec_output
        if not exec_ok:
            result.fail(f"[EXEC] {exec_output[:500]}")
        else:
            result.execution_ok = True
            log.info("    ? Execution successful")
    else:
        log.warning("  Skipping sandbox (static checks failed)")
        result.fail("[EXEC] Skipped; fix static errors first")

    result.passed = all([
        result.ast_ok,
        result.api_ok,
        result.order_ok,
        result.deps_ok,
        result.params_ok,
        result.execution_ok,
    ])
    return result
