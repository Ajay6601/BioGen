import ast
import sys

# Known valid top-level imports in biogen-generated scripts
KNOWN_MODULES = {
    "scanpy",
    "sc",
    "anndata",
    "ad",
    "pandas",
    "pd",
    "numpy",
    "np",
    "matplotlib",
    "matplotlib.pyplot",
    "plt",
    "seaborn",
    "sns",
    "pydeseq2",
    "pydeseq2.dds",
    "pydeseq2.ds",
    "os",
    "sys",
    "pathlib",
    "argparse",
    "logging",
    "warnings",
    "adjustText",
    "scipy",
    "sklearn",
}


def check_ast(script: str, *, require_main: bool = True) -> list[str]:
    """Parse script and check for syntax errors + import issues."""
    issues: list[str] = []

    try:
        tree = ast.parse(script)
    except SyntaxError as e:
        issues.append(f"SyntaxError at line {e.lineno}: {e.msg}")
        return issues

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in KNOWN_MODULES and root not in sys.stdlib_module_names:
                    issues.append(
                        f"Line {node.lineno}: unknown import '{alias.name}'"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root not in KNOWN_MODULES and root not in sys.stdlib_module_names:
                    issues.append(
                        f"Line {node.lineno}: unknown import from '{node.module}'"
                    )

    source_lines = script.splitlines()
    for i, line in enumerate(source_lines, 1):
        stripped = line.strip()
        if "TODO" in stripped or "..." == stripped:
            if not (stripped.startswith("#") or stripped.startswith('"""')):
                issues.append(f"Line {i}: placeholder/ellipsis found: '{stripped}'")

    if require_main:
        func_names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if "main" not in func_names:
            issues.append("Missing main() function")

    return issues


def is_valid_python(code: str) -> bool:
    """True if there are no AST issues (snippets allowed without main())."""
    return len(check_ast(code, require_main=False)) == 0
