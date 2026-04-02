import subprocess
import tempfile
import os
from pathlib import Path
from biogen.config import SANDBOX_TIMEOUT, SANDBOX_DIR
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
    # Write script to temp file
    run_dir = SANDBOX_DIR / f"run_{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    script_path = run_dir / "workflow.py"
    out_dir = run_dir / "output"
    out_dir.mkdir(exist_ok=True)

    # Inject data_path and output_dir into the script's main call
    # Replace argparse with direct values for sandbox
    patched = script
    if "argparse" in script:
        # Add a direct invocation at the bottom
        patched += f'\n\n# --- Sandbox injection ---\n'
        patched += f'if __name__ == "__main__":\n'
        patched += f'    main("{data_path}", "{str(out_dir)}")\n'
    elif 'if __name__' in script:
        # Replace sys.argv references
        patched = patched.replace("sys.argv[1]", f'"{data_path}"')
        patched = patched.replace("sys.argv[2]", f'"{str(out_dir)}"')

    script_path.write_text(patched)
    log.info(f"  Sandbox script: {script_path}")
    log.info(f"  Data path: {data_path}")

    try:
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT,
            cwd=str(run_dir),
            env={**os.environ, "MPLBACKEND": "Agg"},  # Non-interactive matplotlib
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        combined = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}" if stderr else stdout

        if result.returncode != 0:
            log.warning(f"  Sandbox failed (exit {result.returncode})")
            # Extract the actual error from stderr
            error_lines = stderr.splitlines()
            # Get last meaningful error
            actual_error = ""
            for line in reversed(error_lines):
                if line.strip() and not line.startswith("Traceback"):
                    actual_error = line.strip()
                    break
            return False, actual_error or combined[:500]

        # Check that output files were created
        output_files = list(out_dir.iterdir())
        if output_files:
            log.info(f"  Output files: {[f.name for f in output_files]}")
        else:
            log.warning("  No output files generated")

        return True, combined

    except subprocess.TimeoutExpired:
        log.warning(f"  Sandbox timed out ({SANDBOX_TIMEOUT}s)")
        return False, f"Execution timed out after {SANDBOX_TIMEOUT}s"
    except Exception as e:
        log.error(f"  Sandbox error: {e}")
        return False, str(e)
