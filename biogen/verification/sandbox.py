def execute_safely(code: str) -> dict[str, str]:
    # Placeholder sandbox implementation.
    local_vars: dict[str, str] = {}
    exec(code, {}, local_vars)
    return {"status": "ok", "locals": str(sorted(local_vars.keys()))}
