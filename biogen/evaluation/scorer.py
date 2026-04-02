def pass_rate(results: list[bool]) -> float:
    if not results:
        return 0.0
    return sum(results) / len(results)
