"""Unit tests for the planner module (run without LLM calls using mocks)."""
import json
from unittest.mock import patch

from biogen.generation.planner import WorkflowPlan, WorkflowStep, plan_workflow

MOCK_PLAN_RESPONSE = json.dumps({
    "steps": [
        {
            "step_id": 1,
            "name": "load_data",
            "description": "Load the count matrix and metadata",
            "tool": "pandas",
            "inputs": ["raw_data"],
            "output_type": "DataFrame"
        },
        {
            "step_id": 2,
            "name": "run_deseq2",
            "description": "Run differential expression with PyDESeq2",
            "tool": "pydeseq2",
            "inputs": [1],
            "output_type": "DeseqStats"
        },
        {
            "step_id": 3,
            "name": "volcano_plot",
            "description": "Generate volcano plot",
            "tool": "matplotlib",
            "inputs": [2],
            "output_type": "Figure"
        }
    ]
})


@patch("biogen.generation.planner.call_llm_json", return_value=MOCK_PLAN_RESPONSE)
def test_plan_workflow_bulk_de(mock_llm):
    plan = plan_workflow("Run DE analysis and make a volcano plot")
    assert isinstance(plan, WorkflowPlan)
    assert len(plan.steps) == 3
    assert plan.analysis_type == "bulk_rnaseq_de"
    assert plan.steps[0].name == "load_data"
    assert plan.steps[1].tool == "pydeseq2"
    mock_llm.assert_called_once()


@patch("biogen.generation.planner.call_llm_json", return_value=MOCK_PLAN_RESPONSE)
def test_plan_step_ordering(mock_llm):
    plan = plan_workflow("test query")
    ids = [s.step_id for s in plan.steps]
    assert ids == sorted(ids), "Steps should be in ascending order"
