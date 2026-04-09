###############################################################################
# FILE: biogen/verification/api_introspect.py
###############################################################################
"""
Runtime API introspection — builds a reference doc string from actual installed
libraries that gets injected into the coder/repair prompts.

Instead of hardcoding every fix, we give the LLM the real API as context
so it generates correct code the first time, and if it doesn't, the repair
prompt includes the actual signature.
"""
import importlib
import inspect

from biogen.utils.logger import get_logger

log = get_logger("biogen.introspect")

# Libraries and their key classes/functions to introspect
INTROSPECT_MAP = {
    "pydeseq2.dds": ["DeseqDataSet"],
    "pydeseq2.ds": ["DeseqStats"],
    "scanpy.pp": [
        "filter_cells", "filter_genes", "normalize_total", "log1p",
        "highly_variable_genes", "pca", "neighbors", "scale", "regress_out",
    ],
    "scanpy.tl": [
        "umap", "tsne", "leiden", "louvain", "rank_genes_groups",
        "dendrogram", "diffmap", "paga",
    ],
    "scanpy.pl": [
        "umap", "pca", "violin", "dotplot", "stacked_violin",
        "heatmap", "rank_genes_groups", "scatter",
    ],
}

_CACHE: dict[str, str] = {}


def _callable_signature(obj):
    if inspect.isclass(obj):
        return inspect.signature(obj.__init__)
    return inspect.signature(obj)


def _get_signature_doc(module_path: str, name: str) -> str | None:
    """Get a clean signature + first line of docstring for one function/class."""
    try:
        mod = importlib.import_module(module_path)
        obj = getattr(mod, name, None)
        if obj is None:
            return None

        sig = _callable_signature(obj)
        params = []
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if param.default is inspect.Parameter.empty:
                params.append(pname)
            else:
                default = repr(param.default)
                if len(default) > 40:
                    default = "..."
                params.append(f"{pname}={default}")

        sig_str = f"{name}({', '.join(params)})"

        doc = inspect.getdoc(obj) or ""
        first_line = ""
        for line in doc.split("\n"):
            line = line.strip()
            if line and not line.startswith("..") and not line.startswith("---"):
                first_line = line
                break

        extras: list[str] = []
        if inspect.isclass(obj):
            for attr_name in dir(obj):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(obj, attr_name, None)
                if callable(attr) and attr_name in (
                    "deseq2", "summary", "fit_size_factors",
                    "fit_genewise_dispersions", "fit_dispersion_trend",
                ):
                    try:
                        msig = inspect.signature(attr)
                        extras.append(f"  .{attr_name}{msig}")
                    except (ValueError, TypeError):
                        extras.append(f"  .{attr_name}()")

            if name == "DeseqStats" and "results_df" in dir(obj):
                extras.append(
                    "  .results_df  (property — access as attribute, not method)"
                )

        result = sig_str
        if first_line:
            result += f"\n    # {first_line}"
        if extras:
            result += "\n  Methods/properties:\n" + "\n".join(extras)
        return result

    except Exception as e:
        log.debug(f"Could not introspect {module_path}.{name}: {e}")
        return None


def get_api_reference(analysis_type: str = "all") -> str:
    """
    Build a reference string of actual API signatures for the given analysis type.
    This gets injected into LLM prompts so the model sees real signatures.
    """
    cache_key = analysis_type
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    sections: list[str] = []

    if analysis_type in ("bulk_rnaseq_de", "bulk_rnaseq"):
        modules = {
            "pydeseq2.dds": INTROSPECT_MAP["pydeseq2.dds"],
            "pydeseq2.ds": INTROSPECT_MAP["pydeseq2.ds"],
        }
    elif analysis_type in ("scrna_clustering", "scrna_seq"):
        modules = {k: v for k, v in INTROSPECT_MAP.items() if k.startswith("scanpy")}
    else:
        modules = INTROSPECT_MAP

    for module_path, names in modules.items():
        sigs = []
        for name in names:
            doc = _get_signature_doc(module_path, name)
            if doc:
                sigs.append(f"  {module_path}.{doc}")
        if sigs:
            sections.append(f"# {module_path}\n" + "\n".join(sigs))

    sections.append("""
# CRITICAL NOTES — common LLM mistakes:
# - PyDESeq2 uses 'metadata' NOT 'colData' (colData is R syntax)
# - PyDESeq2 uses 'counts' NOT 'countData'
# - PyDESeq2 uses 'design_factors' NOT 'design_formula' or 'design'
# - DeseqStats.results_df is a PROPERTY (access as .results_df, not .results_df())
# - DeseqStats expects stat_res.summary() to print, .results_df to get DataFrame
# - alpha in DeseqStats is significance threshold (0.05), NOT a fraction (0.5)
# - scanpy: normalize_total() THEN log1p() THEN highly_variable_genes() — never reverse
# - scanpy: neighbors() requires PCA first, UMAP/leiden require neighbors first
""")

    result = "\n\n".join(sections)
    _CACHE[cache_key] = result
    log.info(f"Built API reference: {len(result)} chars for {analysis_type}")
    return result
