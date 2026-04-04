"""Unit tests for sandbox execution."""
import tempfile
from pathlib import Path

from biogen.verification.sandbox import execute_in_sandbox


def test_sandbox_simple_success():
    script = """
import os

def main(data_path: str, output_dir: str):
    with open(os.path.join(output_dir, "test.txt"), "w") as f:
        f.write("hello")

if __name__ == "__main__":
    import sys
    main(sys.argv[1], sys.argv[2])
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_path = str(Path(tmpdir) / "dummy.csv")
        Path(data_path).write_text("a,b\n1,2\n")
        ok, output = execute_in_sandbox(script, data_path, tmpdir)
        assert ok, f"Sandbox should succeed: {output}"


def test_sandbox_syntax_error():
    script = "def broken(\n    pass"
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir)
        dummy = p / "dummy.csv"
        dummy.write_text("x\n")
        ok, output = execute_in_sandbox(script, str(dummy), str(p))
    assert not ok
