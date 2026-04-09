###############################################################################
# FILE: biogen/generation/error_recovery.py
###############################################################################
"""
Diagnoses runtime errors from sandbox execution and generates targeted fixes.
Not a blind retry — parses the traceback, classifies the failure, and patches
the specific broken section.
"""
import re
import traceback
from biogen.utils.llm_client import call_llm
from biogen.utils.logger import get_logger

log = get_logger("biogen.recovery")


# ──────────────────────────────────────────────────────────────────────────────
# Error classification — deterministic pattern matching before LLM fallback
# ──────────────────────────────────────────────────────────────────────────────

ERROR_PATTERNS = [
    {
        "pattern": r"KeyError: ['\"](.+?)['\"]",
        "category": "missing_column",
        "fix_hint": "Column '{match}' not found. Check the actual column names in the data and metadata.",
    },
    {
        "pattern": r"ImportError: cannot import name ['\"](.+?)['\"] from ['\"]pydeseq2['\"]",
        "category": "pydeseq2_import",
        "fix_hint": "Wrong PyDESeq2 import. Use: from pydeseq2.dds import DeseqDataSet, from pydeseq2.ds import DeseqStats. Note lowercase 'eseq' not 'ESeq'.",
    },
    {
        "pattern": r"has no parameter ['\"]?(colData|countData|design_formula|sizeFactors)['\"]?",
        "category": "pydeseq2_r_api",
        "fix_hint": "R-style DESeq2 parameter used. PyDESeq2 uses: metadata (not colData), counts (not countData), design_factors (not design_formula).",
    },
    {
        "pattern": r"has no parameter ['\"](.+?)['\"]",
        "category": "hallucinated_param",
        "fix_hint": "Parameter '{match}' does not exist on this function. Check the actual API signature.",
    },
    {
        "pattern": r"alpha=[\\d.]+ above max",
        "category": "param_out_of_range",
        "fix_hint": "alpha value too high. For DE analysis, alpha is the significance threshold (typically 0.05), not a proportion.",
    },
    {
        "pattern": r"ValueError: could not convert string to float: ['\"](.+?)['\"]",
        "category": "dtype_mismatch",
        "fix_hint": "Non-numeric value '{match}' in data. Add dtype conversion or filter non-numeric rows.",
    },
    {
        "pattern": r"ModuleNotFoundError: No module named ['\"](.+?)['\"]",
        "category": "missing_module",
        "fix_hint": "Module '{match}' not installed. Replace with an available alternative.",
    },
    {
        "pattern": r"FileNotFoundError: .+?['\"](.+?)['\"]",
        "category": "file_not_found",
        "fix_hint": "File '{match}' not found. Check the data_path argument and file loading logic.",
    },
    {
        "pattern": r"IndexError: (.+)",
        "category": "index_error",
        "fix_hint": "Index out of bounds: {match}. Add bounds checking before array access.",
    },
    {
        "pattern": r"TypeError: (.+?) got an unexpected keyword argument ['\"](.+?)['\"]",
        "category": "wrong_api",
        "fix_hint": "Function {match} doesn't accept parameter '{match2}'. Check library version and API docs.",
    },
    {
        "pattern": r"AttributeError: ['\"](.+?)['\"] object has no attribute ['\"](.+?)['\"]",
        "category": "wrong_attribute",
        "fix_hint": "Object type '{match}' has no attribute '{match2}'. Check the return type of the previous step.",
    },
    {
        "pattern": r"shapes? (.+?) (?:and|not aligned|mismatch)",
        "category": "shape_mismatch",
        "fix_hint": "Array shape mismatch. Verify dimensions match between pipeline steps.",
    },
    {
        "pattern": r"anndata.*?AnnData object.*?expected .+? but got",
        "category": "anndata_format",
        "fix_hint": "AnnData format issue. Check .X dtype and ensure counts are in the right layer.",
    },
    {
        "pattern": r"`obs` must have as many rows as `X` has rows \((\d+)\), but has (\d+) rows",
        "category": "transposed_matrix",
        "fix_hint": "Count matrix is transposed. PyDESeq2/AnnData expect samples as rows, genes as columns. Transpose the DataFrame before creating the AnnData/DeseqDataSet.",
    },
    {
        "pattern": r"Observations annot.*?must have as many rows",
        "category": "transposed_matrix",
        "fix_hint": "Count matrix is transposed. Transpose the DataFrame so samples are rows and genes are columns.",
    },
    {
        "pattern": r"ValueError: Negative values in data",
        "category": "negative_counts",
        "fix_hint": "Negative values found. Data may be log-transformed — skip log1p or use raw counts layer.",
    },
]


