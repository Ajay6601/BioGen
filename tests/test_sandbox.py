from biogen.verification.sandbox import execute_safely


def test_execute_safely_returns_ok_status() -> None:
    result = execute_safely("a = 1\nb = a + 2")
    assert result["status"] == "ok"
