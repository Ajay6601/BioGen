import re
from biogen.generation.planner import WorkflowPlan
from biogen.utils.llm_client import call_llm
from biogen.generation.prompts import LINKER_SYSTEM, LINKER_USER
from biogen.utils.logger import get_logger

log = get_logger("biogen.linker")


def _strip_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        code = re.sub(r"^```\w*\n?", "", code)
    if code.endswith("```"):
        code = code[:-3]
    return code.strip()


def link_steps(plan: WorkflowPlan, step_codes: dict[int, str]) -> str:
    """Combine individual step functions into a single executable script."""
    log.info("Linking step functions into integrated script...")

    # Build the step functions block for the prompt
    blocks = []
    for step in plan.steps:
        code = step_codes[step.step_id]
        blocks.append(
            f"# === Step {step.step_id}: {step.name} ===\n"
            f"# Tool: {step.tool} | Output: {step.output_type}\n"
            f"{code}"
        )
    step_functions = "\n\n".join(blocks)

    user = LINKER_USER.format(step_functions=step_functions)
    raw = call_llm(LINKER_SYSTEM, user)
    script = _strip_fences(raw)

    if not script:
        raise ValueError("Linker returned empty script")

    # Ensure the script has a main guard
    if "if __name__" not in script:
        script += '\n\nif __name__ == "__main__":\n    import sys\n    main(sys.argv[1], sys.argv[2])\n'

    log.info(f"Linked script: {len(script.splitlines())} lines")
    return script
