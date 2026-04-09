###############################################################################
# FILE: biogen/generation/adapter_gen.py
###############################################################################
"""
The ONE place where the LLM writes code.

The LLM generates a single function: load_and_prepare()
This function handles all the messy, unpredictable data wrangling:
  - Reading whatever file format the user provides
  - Finding/inferring conditions from metadata or sample names
  - Cleaning column names, handling duplicates, fixing types
  - Returning a STANDARD output: (counts_df, metadata)

Everything after this function is pre-validated templates.
If this function fails, the error is always about data loading — 
never about bioinformatics logic.
"""
import re
from biogen.generation.data_inspector import DataProfile
from biogen.utils.llm_client import call_llm
from biogen.utils.logger import get_logger

log = get_logger("biogen.adapter")


ADAPTER_SYSTEM = """You are a data loading specialist. You write ONE Python function
that loads messy bioinformatics data files and returns clean, standardized output.

Your function signature MUST be exactly:
    def load_and_prepare(data_path: str, metadata_path: str = "") -> tuple:
        # Returns (counts_df, metadata)
        # counts_df: pd.DataFrame — samples as ROWS, genes as COLUMNS, integer counts
        # metadata: pd.DataFrame — index matches counts_df.index, has "condition" column

You are given the ACTUAL data profile — real column names, real sample names,
real data types. Use this information to write code that works for THIS specific dataset.

RULES:
- Import only: pandas, numpy, os, anndata (if h5ad)
- counts_df must have samples as rows, genes as columns (transpose if needed)
- counts_df values must be integers (use .round().astype(int) if float)
- metadata must have a column named exactly "condition"
- metadata index must match counts_df index exactly
- If metadata file exists, load it and find the condition column
- If no metadata file, infer conditions from sample names or create groups
- Filter out genes with zero total counts
- Handle common issues: BOM characters, extra whitespace in headers, mixed types
- Return (counts_df, metadata) as a tuple

Respond ONLY with the Python function. No markdown fences. No explanation."""


ADAPTER_USER = """DATA PROFILE:
{profile}

User's query: {query}

File path: {data_path}
Metadata path: {metadata_path}

The user mentioned these conditions/groups: {conditions_hint}

Write the load_and_prepare() function for this specific dataset."""


ADAPTER_FOR_H5AD = """DATA PROFILE:
{profile}

User's query: {query}

File path: {data_path}

Write a load_and_prepare() function with this EXACT signature:
    def load_and_prepare(data_path: str, metadata_path: str = "") -> tuple:

For h5ad files:
1. Load with scanpy: adata = sc.read_h5ad(data_path)
2. Call adata.var_names_make_unique()
3. Return (adata, None) — second element is None for scRNA-seq

Respond ONLY with the Python function. No markdown fences."""


def _extract_conditions_hint(query: str, profile: DataProfile) -> str:
    """Extract any condition/group names mentioned in the query or data."""
    hints = []

    # From data profile
    if profile.conditions:
        hints.append(f"Detected in metadata: {profile.conditions}")
    if profile.condition_column:
        hints.append(f"Condition column: '{profile.condition_column}'")

    # From query — look for "X vs Y" or "comparing X and Y" patterns
    vs_match = re.search(r'(\w+)\s+vs\.?\s+(\w+)', query, re.IGNORECASE)
    if vs_match:
        hints.append(f"User wants to compare: '{vs_match.group(1)}' vs '{vs_match.group(2)}'")

    comparing_match = re.search(r'comparing\s+(.+?)\s+(?:and|vs|to)\s+(.+?)[\s,.]', query, re.IGNORECASE)
    if comparing_match:
        hints.append(f"User wants to compare: '{comparing_match.group(1).strip()}' vs '{comparing_match.group(2).strip()}'")

    # Check for treated/control/drug/dmso/wt/ko mentions
    for term in ['treated', 'untreated', 'control', 'drug', 'dmso', 'wt', 'ko',
                 'wildtype', 'knockout', 'dexamethasone', 'vehicle', 'sham']:
        if term.lower() in query.lower():
            hints.append(f"Mentioned in query: '{term}'")

    return "; ".join(hints) if hints else "None detected — infer from sample names or metadata"


def generate_adapter(
    query: str,
    profile: DataProfile,
    data_path: str,
    metadata_path: str = "",
) -> str:
    """Generate the data adapter function using LLM + actual data profile."""

    if profile.file_type == "h5ad":
        # No LLM needed — h5ad loading is always the same
        log.info("  Using hardcoded h5ad adapter (no LLM call needed)")
        return """
import scanpy as sc

def load_and_prepare(data_path: str, metadata_path: str = ""):
    adata = sc.read_h5ad(data_path)
    adata.var_names_make_unique()
    return (adata, None)
""".strip()

    # CSV/TSV — LLM writes the adapter
    conditions_hint = _extract_conditions_hint(query, profile)
    user = ADAPTER_USER.replace("{profile}", profile.to_prompt_context()) \
                       .replace("{query}", query) \
                       .replace("{data_path}", data_path) \
                       .replace("{metadata_path}", metadata_path or "none") \
                       .replace("{conditions_hint}", conditions_hint)
    raw = call_llm(ADAPTER_SYSTEM, user)

    # Strip fences
    code = raw.strip()
    if code.startswith("```"):
        code = re.sub(r"^```\w*\n?", "", code)
    if code.endswith("```"):
        code = code[:-3]
    code = code.strip()

    # Validate it defines load_and_prepare
    if "def load_and_prepare" not in code:
        raise ValueError("Adapter generator did not produce a load_and_prepare function")

    log.info(f"Generated adapter: {len(code.splitlines())} lines")
    return code


def generate_adapter_with_retry(
    query: str,
    profile: DataProfile,
    data_path: str,
    metadata_path: str = "",
    error: str = "",
) -> str:
    """Regenerate adapter with error context from a previous failed attempt."""

    if profile.file_type == "h5ad":
        # h5ad adapter is deterministic — if it failed, the issue is downstream
        log.info("  h5ad adapter is hardcoded — error is in template assembly, not adapter")
        return """
import scanpy as sc

def load_and_prepare(data_path: str, metadata_path: str = ""):
    adata = sc.read_h5ad(data_path)
    adata.var_names_make_unique()
    return (adata, None)
""".strip()

    conditions_hint = _extract_conditions_hint(query, profile)

    error_context = ""
    if error:
        error_context = f"""

PREVIOUS ATTEMPT FAILED WITH THIS ERROR:
{error}

Fix the function to handle this error. Common fixes:
- If column not found: check actual column names in the profile above
- If shape mismatch: make sure to transpose so samples are rows
- If type error: ensure counts are integers with .round().astype(int)
- If condition not found: look at ALL metadata columns, not just 'condition'
"""

    user = ADAPTER_USER.replace("{profile}", profile.to_prompt_context()) \
                       .replace("{query}", query) \
                       .replace("{data_path}", data_path) \
                       .replace("{metadata_path}", metadata_path or "none") \
                       .replace("{conditions_hint}", conditions_hint)
    user += error_context

    raw = call_llm(ADAPTER_SYSTEM, user)

    code = raw.strip()
    if code.startswith("```"):
        code = re.sub(r"^```\w*\n?", "", code)
    if code.endswith("```"):
        code = code[:-3]
    code = code.strip()

    if "def load_and_prepare" not in code:
        raise ValueError("Adapter retry did not produce a load_and_prepare function")

    return code
