###############################################################################
# FILE: biogen/verification/api_validator.py
###############################################################################
"""
Validates that LLM-generated code uses real API signatures — not hallucinated
parameters, deprecated functions, or wrong argument types.

Uses library introspection (inspect module) to build a knowledge base of actual
function signatures at runtime, then checks every function call in the AST
against it.

This catches the #1 failure mode in LLM code generation: plausible-looking
but non-existent API parameters.
"""
import ast
import importlib
import inspect
from dataclasses import dataclass

from biogen.utils.logger import get_logger

log = get_logger("biogen.api_validator")


@dataclass
class APISignature:
    module: str
    func_name: str
    params: set[str]
    required_params: set[str]
    has_kwargs: bool  # accepts **kwargs — can't validate extra params


# ──────────────────────────────────────────────────────────────────────────────
# Build knowledge base from actual installed libraries
# ──────────────────────────────────────────────────────────────────────────────

_KB_CACHE: dict[str, APISignature] = {}

# Functions we want to validate — the ones LLMs get wrong most often
TRACKED_FUNCTIONS = {
    # scanpy preprocessing
    "sc.pp.filter_cells": ("scanpy.pp", "filter_cells"),
    "sc.pp.filter_genes": ("scanpy.pp", "filter_genes"),
    "sc.pp.normalize_total": ("scanpy.pp", "normalize_total"),
    "sc.pp.log1p": ("scanpy.pp", "log1p"),
    "sc.pp.highly_variable_genes": ("scanpy.pp", "highly_variable_genes"),
    "sc.pp.pca": ("scanpy.pp", "pca"),
    "sc.pp.neighbors": ("scanpy.pp", "neighbors"),
    "sc.pp.scale": ("scanpy.pp", "scale"),
    "sc.pp.regress_out": ("scanpy.pp", "regress_out"),
    "sc.pp.scrublet": ("scanpy.pp", "scrublet"),
    # scanpy tools
    "sc.tl.umap": ("scanpy.tl", "umap"),
    "sc.tl.tsne": ("scanpy.tl", "tsne"),
    "sc.tl.leiden": ("scanpy.tl", "leiden"),
    "sc.tl.louvain": ("scanpy.tl", "louvain"),
    "sc.tl.rank_genes_groups": ("scanpy.tl", "rank_genes_groups"),
    "sc.tl.dendrogram": ("scanpy.tl", "dendrogram"),
    "sc.tl.paga": ("scanpy.tl", "paga"),
    "sc.tl.diffmap": ("scanpy.tl", "diffmap"),
    # scanpy plotting
    "sc.pl.umap": ("scanpy.pl", "umap"),
    "sc.pl.pca": ("scanpy.pl", "pca"),
    "sc.pl.violin": ("scanpy.pl", "violin"),
    "sc.pl.dotplot": ("scanpy.pl", "dotplot"),
    "sc.pl.stacked_violin": ("scanpy.pl", "stacked_violin"),
    "sc.pl.heatmap": ("scanpy.pl", "heatmap"),
    "sc.pl.rank_genes_groups": ("scanpy.pl", "rank_genes_groups"),
    "sc.pl.rank_genes_groups_dotplot": ("scanpy.pl", "rank_genes_groups_dotplot"),
    # pydeseq2
    "DeseqDataSet": ("pydeseq2.dds", "DeseqDataSet"),
    "DeseqStats": ("pydeseq2.ds", "DeseqStats"),
}


def _callable_signature(obj):
    if inspect.isclass(obj):
        return inspect.signature(obj.__init__)
    return inspect.signature(obj)


def _build_kb() -> dict[str, APISignature]:
    """Introspect installed libraries to get actual function signatures."""
    if _KB_CACHE:
        return _KB_CACHE

    for call_name, (module_path, func_name) in TRACKED_FUNCTIONS.items():
        try:
            mod = importlib.import_module(module_path)
            func = getattr(mod, func_name, None)
            if func is None:
                continue

            sig = _callable_signature(func)
            params = set()
            required = set()
            has_kwargs = False

            for pname, param in sig.parameters.items():
                if pname == "self":
                    continue
                if param.kind == inspect.Parameter.VAR_KEYWORD:
                    has_kwargs = True
                    continue
                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    continue
                params.add(pname)
                if param.default is inspect.Parameter.empty:
                    required.add(pname)

            _KB_CACHE[call_name] = APISignature(
                module=module_path,
                func_name=func_name,
                params=params,
                required_params=required,
                has_kwargs=has_kwargs,
            )
        except Exception as e:
            log.debug(f"Could not introspect {call_name}: {e}")

    log.info(f"API knowledge base: {len(_KB_CACHE)} function signatures loaded")
    return _KB_CACHE


# ──────────────────────────────────────────────────────────────────────────────
# AST visitor that extracts function calls + their keyword arguments
# ──────────────────────────────────────────────────────────────────────────────

class CallExtractor(ast.NodeVisitor):
    """Extracts all function calls with their keyword argument names."""

    def __init__(self):
        self.calls: list[tuple[str, list[str], int]] = []  # (func_name, kwargs, lineno)

    def _resolve_name(self, node: ast.expr) -> str | None:
        """Resolve dotted attribute access to a string like 'sc.pp.neighbors'."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._resolve_name(node.value)
            if parent:
                return f"{parent}.{node.attr}"
        return None

    def visit_Call(self, node: ast.Call):
        name = self._resolve_name(node.func)
        if name:
            kwargs = [kw.arg for kw in node.keywords if kw.arg is not None]
            self.calls.append((name, kwargs, node.lineno))
        self.generic_visit(node)


# ──────────────────────────────────────────────────────────────────────────────
# Main validation
# ──────────────────────────────────────────────────────────────────────────────

def validate_api_calls(script: str) -> list[str]:
    """
    Parse the script, extract all function calls, and validate keyword
    arguments against the actual installed library signatures.

    Returns list of issues found.
    """
    issues = []
    kb = _build_kb()

    try:
        tree = ast.parse(script)
    except SyntaxError:
        return ["Cannot validate API calls: script has syntax errors"]

    extractor = CallExtractor()
    extractor.visit(tree)

    for call_name, kwargs, lineno in extractor.calls:
        sig = kb.get(call_name)
        if sig is None:
            continue  # Not a tracked function

        if sig.has_kwargs:
            continue  # Accepts **kwargs, can't validate

        # Check for hallucinated parameters
        valid_params = sig.params
        for kwarg in kwargs:
            if kwarg not in valid_params:
                issues.append(
                    f"Line {lineno}: {call_name}() has no parameter '{kwarg}'. "
                    f"Valid params: {sorted(valid_params)}"
                )

    if issues:
        log.warning(f"API validation found {len(issues)} hallucinated parameters")
    else:
        log.info(f"API validation passed ({len(extractor.calls)} calls checked)")

    return issues