def classify_error(error_text: str) -> dict:
    """Classify an error by pattern matching. Returns category + fix hint."""
    for pat in ERROR_PATTERNS:
        match = re.search(pat["pattern"], error_text, re.IGNORECASE)
        if match:
            groups = match.groups()
            hint = pat["fix_hint"]
            if "{match}" in hint and groups:
                hint = hint.replace("{match}", groups[0])
            if "{match2}" in hint and len(groups) > 1:
                hint = hint.replace("{match2}", groups[1])
            return {
                "category": pat["category"],
                "fix_hint": hint,
                "matched_text": match.group(0),
            }

    return {
        "category": "unknown",
        "fix_hint": "Unrecognized error. See full traceback.",
        "matched_text": error_text[:200],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Error-aware code repair
# ──────────────────────────────────────────────────────────────────────────────

REPAIR_SYSTEM = """You are a bioinformatics code debugger. You are given:
1. A Python script that failed during execution
2. The exact error traceback
3. A diagnosis of what went wrong
4. A profile of the actual data being processed

Your job: Fix the script so it works. Rules:
- Only change what's broken. Don't rewrite working parts.
- Use the DATA PROFILE to reference actual column names, data types, and shapes.
- If a column name is wrong, use the actual column names from the profile.
- If the data type is wrong (e.g. log-transformed but code assumes raw counts), adapt the pipeline.
- If a library API is wrong, use the correct function signature.
- Return ONLY the complete fixed Python script. No markdown fences. No explanation."""

REPAIR_USER = """FAILED SCRIPT:
```
{script}
```

ERROR:
{error}

DIAGNOSIS:
Category: {category}
Fix hint: {fix_hint}

DATA PROFILE:
{data_profile}

Return the complete fixed script."""


def repair_script(
    script: str,
    error_text: str,
    data_profile_text: str,
) -> str:
    """Attempt to repair a failed script using error diagnosis + data profile."""
    diagnosis = classify_error(error_text)

    log.info(f"Error category: {diagnosis['category']}")
    log.info(f"Fix hint: {diagnosis['fix_hint']}")

    # For simple deterministic fixes, patch without LLM
    fixed = _try_deterministic_fix(script, diagnosis, error_text)
    if fixed:
        log.info("Applied deterministic fix (no LLM cost)")
        return fixed

    # LLM-based repair
    log.info("Applying LLM-based repair...")
    user = REPAIR_USER.format(
        script=script,
        error=error_text[:1500],
        category=diagnosis["category"],
        fix_hint=diagnosis["fix_hint"],
        data_profile=data_profile_text,
    )

    repaired = call_llm(REPAIR_SYSTEM, user)

    # Strip fences if present
    repaired = repaired.strip()
    if repaired.startswith("```"):
        repaired = re.sub(r"^```\w*\n?", "", repaired)
    if repaired.endswith("```"):
        repaired = repaired[:-3]

    return repaired.strip()


def _try_deterministic_fix(
    script: str,
    diagnosis: dict,
    error_text: str,
) -> str | None:
    """Try to fix common errors without an LLM call."""
    cat = diagnosis["category"]
    fixed = script

    if cat == "missing_module":
        replacements = {
            "adjusttext": "adjustText",
            "sklearn": "scikit-learn",
        }
        match = re.search(r"No module named ['\"](.+?)['\"]", error_text)
        if match:
            module = match.group(1)
            if module in replacements:
                return fixed.replace(
                    f"import {module}",
                    f"import {replacements[module]}"
                )
        return None

    if cat == "negative_counts":
        if "sc.pp.log1p" in fixed:
            fixed = fixed.replace(
                "sc.pp.log1p(adata)",
                "# sc.pp.log1p(adata)  # Skipped: data already log-transformed"
            )
            return fixed
        return None

    # ── Transposed matrix fix ──
    # CSV has genes as rows, samples as columns. PyDESeq2 needs the opposite.
    if cat == "transposed_matrix" or "obs` must have as many rows" in error_text:
        # Add .T after read_csv
        if "pd.read_csv" in fixed and ".T" not in fixed.split("pd.read_csv")[1].split("\n")[0]:
            # Find the line with read_csv and add .T
            lines = fixed.splitlines()
            new_lines = []
            for line in lines:
                if "pd.read_csv" in line and ".T" not in line:
                    line = line.rstrip()
                    if line.endswith(")"):
                        line = line + ".T"
                    new_lines.append(line)
                else:
                    new_lines.append(line)
            fixed = "\n".join(new_lines)
            log.info("Applied deterministic fix: transposed count matrix")
            return fixed

    # ── PyDESeq2 import fixes ──
    # LLMs get the import path and casing wrong constantly
    applied = False

    import_fixes = {
        "from pydeseq2 import DESeqDataSet": "from pydeseq2.dds import DeseqDataSet",
        "from pydeseq2 import DeseqDataSet": "from pydeseq2.dds import DeseqDataSet",
        "from pydeseq2 import DESeqStats": "from pydeseq2.ds import DeseqStats",
        "from pydeseq2 import DeseqStats": "from pydeseq2.ds import DeseqStats",
        "from pydeseq2 import DESeqDataSet, DESeqStats": "from pydeseq2.dds import DeseqDataSet\nfrom pydeseq2.ds import DeseqStats",
        "from pydeseq2 import DeseqDataSet, DeseqStats": "from pydeseq2.dds import DeseqDataSet\nfrom pydeseq2.ds import DeseqStats",
        "import pydeseq2": "from pydeseq2.dds import DeseqDataSet\nfrom pydeseq2.ds import DeseqStats",
    }
    for wrong, right in import_fixes.items():
        if wrong in fixed:
            fixed = fixed.replace(wrong, right)
            applied = True

    # Also fix class name casing in the body
    if "DESeqDataSet" in fixed:
        fixed = fixed.replace("DESeqDataSet", "DeseqDataSet")
        applied = True
    if "DESeqStats" in fixed:
        fixed = fixed.replace("DESeqStats", "DeseqStats")
        applied = True

    # ── PyDESeq2 R-style parameter hallucinations ──
    # LLMs constantly confuse R DESeq2 API with Python PyDESeq2 API
    if "colData=" in fixed or "colData =" in fixed:
        fixed = re.sub(r'colData\s*=', 'metadata=', fixed)
        applied = True

    if "countData=" in fixed or "countData =" in fixed:
        fixed = re.sub(r'countData\s*=', 'counts=', fixed)
        applied = True

    if re.search(r"design\s*=\s*['\"]~", fixed):
        fixed = re.sub(
            r"design\s*=\s*['\"]~(\w+)['\"]",
            r'design_factors="\1"',
            fixed
        )
        applied = True

    if "design_formula=" in fixed or "design_formula =" in fixed:
        fixed = re.sub(r'design_formula\s*=', 'design_factors=', fixed)
        applied = True

    if "sizeFactors=" in fixed or "sizeFactors =" in fixed:
        fixed = re.sub(r',?\s*sizeFactors\s*=\s*[^,\)]+', '', fixed)
        applied = True

    redundant_calls = [
        r'\.fit_size_factors\(\)',
        r'\.fit_dispersion_trend\(\)',
        r'\.fit_genewise_dispersions\(\)',
        r'\.fit_MAP_dispersions\(\)',
        r'\.fit_LFC\(\)',
    ]
    for pat in redundant_calls:
        if re.search(pat, fixed):
            fixed = re.sub(
                r'\n\s*.*?' + pat + r'.*?\n',
                '\n',
                fixed
            )
            applied = True

    if "counts_df.columns" in fixed and ".T" in fixed:
        if any(kw in fixed for kw in ["'treated'" , "'control'", "split('_')", "rsplit('_'"]):
            fixed = fixed.replace("counts_df.columns", "counts_df.index")
            applied = True

    alpha_matches = re.findall(r'alpha\s*=\s*([\d.]+)', fixed)
    for val in alpha_matches:
        try:
            if float(val) > 0.1:
                fixed = fixed.replace(f'alpha={val}', 'alpha=0.05')
                applied = True
        except ValueError:
            pass

    if applied:
        log.info("Applied deterministic fix for PyDESeq2 imports / API mapping")
        return fixed

    return None
