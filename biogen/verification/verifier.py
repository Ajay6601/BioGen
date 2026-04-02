from dataclasses import dataclass, field
from biogen.generation.planner import WorkflowPlan
from biogen.verification.ast_checker import check_ast
from biogen.verification.dep_graph import check_dependencies
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
    execution_ok: bool = False
    execution_output: str = ""

    def fail(self, msg: str):
        self.passed = False
        self.issues.append(msg)


def verify_script(
    script: str,
    plan: WorkflowPlan,
    data_path: str,
    output_dir: str,
) -> VerificationResult:
    """Run all verification checks on a generated script."""
    result = VerificationResult()

    # 1. AST check
    log.info("  Check 1/4: AST validation...")
    ast_issues = check_ast(script)
    if ast_issues:
        for issue in ast_issues:
            result.fail(f"[AST] {issue}")
    else:
        result.ast_ok = True
        log.info("    ? AST valid")

    # 2. Dependency graph check
    log.info("  Check 2/4: Dependency graph...")
    dep_issues = check_dependencies(script, plan)
    if dep_issues:
        for issue in dep_issues:
            result.fail(f"[DEP] {issue}")
    else:
        result.deps_ok = True
        log.info("    ? Dependencies valid")

    # 3. Parameter constraints
    log.info("  Check 3/4: Parameter constraints...")
    param_issues = check_params(script, plan.analysis_type)
    if param_issues:
        for issue in param_issues:
            result.fail(f"[PARAM] {issue}")
    else:
        result.params_ok = True
        log.info("    ? Parameters valid")

    # 4. Sandbox execution (only if first 3 pass)
    if result.ast_ok:
        log.info("  Check 4/4: Sandbox execution...")
        exec_ok, exec_output = execute_in_sandbox(script, data_path, output_dir)
        result.execution_output = exec_output
        if not exec_ok:
            result.fail(f"[EXEC] {exec_output[:500]}")
        else:
            result.execution_ok = True
            log.info("    ? Execution successful")
    else:
        log.warning("  Skipping sandbox (AST failed)")
        result.fail("[EXEC] Skipped — AST errors present")

    result.passed = all([
        result.ast_ok, result.deps_ok, result.params_ok, result.execution_ok
    ])
    return result
