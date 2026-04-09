import json
from dataclasses import dataclass, field, asdict
from biogen.utils.llm_client import call_llm_json
from biogen.generation.prompts import PLANNER_SYSTEM, PLANNER_USER
from biogen.utils.logger import get_logger

log = get_logger("biogen.planner")


@dataclass
class WorkflowStep:
    step_id: int
    name: str
    description: str
    tool: str
    inputs: list[str | int] = field(default_factory=list)
    output_type: str = "Any"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorkflowPlan:
    query: str
    steps: list[WorkflowStep] = field(default_factory=list)
    raw_json: str = ""

    @property
    def analysis_type(self) -> str:
        tools = {s.tool.lower() for s in self.steps}
        if "pydeseq2" in tools:
            return "bulk_rnaseq_de"
        if "scanpy" in tools:
            return "scrna_clustering"
        return "visualization"


def plan_workflow(query: str, data_info: str = "count matrix CSV") -> WorkflowPlan:
    """Decompose a natural language query into ordered workflow steps."""
    log.info(f"Planning workflow for: {query[:80]}...")

    user_msg = PLANNER_USER.replace("{query}", query).replace("{data_info}", data_info)
    raw = call_llm_json(PLANNER_SYSTEM, user_msg)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse planner output: {e}")
        raise ValueError(f"Planner returned invalid JSON: {raw[:200]}")

    steps_data = parsed.get("steps", [])
    if not steps_data:
        raise ValueError("Planner returned empty step list")

    steps = []
    for s in steps_data:
        steps.append(WorkflowStep(
            step_id=s["step_id"],
            name=s["name"],
            description=s["description"],
            tool=s["tool"],
            inputs=s.get("inputs", []),
            output_type=s.get("output_type", "Any"),
        ))

    plan = WorkflowPlan(query=query, steps=steps, raw_json=raw)
    log.info(f"Plan created: {len(steps)} steps, type={plan.analysis_type}")
    for s in steps:
        log.info(f"  Step {s.step_id}: {s.name} ({s.tool}) → {s.output_type}")

    return plan
