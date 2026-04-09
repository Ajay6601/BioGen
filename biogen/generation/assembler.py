###############################################################################
# FILE: biogen/generation/assembler.py
###############################################################################
"""
The LLM selects templates and fills parameters. It never writes code.
The assembler stitches selected templates into a runnable script.
"""
import json
import re
from biogen.templates.registry import ALL_TEMPLATES, get_template_catalog, Template
from biogen.generation.data_inspector import DataProfile
from biogen.utils.llm_client import call_llm_json
from biogen.utils.logger import get_logger

log = get_logger("biogen.assembler")

# When no LLM adapter is supplied (e.g. orchestrator uses only templates + in-process executor),
# embed a minimal loader so assembled scripts remain runnable standalone.
DEFAULT_ADAPTER_BULK = """
import os
import pandas as pd
import numpy as np

def load_and_prepare(data_path: str, metadata_path: str = ""):
    path = str(data_path)
    sep = "\t" if path.endswith((".tsv", ".tab")) else ","
    counts_df = pd.read_csv(path, sep=sep, index_col=0)
    if counts_df.shape[0] > counts_df.shape[1] * 5:
        counts_df = counts_df.T
    counts_df = counts_df.apply(pd.to_numeric, errors="coerce").fillna(0)
    counts_df = counts_df.round().astype(int)
    sample_names = counts_df.index.tolist()
    if metadata_path and os.path.exists(metadata_path):
        msep = "\t" if str(metadata_path).endswith((".tsv", ".tab")) else ","
        metadata = pd.read_csv(metadata_path, sep=msep, index_col=0)
    else:
        metadata = pd.DataFrame(
            {"condition": ["A" if i % 2 == 0 else "B" for i in range(len(sample_names))]},
            index=sample_names,
        )
    return counts_df, metadata
""".strip()

DEFAULT_ADAPTER_SCRNA = """
import anndata as ad

def load_and_prepare(data_path: str, metadata_path: str = ""):
    adata = ad.read_h5ad(data_path)
    return adata, None
""".strip()

# ──────────────────────────────────────────────────────────────────────────────
# Step 1: LLM selects templates + fills params
# ──────────────────────────────────────────────────────────────────────────────

SELECTOR_SYSTEM = """You are a bioinformatics workflow planner. You have a catalog of
pre-validated code templates. Your job is to:

1. SELECT which templates to use (by template ID)
2. FILL IN their parameters based on the user's query and data profile
3. ORDER them correctly

You MUST only use templates from the catalog. Do not invent new templates.

CATALOG:
{catalog}

Respond ONLY with valid JSON (no markdown):
{{
  "steps": [
    {{
      "template_id": "load_bulk_csv",
      "params": {{"data_path": "DATA_PATH"}}
    }},
    {{
      "template_id": "run_deseq2",
      "params": {{"design_factor": "condition", "contrast_ref": "control", "contrast_test": "treated"}}
    }}
  ]
}}

Rules:
- Use DATA_PATH as placeholder for the data file path (will be replaced at runtime)
- Use OUTPUT_DIR as placeholder for the output directory
- For output_path params, use OUTPUT_DIR/filename.png format
- Fill params from the data profile: use actual condition names, column names, etc.
- If the user asks for a volcano plot, include the volcano_plot template
- If the user asks for a heatmap, include the heatmap template
- If the user asks for PCA, include the pca_plot template
- Respect template dependencies (check 'requires' field)
"""

SELECTOR_USER = """User query: {query}

Data profile:
{data_profile}

Select templates and fill their parameters."""


def select_templates(query: str, data_profile: DataProfile) -> list[dict]:
    """Ask LLM to select templates and fill parameters."""
    catalog = get_template_catalog()
    system = SELECTOR_SYSTEM.replace("{catalog}", catalog)
    user = SELECTOR_USER.replace("{query}", query).replace("{data_profile}", data_profile.to_prompt_context())

    raw = call_llm_json(system, user)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"Template selector returned invalid JSON: {raw[:300]}")

    steps = parsed.get("steps", [])
    if not steps:
        raise ValueError("Template selector returned no steps")

    # Validate all template IDs exist
    for step in steps:
        tid = step.get("template_id")
        if tid not in ALL_TEMPLATES:
            log.warning(f"Unknown template '{tid}', skipping")

    valid_steps = [s for s in steps if s.get("template_id") in ALL_TEMPLATES]
    log.info(f"Selected {len(valid_steps)} templates: {[s['template_id'] for s in valid_steps]}")
    return valid_steps


# ──────────────────────────────────────────────────────────────────────────────
# Step 2: Assemble templates into a runnable script
# ──────────────────────────────────────────────────────────────────────────────

def _fill_template(template: Template, params: dict) -> str:
    """Return template code as-is. Templates have hardcoded defaults.
    Params are only used in main() call arguments, not baked into functions."""
    return template.code.strip()


