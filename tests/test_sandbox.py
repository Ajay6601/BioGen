from biogen.verification.sandbox import execute_in_sandbox


def test_execute_in_sandbox_runs_minimal_script() -> None:
    script = "def main(data_path, output_dir):\n    pass\n"
    ok, _out = execute_in_sandbox(script, "nonexistent.csv", "outputs")
    assert ok is True
