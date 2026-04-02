import ast
import re
from pathlib import Path

import yaml

from biogen.config import CONSTRAINTS_DIR
from biogen.utils.logger import get_logger

log = get_logger("biogen.params")

_constraints_cache: dict[str, list[tuple[str, dict]]] = {}


def _load_tool_param_rules(analysis_type: str) -> list[tuple[str, dict]]:
    """Load (tool_name, parameters_dict) from each YAML under CONSTRAINTS_DIR."""
    if analysis_type in _constraints_cache:
        return _constraints_cache[analysis_type]

    rules: list[tuple[str, dict]] = []
    if CONSTRAINTS_DIR.is_dir():
        for yaml_file in sorted(CONSTRAINTS_DIR.glob("*.yaml")):
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            tool = data.get("tool")
            params = data.get("parameters")
            if isinstance(tool, str) and isinstance(params, dict):
                rules.append((tool, params))

    _constraints_cache[analysis_type] = rules
    return rules


def _normalize_expected_type(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    m = raw.lower()
    if m in ("int", "integer"):
        return "int"
    if m in ("float", "double"):
        return "float"
    if m in ("str", "string"):
        return "str"
    return m


def check_params(script: str, analysis_type: str) -> list[str]:
    """Validate parameters in generated code against YAML constraints."""
    issues: list[str] = []
    bundles = _load_tool_param_rules(analysis_type)

    if not bundles:
        log.debug("No constraints loaded, skipping param check")
        return issues

    for tool, param_rules in bundles:
        if tool not in script:
            continue

        for param_name, raw_rule in param_rules.items():
            if not isinstance(raw_rule, dict):
                continue
            rule = {k: v for k, v in raw_rule.items()}
            et = _normalize_expected_type(rule.get("type"))
            if et:
                rule["type"] = et

            pattern = rf"{param_name}\s*=\s*([^\s,\)]+)"
            matches = re.findall(pattern, script)

            for match in matches:
                try:
                    val = ast.literal_eval(match)
                except (ValueError, SyntaxError):
                    continue

                expected_type = rule.get("type")
                if expected_type == "int" and not isinstance(val, int):
                    issues.append(
                        f"{tool}: {param_name} should be int, got {type(val).__name__}"
                    )
                elif expected_type == "float" and not isinstance(val, (int, float)):
                    issues.append(
                        f"{tool}: {param_name} should be float, got {type(val).__name__}"
                    )

                min_val = rule.get("min")
                max_val = rule.get("max")
                if isinstance(val, (int, float)):
                    if min_val is not None and val < min_val:
                        issues.append(
                            f"{tool}: {param_name}={val} below min={min_val}"
                        )
                    if max_val is not None and val > max_val:
                        issues.append(
                            f"{tool}: {param_name}={val} above max={max_val}"
                        )

                allowed = rule.get("allowed")
                if allowed and val not in allowed:
                    if isinstance(val, str):
                        issues.append(
                            f"{tool}: {param_name}='{val}' not in {allowed}"
                        )

    return issues


def load_constraint_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
