"""
LangGraph orchestration: plan → code → link → verify → decide
"""
from typing import TypedDict, cast

from langgraph.graph import END, StateGraph

from biogen.generation.coder import generate_all_steps
from biogen.generation.linker import link_steps
from biogen.generation.planner import WorkflowPlan, plan_workflow
from biogen.utils.logger import get_logger
from biogen.verification.verifier import VerificationResult, verify_script

log = get_logger("biogen.orchestrator")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class PipelineState(TypedDict):
    query: str
    data_path: str
    data_info: str
    output_dir: str
    plan: WorkflowPlan | None
    step_codes: dict[int, str] | None
    script: str | None
    verification: VerificationResult | None
    final_status: str  # "success" | "failed" | "needs_fix"
    attempt: int


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def plan_node(state: PipelineState) -> dict:
    log.info("[bold cyan]Phase 1: Planning workflow...[/]")
    plan = plan_workflow(state["query"], state["data_info"])
    return {"plan": plan}


def code_node(state: PipelineState) -> dict:
    log.info("[bold cyan]Phase 2: Generating step code...[/]")
    plan = state["plan"]
    assert plan is not None
    step_codes = generate_all_steps(plan)
    return {"step_codes": step_codes}


def link_node(state: PipelineState) -> dict:
    log.info("[bold cyan]Phase 3: Linking into script...[/]")
    plan = state["plan"]
    step_codes = state["step_codes"]
    assert plan is not None and step_codes is not None
    script = link_steps(plan, step_codes)
    return {"script": script}


def verify_node(state: PipelineState) -> dict:
    log.info("[bold cyan]Phase 4: Verifying script...[/]")
    script = state["script"]
    plan = state["plan"]
    assert script is not None and plan is not None
    result = verify_script(
        script=script,
        plan=plan,
        data_path=state["data_path"],
        output_dir=state["output_dir"],
    )
    return {"verification": result}


def decide_node(state: PipelineState) -> dict:
    v = state["verification"]
    attempt = state["attempt"]
    assert v is not None

    if v.passed:
        log.info("[bold green]✓ Script passed all verification checks[/]")
        return {"final_status": "success"}

    if attempt >= 2:
        log.warning(f"[bold red]✗ Failed after {attempt} attempts[/]")
        for issue in v.issues:
            log.warning(f"  - {issue}")
        return {"final_status": "failed"}

    log.warning(f"[bold yellow]⟳ Attempt {attempt}: issues found, regenerating...[/]")
    for issue in v.issues:
        log.warning(f"  - {issue}")
    return {"final_status": "needs_fix", "attempt": attempt + 1}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_decide(state: PipelineState):
    if state["final_status"] == "needs_fix":
        return "code"
    return END


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def build_graph():
    g = StateGraph(PipelineState)

    g.add_node("plan", plan_node)
    g.add_node("code", code_node)
    g.add_node("link", link_node)
    g.add_node("verify", verify_node)
    g.add_node("decide", decide_node)

    g.set_entry_point("plan")
    g.add_edge("plan", "code")
    g.add_edge("code", "link")
    g.add_edge("link", "verify")
    g.add_edge("verify", "decide")
    g.add_conditional_edges("decide", route_after_decide)

    return g.compile()


def run_pipeline(
    query: str,
    data_path: str,
    output_dir: str,
    data_info: str = "count matrix CSV",
) -> PipelineState:
    """Run the full generation + verification pipeline."""
    graph = build_graph()

    initial_state: PipelineState = {
        "query": query,
        "data_path": data_path,
        "data_info": data_info,
        "output_dir": output_dir,
        "plan": None,
        "step_codes": None,
        "script": None,
        "verification": None,
        "final_status": "",
        "attempt": 1,
    }

    final = graph.invoke(initial_state)
    return cast(PipelineState, final)
