"""
Validates that bioinformatics operations are called in a scientifically
correct order. Catches silent correctness bugs where the script runs
but produces wrong results.

This is the check that separates "it works" from "it's right."
"""
import re

from biogen.utils.logger import get_logger

log = get_logger("biogen.order_checker")

SCANPY_ORDER_RULES = [
    {
        "op": "sc.pp.filter_cells",
        "must_precede": ["sc.pp.normalize_total", "sc.pp.log1p", "sc.pp.pca"],
        "msg": "Cell filtering should happen before normalization/PCA",
        "severity": "error",
    },
    {
        "op": "sc.pp.filter_genes",
        "must_precede": ["sc.pp.normalize_total", "sc.pp.log1p"],
        "msg": "Gene filtering should happen before normalization",
        "severity": "warning",
    },
    {
        "op": "sc.pp.normalize_total",
        "must_precede": ["sc.pp.log1p", "sc.pp.highly_variable_genes", "sc.pp.pca"],
        "msg": "Normalization must happen before log1p/HVG/PCA",
        "severity": "error",
    },
    {
        "op": "sc.pp.log1p",
        "must_precede": ["sc.pp.highly_variable_genes", "sc.pp.pca", "sc.pp.scale"],
        "msg": "Log transform must happen before HVG selection/PCA/scaling",
        "severity": "error",
    },
    {
        "op": "sc.pp.highly_variable_genes",
        "must_precede": ["sc.pp.pca"],
        "msg": "HVG selection should happen before PCA",
        "severity": "warning",
    },
    {
        "op": "sc.pp.pca",
        "must_precede": ["sc.pp.neighbors"],
        "msg": "PCA must happen before neighbor computation",
        "severity": "error",
    },
    {
        "op": "sc.pp.neighbors",
        "must_precede": ["sc.tl.umap", "sc.tl.leiden", "sc.tl.louvain", "sc.tl.paga"],
        "msg": "Neighbors must be computed before UMAP/clustering",
        "severity": "error",
    },
]

PYDESEQ2_ORDER_RULES = [
    {
        "op": "DeseqDataSet",
        "must_precede": [".deseq2()", "DeseqStats"],
        "msg": "DeseqDataSet must be created before running .deseq2() or DeseqStats",
        "severity": "error",
    },
    {
        "op": ".deseq2()",
        "must_precede": ["DeseqStats", "results_df"],
        "msg": ".deseq2() must run before creating DeseqStats",
        "severity": "error",
    },
]

ANTI_PATTERNS = [
    {
        "pattern": r"sc\.pp\.log1p.*\n.*sc\.pp\.log1p",
        "msg": "Double log1p detected — data would be log-transformed twice",
        "severity": "error",
    },
    {
        "pattern": r"sc\.pp\.normalize_total.*\n.*sc\.pp\.normalize_total",
        "msg": "Double normalization detected — data would be normalized twice",
        "severity": "error",
    },
    {
        "pattern": r"sc\.pp\.log1p.*sc\.pp\.normalize_total",
        "msg": "log1p called BEFORE normalize_total — wrong order, will produce incorrect results",
        "severity": "error",
    },
    {
        "pattern": r"sc\.tl\.umap(?!.*sc\.pp\.neighbors)",
        "msg": "UMAP called but sc.pp.neighbors may not have been called first",
        "severity": "warning",
    },
]


def _find_operation_positions(script: str, operations: list[str]) -> dict[str, int]:
    positions: dict[str, int] = {}
    lines = script.splitlines()
    for i, line in enumerate(lines):
        for op in operations:
            if op in line and op not in positions:
                stripped = line.strip()
                if not stripped.startswith("#"):
                    positions[op] = i
    return positions


def check_operation_order(script: str, analysis_type: str) -> list[str]:
    issues: list[str] = []

    if analysis_type in ("scrna_clustering", "scrna_seq"):
        rules = SCANPY_ORDER_RULES
    elif analysis_type in ("bulk_rnaseq_de", "bulk_rnaseq"):
        rules = PYDESEQ2_ORDER_RULES
    else:
        rules = SCANPY_ORDER_RULES + PYDESEQ2_ORDER_RULES

    all_ops: set[str] = set()
    for rule in rules:
        all_ops.add(rule["op"])
        all_ops.update(rule["must_precede"])

    positions = _find_operation_positions(script, list(all_ops))

    for rule in rules:
        op = rule["op"]
        if op not in positions:
            continue

        op_line = positions[op]
        for must_follow in rule["must_precede"]:
            if must_follow in positions:
                follow_line = positions[must_follow]
                if op_line > follow_line:
                    severity = rule["severity"]
                    issues.append(
                        f"[{severity.upper()}] {rule['msg']}: "
                        f"'{op}' (line {op_line + 1}) comes after "
                        f"'{must_follow}' (line {follow_line + 1})"
                    )

    for ap in ANTI_PATTERNS:
        if re.search(ap["pattern"], script, re.DOTALL):
            issues.append(f"[{ap['severity'].upper()}] {ap['msg']}")

    if issues:
        log.warning(f"Order check found {len(issues)} issues")
    else:
        log.info("Operation order check passed")

    return issues
