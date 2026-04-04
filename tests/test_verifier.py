"""Unit tests for AST checker and param constraints."""
from biogen.verification.ast_checker import check_ast
from biogen.verification.param_constraints import check_params

VALID_SCRIPT = """
import pandas as pd
import numpy as np

def step_1(data_path: str) -> pd.DataFrame:
    return pd.read_csv(data_path)

def main(data_path: str, output_dir: str):
    result = step_1(data_path)
    result.to_csv(f"{output_dir}/output.csv")

if __name__ == "__main__":
    import sys
    main(sys.argv[1], sys.argv[2])
"""

INVALID_SCRIPT = """
import pandas as pd
import some_fake_library

def step_1(data_path)
    return pd.read_csv(data_path)
"""

SCRIPT_NO_MAIN = """
import pandas as pd

def process(data_path: str):
    return pd.read_csv(data_path)
"""


def test_ast_valid_script():
    issues = check_ast(VALID_SCRIPT)
    assert len(issues) == 0


def test_ast_syntax_error():
    issues = check_ast(INVALID_SCRIPT)
    assert any("SyntaxError" in i for i in issues)


def test_ast_missing_main():
    issues = check_ast(SCRIPT_NO_MAIN)
    assert any("Missing main()" in i for i in issues)


def test_param_constraints_valid():
    script = 'sc.pp.neighbors(adata, n_neighbors=15, n_pcs=50)'
    issues = check_params(script, "scrna_clustering")
    assert len(issues) == 0


def test_param_constraints_out_of_range():
    script = 'sc.pp.neighbors(adata, n_neighbors=500)'
    issues = check_params(script, "scrna_clustering")
    assert any("above max" in i for i in issues)
