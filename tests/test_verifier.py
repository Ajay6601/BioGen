from biogen.verification.verifier import verify_pipeline


def test_verify_pipeline_true_for_valid_code() -> None:
    code_blocks = ["x = 1", "y = x + 1"]
    assert verify_pipeline(code_blocks) is True
