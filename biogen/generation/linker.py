def link_steps(step_codes: dict[int, str]) -> None:
    if not step_codes:
        raise ValueError("No steps to link")
