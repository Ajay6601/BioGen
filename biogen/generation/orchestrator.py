###############################################################################
# FILE: biogen/generation/orchestrator.py
###############################################################################
"""
LangGraph orchestration: inspect → select templates → assemble → execute → done

Key architecture decision:
  The LLM NEVER writes code. It selects from pre-validated templates and
  fills parameters. The assembler stitches them into a script.
  In-process execution (`execute_pipeline`) validates the workflow end-to-end.
"""
from typing import TypedDict

from langgraph.graph import StateGraph, END

from biogen.generation.data_inspector import inspect_data, DataProfile
from biogen.generation.assembler import select_templates, assemble_script
from biogen.execution.executor import execute_pipeline, PipelineResult
from biogen.utils.logger import get_logger

log = get_logger("biogen.orchestrator")


class PipelineState(TypedDict):
    query: str
    data_path: str
    metadata_path: str
    output_dir: str
    data_profile: DataProfile | None
    selected_steps: list[dict] | None
    script: str | None
    execution_result: PipelineResult | None
    final_status: str


def inspect_node(state: PipelineState) -> dict:
    log.info("[bold cyan]Phase 1: Inspecting data...[/]")
    meta = state["metadata_path"] if state["metadata_path"] else None
    profile = inspect_data(state["data_path"], meta)
    log.info(f"\n{profile.to_prompt_context()}")
    return {"data_profile": profile}


def select_node(state: PipelineState) -> dict:
    """LLM picks templates + fills params. No code generation."""
    log.info("[bold cyan]Phase 2: Selecting workflow templates...[/]")
    profile = state["data_profile"]
    assert profile is not None
    steps = select_templates(state["query"], profile)
    for s in steps:
        log.info(f"  → {s['template_id']}: {s.get('params', {})}")
    return {"selected_steps": steps}


def assemble_node(state: PipelineState) -> dict:
    """Stitch pre-validated templates into a runnable script (built-in adapter if none)."""
    log.info("[bold cyan]Phase 3: Assembling script from templates...[/]")
    profile = state["data_profile"]
    assert profile is not None
    analysis_type = profile.inferred_experiment or "bulk_rnaseq"

    script = assemble_script(
        steps=state["selected_steps"],
        data_path=state["data_path"],
        output_dir=state["output_dir"],
        metadata_path=state["metadata_path"],
        analysis_type=analysis_type,
    )
    return {"script": script}


def execute_node(state: PipelineState) -> dict:
    """Run the pipeline in-process (stepwise execution with validation)."""
    log.info("[bold cyan]Phase 4: Executing pipeline in-process...[/]")
    profile = state["data_profile"]
    assert profile is not None
    result = execute_pipeline(
        selected_steps=state["selected_steps"],
        data_path=state["data_path"],
        output_dir=state["output_dir"],
        metadata_path=state["metadata_path"] or "",
        profile=profile,
        params={},
    )
    if result.success:
        log.info("[bold green]  ✓ Execution successful[/]")
    else:
        for e in result.errors:
            log.warning(f"  ✗ {e}")
    return {"execution_result": result}


def decide_node(state: PipelineState) -> dict:
    er = state.get("execution_result")
    if er and er.success:
        log.info("[bold green]✓ Workflow generated successfully[/]")
        return {"final_status": "success"}
    log.warning("[bold red]✗ Execution failed — see logs above[/]")
    return {"final_status": "failed"}


def build_graph():
    g = StateGraph(PipelineState)
    g.add_node("inspect", inspect_node)
    g.add_node("select", select_node)
    g.add_node("assemble", assemble_node)
    g.add_node("execute", execute_node)
    g.add_node("decide", decide_node)

    g.set_entry_point("inspect")
    g.add_edge("inspect", "select")
    g.add_edge("select", "assemble")
    g.add_edge("assemble", "execute")
    g.add_edge("execute", "decide")
    g.add_edge("decide", END)

    return g.compile()


def run_pipeline(
    query: str,
    data_path: str,
    output_dir: str,
    data_info: str = "",
    metadata_path: str = "",
) -> dict:
    """Run the full pipeline: inspect → select → assemble → execute."""
    graph = build_graph()
    initial: PipelineState = {
        "query": query,
        "data_path": data_path,
        "metadata_path": metadata_path,
        "output_dir": output_dir,
        "data_profile": None,
        "selected_steps": None,
        "script": None,
        "execution_result": None,
        "final_status": "",
    }
    return graph.invoke(initial)
