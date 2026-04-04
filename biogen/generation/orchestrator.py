"""
LangGraph orchestration: inspect → plan → code → link → verify → recover → decide

Key difference from a naive pipeline:
  1. Data inspection runs FIRST — the planner/coder see actual column names,
     data types, quality issues, and recommendations.
  2. Error recovery classifies failures and patches the script with awareness
     of both the traceback AND the data profile.
  3. Retry loop feeds error context back, not just "try again."
"""
from typing import TypedDict, cast

from langgraph.graph import END, StateGraph

from biogen.generation.coder import generate_all_steps
from biogen.generation.data_inspector import DataProfile, inspect_data
from biogen.generation.error_recovery import classify_error, repair_script
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
    metadata_path: str
    data_info: str
    output_dir: str
    data_profile: DataProfile | None
    plan: WorkflowPlan | None
    step_codes: dict[int, str] | None
    script: str | None
    verification: VerificationResult | None
    last_error: str
    final_status: str
    attempt: int


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def inspect_node(state: PipelineState) -> dict:
    """Inspect the actual data — schema, types, quality, recommendations."""
    log.info("[bold cyan]Phase 0: Inspecting data...[/]")
    meta = state["metadata_path"].strip() or None

    profile = inspect_data(state["data_path"], meta)
    log.info(f"\n{profile.to_prompt_context()}")
    return {"data_profile": profile}


def plan_node(state: PipelineState) -> dict:
    """Plan with full data awareness — the LLM sees actual column names, types, warnings."""
    log.info("[bold cyan]Phase 1: Planning workflow...[/]")
    profile = state["data_profile"]
    assert profile is not None

    data_info = profile.to_prompt_context()
    if state["data_info"].strip():
        data_info = (
            f"USER DATA DESCRIPTION:\n{state['data_info'].strip()}\n\n{data_info}"
        )

    if state["last_error"]:
        data_info += (
            f"\n\n⚠️ PREVIOUS ATTEMPT FAILED WITH:\n{state['last_error'][:500]}\n"
            f"Adjust the plan to avoid this error."
        )

    plan = plan_workflow(state["query"], data_info)
    return {"plan": plan}


def code_node(state: PipelineState) -> dict:
    """Generate code — prompts include actual data profile so code references real columns."""
    log.info("[bold cyan]Phase 2: Generating step code...[/]")
    plan = state["plan"]
    assert plan is not None
    profile = state["data_profile"]
    assert profile is not None
    profile_text = profile.to_prompt_context()

    for step in plan.steps:
        step.description = (
            f"{step.description}\n\n"
            f"ACTUAL DATA CONTEXT:\n{profile_text}"
        )

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


def recover_node(state: PipelineState) -> dict:
    """If verification failed, diagnose and repair — not a blind retry."""
    v = state["verification"]
    attempt = state["attempt"]
    assert v is not None

    if v.passed:
        log.info("[bold green]✓ All checks passed[/]")
        return {"final_status": "success"}

    if attempt >= 3:
        log.warning(f"[bold red]✗ Failed after {attempt} attempts[/]")
        for issue in v.issues:
            log.warning(f"  - {issue}")
        return {"final_status": "failed"}

    log.warning(f"[bold yellow]⟳ Attempt {attempt}: diagnosing failure...[/]")

    exec_errors = [i for i in v.issues if i.startswith("[EXEC]")]
    error_text = exec_errors[0] if exec_errors else "; ".join(v.issues)

    diagnosis = classify_error(error_text)
    log.info(f"  Category: {diagnosis['category']}")
    log.info(f"  Hint: {diagnosis['fix_hint']}")

    profile = state.get("data_profile")
    profile_text = profile.to_prompt_context() if profile else ""
    script = state.get("script") or ""

    try:
        repaired = repair_script(
            script=script,
            error_text=error_text,
            data_profile_text=profile_text,
        )
        return {
            "script": repaired,
            "last_error": error_text,
            "final_status": "needs_fix",
            "attempt": attempt + 1,
        }
    except Exception as e:
        log.error(f"  Repair failed: {e}")
        return {
            "last_error": error_text,
            "final_status": "needs_fix",
            "attempt": attempt + 1,
        }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_recover(state: PipelineState):
    if state["final_status"] == "needs_fix":
        return "verify"
    return END


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def build_graph():
    g = StateGraph(PipelineState)

    g.add_node("inspect", inspect_node)
    g.add_node("plan", plan_node)
    g.add_node("code", code_node)
    g.add_node("link", link_node)
    g.add_node("verify", verify_node)
    g.add_node("recover", recover_node)

    g.set_entry_point("inspect")
    g.add_edge("inspect", "plan")
    g.add_edge("plan", "code")
    g.add_edge("code", "link")
    g.add_edge("link", "verify")
    g.add_edge("verify", "recover")
    g.add_conditional_edges("recover", route_after_recover)

    return g.compile()


def run_pipeline(
    query: str,
    data_path: str,
    output_dir: str,
    data_info: str = "",
    metadata_path: str = "",
) -> PipelineState:
    """Run the full inspect → generate → verify → recover pipeline."""
    graph = build_graph()

    initial_state: PipelineState = {
        "query": query,
        "data_path": data_path,
        "metadata_path": metadata_path,
        "data_info": data_info,
        "output_dir": output_dir,
        "data_profile": None,
        "plan": None,
        "step_codes": None,
        "script": None,
        "verification": None,
        "last_error": "",
        "final_status": "",
        "attempt": 1,
    }

    final = graph.invoke(initial_state)
    return cast(PipelineState, final)
