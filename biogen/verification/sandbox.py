import os
import subprocess
from pathlib import Path

from biogen.config import SANDBOX_DIR, SANDBOX_TIMEOUT
from biogen.utils.logger import get_logger

log = get_logger("biogen.sandbox")


def execute_in_sandbox(
    script: str,
    data_path: str,
    output_dir: str,
) -> tuple[bool, str]:
    """Execute the generated script in a subprocess sandbox.

    Returns (success: bool, output: str).
    """
    run_dir = SANDBOX_DIR / f"run_{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    script_path = run_dir / "workflow.py"
    out_dir = run_dir / "output"
    out_dir.mkdir(exist_ok=True)

    patched = script
    if "argparse" in script:
        patched += "\n\n# --- Sandbox injection ---\n"
        patched += 'if __name__ == "__main__":\n'
        patched += f'    main("{data_path}", "{str(out_dir)}")\n'
    elif "if __name__" in script:
        patched = patched.replace("sys.argv[1]", f'"{data_path}"')
        patched = patched.replace("sys.argv[2]", f'"{str(out_dir)}"')

    script_path.write_text(patched, encoding="utf-8")
    log.info(f"  Sandbox script: {script_path}")
    log.info(f"  Data path: {data_path}")

    try:
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT,
            cwd=str(run_dir),
            env={**os.environ, "MPLBACKEND": "Agg"},
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        combined = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}" if stderr else stdout

        if result.returncode != 0:
            log.warning(f"  Sandbox failed (exit {result.returncode})")
            error_lines = stderr.splitlines()
            actual_error = ""
            for line in reversed(error_lines):
                if line.strip() and not line.startswith("Traceback"):
                    actual_error = line.strip()
                    break
            return False, actual_error or combined[:500]

        output_files = list(out_dir.iterdir())
        if output_files:
            log.info(f"  Output files: {[f.name for f in output_files]}")
        else:
            log.warning("  No output files generated")

        return True, combined

    except subprocess.TimeoutExpired:
        log.warning(f"  Sandbox timed out ({SANDBOX_TIMEOUT}s)")
        return False, f"Execution timed out after {SANDBOX_TIMEOUT}s"
    except OSError as e:
        log.error(f"  Sandbox error: {e}")
        return False, str(e)
