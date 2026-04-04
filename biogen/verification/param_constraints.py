import ast
import re
from pathlib import Path

import yaml

from biogen.config import CONSTRAINTS_DIR
from biogen.utils.logger import get_logger

log = get_logger("biogen.params")

_constraints_cache: dict[str, dict] = {}


def _load_constraints(analysis_type: str) -> dict:
    if analysis_type in _constraints_cache:
        return _constraints_cache[analysis_type]

    constraints = {}
    for yaml_file in CONSTRAINTS_DIR.glob("*.yaml"):
        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            constraints.update(data)

    _constraints_cache[analysis_type] = constraints
    return constraints


def check_params(script: str, analysis_type: str) -> list[str]:
    issues = []
    constraints = _load_constraints(analysis_type)

    if not constraints:
        log.debug("No constraints loaded, skipping param check")
        return issues

    for func_pattern, rules in constraints.items():
        if func_pattern not in script:
            continue

        if not isinstance(rules, dict):
            continue
        param_rules = rules.get("params", {})
        for param_name, rule in param_rules.items():
            if not isinstance(rule, dict):
                continue
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
                        f"{func_pattern}: {param_name} should be int, got {type(val).__name__}"
                    )
                elif expected_type == "float" and not isinstance(val, (int, float)):
                    issues.append(
                        f"{func_pattern}: {param_name} should be float, got {type(val).__name__}"
                    )

                min_val = rule.get("min")
                max_val = rule.get("max")
                if isinstance(val, (int, float)):
                    if min_val is not None and val < min_val:
                        issues.append(
                            f"{func_pattern}: {param_name}={val} below min={min_val}"
                        )
                    if max_val is not None and val > max_val:
                        issues.append(
                            f"{func_pattern}: {param_name}={val} above max={max_val}"
                        )

                allowed = rule.get("allowed")
                if allowed and val not in allowed:
                    if isinstance(val, str):
                        issues.append(
                            f"{func_pattern}: {param_name}='{val}' not in {allowed}"
                        )

    return issues


def load_constraint_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
