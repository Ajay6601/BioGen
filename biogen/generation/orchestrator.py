from .coder import generate_all_steps
from .linker import link_steps
from .planner import plan_workflow


def run_pipeline(query: str) -> dict[int, str]:
    plan = plan_workflow(query)
    step_codes = generate_all_steps(plan)
    link_steps(step_codes)
    return step_codes
