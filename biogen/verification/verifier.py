from .ast_checker import is_valid_python
from .dep_graph import dependencies_are_consistent


def verify_pipeline(code_blocks: list[str]) -> bool:
    return all(is_valid_python(code) for code in code_blocks) and dependencies_are_consistent(
        code_blocks
    )
