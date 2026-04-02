import re
from biogen.generation.planner import WorkflowPlan, WorkflowStep
from biogen.utils.llm_client import call_llm
from biogen.generation.prompts import CODER_SYSTEM, CODER_USER
from biogen.utils.logger import get_logger

log = get_logger("biogen.coder")


def _strip_fences(code: str) -> str:
    """Remove markdown code fences if present."""
    code = code.strip()
    if code.startswith("```"):
        # Remove opening fence (```python or ```)
        code = re.sub(r"^```\w*\n?", "", code)
    if code.endswith("```"):
        code = code[:-3]
    return code.strip()


def _build_prev_outputs(plan: WorkflowPlan, current_step_id: int) -> str:
    """Build description of previous step outputs for the coder prompt."""
    lines = []
    for s in plan.steps:
        if s.step_id < current_step_id:
            lines.append(
                f"- step_{s.step_id}() returns {s.output_type} "
                f"(variable name: result_{s.step_id})"
            )
    return "\n".join(lines) if lines else "None (this is the first step)"


def generate_step_code(step: WorkflowStep, plan: WorkflowPlan) -> str:
    """Generate Python code for a single workflow step."""
    log.info(f"Generating code for step {step.step_id}: {step.name}")

    prev = _build_prev_outputs(plan, step.step_id)
    system = CODER_SYSTEM.format(prev_outputs=prev)
    user = CODER_USER.format(
        step_id=step.step_id,
        name=step.name,
        description=step.description,
        tool=step.tool,
        inputs=step.inputs,
        output_type=step.output_type,
    )

    raw = call_llm(system, user)
    code = _strip_fences(raw)

    if not code:
        raise ValueError(f"Coder returned empty code for step {step.step_id}")

    log.info(f"  Generated {len(code.splitlines())} lines")
    return code


def generate_all_steps(plan: WorkflowPlan) -> dict[int, str]:
    """Generate code for all steps in the plan. Returns {step_id: code}."""
    step_codes = {}
    for step in plan.steps:
        code = generate_step_code(step, plan)
        step_codes[step.step_id] = code
    log.info(f"Generated code for {len(step_codes)} steps")
    return step_codes
