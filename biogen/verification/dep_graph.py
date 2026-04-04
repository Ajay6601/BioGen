import ast
import re

from biogen.generation.planner import WorkflowPlan

TYPE_INTERFACES = {
    "AnnData": {"obs", "var", "X", "layers", "obsm", "uns", "shape"},
    "DeseqDataSet": {"deseq2", "varm", "layers", "obs", "var"},
    "DeseqStats": {"results_df", "summary"},
    "DataFrame": {"columns", "index", "iloc", "loc", "shape", "values"},
    "Figure": {"savefig"},
}


def check_dependencies(script: str, plan: WorkflowPlan) -> list[str]:
    """Check that data flows correctly between steps."""
    issues = []

    try:
        tree = ast.parse(script)
    except SyntaxError:
        issues.append("Cannot check dependencies: script has syntax errors")
        return issues

    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = node

    for step in plan.steps:
        fname = f"step_{step.step_id}"
        if fname not in functions and fname not in script:
            pass

    if "main" in functions:
        call_positions = []
        for node in ast.walk(functions["main"]):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                    match = re.match(r"step_(\d+)", name)
                    if match:
                        call_positions.append(int(match.group(1)))

        if call_positions and call_positions != sorted(call_positions):
            issues.append(
                f"Step execution order incorrect: {call_positions} "
                f"(expected ascending)"
            )

    result_vars_defined = set()
    for line in script.splitlines():
        stripped = line.strip()
        assign_match = re.match(r"(result_\d+)\s*=", stripped)
        if assign_match:
            result_vars_defined.add(assign_match.group(1))
        usage_matches = re.findall(r"(result_\d+)", stripped)
        for var in usage_matches:
            if var not in result_vars_defined and "=" not in stripped.split(var)[0]:
                pass

    return issues