def assemble_script(
    steps: list[dict],
    data_path: str,
    output_dir: str,
    metadata_path: str = "",
    *,
    adapter_code: str = "",
    analysis_type: str = "bulk_rnaseq",
) -> str:
    """Assemble: optional LLM adapter + pre-validated templates = full script."""

    ac = (adapter_code or "").strip()
    if not ac:
        ac = (
            DEFAULT_ADAPTER_SCRNA
            if analysis_type in ("scrna", "scrna_seq")
            else DEFAULT_ADAPTER_BULK
        )

    imports = set()
    functions = []

    # 1. Add the adapter first (LLM or built-in default)
    adapter_lines = ac.splitlines()
    adapter_imports = []
    adapter_body = []
    for line in adapter_lines:
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            adapter_imports.append(stripped)
        else:
            adapter_body.append(line)
    imports.update(adapter_imports)
    functions.append("\n".join(adapter_body))

    # 2. Add template functions
    for step in steps:
        tid = step["template_id"]
        if tid in ("load_bulk_csv", "load_metadata", "load_h5ad"):
            continue  # Adapter replaces these
        template = ALL_TEMPLATES[tid]
        code = template.code.strip()
        code_lines = code.splitlines()
        func_lines = []
        for line in code_lines:
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                imports.add(stripped)
            else:
                func_lines.append(line)
        # Join as a single block, skip leading empty lines
        block = "\n".join(func_lines).strip()
        if block:
            functions.append(block)

    # 3. Build main()
    main_lines = []
    main_lines.append("def main(data_path, output_dir, metadata_path=''):")
    main_lines.append("    import os")
    main_lines.append("    os.makedirs(output_dir, exist_ok=True)")
    main_lines.append("")
    main_lines.append("    # Step 1: LLM-generated adapter — handles any data format")
    if analysis_type in ("scrna", "scrna_seq"):
        main_lines.append("    adata, _ = load_and_prepare(data_path, metadata_path)")
    else:
        main_lines.append("    counts_df, metadata = load_and_prepare(data_path, metadata_path)")
    main_lines.append("")

    # Chain template calls
    has_results = False
    for step in steps:
        tid = step["template_id"]
        if tid in ("load_bulk_csv", "load_metadata", "load_h5ad"):
            continue
        template = ALL_TEMPLATES[tid]
        params = step.get("params", {})

        # Find the function name
        func_match = re.search(r"def (\w+)\(", template.code)
        if not func_match:
            continue
        func_name = func_match.group(1)

        # Build call based on template type
        if tid == "run_deseq2":
            call_args = ["counts_df", "metadata"]
            for k, v in params.items():
                if k in ("data_path", "output_path"):
                    continue
                call_args.append(f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}")
            main_lines.append(f"    # Step: {template.name}")
            main_lines.append(f"    results_df = {func_name}({', '.join(call_args)})")
            main_lines.append(f'    results_df.to_csv(os.path.join(output_dir, "de_results.csv"))')
            main_lines.append("")
            has_results = True

        elif tid == "filter_de_results":
            call_args = ["results_df"]
            for k, v in params.items():
                call_args.append(f"{k}={v}")
            main_lines.append(f"    filtered_df = {func_name}({', '.join(call_args)})")
            main_lines.append(f'    filtered_df.to_csv(os.path.join(output_dir, "filtered_results.csv"))')
            main_lines.append("")

        elif tid == "volcano_plot":
            call_args = ["results_df", f'os.path.join(output_dir, "volcano_plot.png")']
            for k, v in params.items():
                if k == "output_path":
                    continue
                call_args.append(f"{k}={v}")
            main_lines.append(f"    {func_name}({', '.join(call_args)})")
            main_lines.append("")

        elif tid == "heatmap":
            main_lines.append(f'    {func_name}(results_df, counts_df, os.path.join(output_dir, "heatmap.png"))')
            main_lines.append("")

        elif tid == "pca_plot":
            main_lines.append(f'    {func_name}(counts_df, metadata, os.path.join(output_dir, "pca_plot.png"))')
            main_lines.append("")

        # scRNA templates
        elif tid == "scrna_qc_filter":
            main_lines.append(f"    adata = {func_name}(adata)")
            main_lines.append("")
        elif tid == "scrna_normalize":
            main_lines.append(f"    adata = {func_name}(adata)")
            main_lines.append("")
        elif tid == "scrna_dimred_cluster":
            call_args = ["adata"]
            for k, v in params.items():
                call_args.append(f"{k}={v}")
            main_lines.append(f"    adata = {func_name}({', '.join(call_args)})")
            main_lines.append("")
        elif tid == "scrna_umap_plot":
            main_lines.append(f'    {func_name}(adata, os.path.join(output_dir, "umap.png"))')
            main_lines.append("")
        elif tid == "scrna_marker_genes":
            main_lines.append(f"    markers = {func_name}(adata)")
            main_lines.append(f'    markers.to_csv(os.path.join(output_dir, "marker_genes.csv"), index=False)')
            main_lines.append("")

    main_lines.append('    print("Pipeline complete.")')

    # Assemble
    script_parts = [
        "# BioGen - LLM adapter + pre-validated templates",
        "",
        "\n".join(sorted(imports)),
        "",
    ]
    for func in functions:
        script_parts.append(func)
        script_parts.append("")  # blank line between functions
    script_parts.append("\n".join(main_lines))
    script_parts.append("")
    script_parts.append('if __name__ == "__main__":')
    script_parts.append("    import sys")
    script_parts.append('    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")')

    script = "\n".join(script_parts)
    log.info(f"Assembled script: {len(script.splitlines())} lines")
    return script
