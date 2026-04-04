"""
Diagnoses runtime errors from sandbox execution and generates targeted fixes.
Not a blind retry — parses the traceback, classifies the failure, and patches
the specific broken section.
"""
import re

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
        "fix_hint": "Call {match} doesn't accept parameter '{match2}'. Check library version and API docs.",
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

REPAIR_USER_PREFIX = """FAILED SCRIPT:
```
"""

REPAIR_USER_SUFFIX = """```

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

    fixed = _try_deterministic_fix(script, diagnosis, error_text)
    if fixed:
        log.info("Applied deterministic fix (no LLM cost)")
        return fixed

    log.info("Applying LLM-based repair...")
    tail = REPAIR_USER_SUFFIX.format(
        error=error_text[:1500],
        category=diagnosis["category"],
        fix_hint=diagnosis["fix_hint"],
        data_profile=data_profile_text,
    )
    user = REPAIR_USER_PREFIX + script + "\n" + tail

    repaired = call_llm(REPAIR_SYSTEM, user)

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

    if cat == "missing_module":
        replacements = {
            "adjusttext": "adjustText",
        }
        match = re.search(r"No module named ['\"](.+?)['\"]", error_text)
        if match:
            module = match.group(1).lower()
            if module in replacements:
                wrong = match.group(1)
                right = replacements[module]
                return script.replace(f"import {wrong}", f"import {right}")
        return None

    if cat == "negative_counts":
        if "sc.pp.log1p" in script:
            return script.replace(
                "sc.pp.log1p(adata)",
                "# sc.pp.log1p(adata)  # Skipped: data already log-transformed",
            )
        return None

    return None
