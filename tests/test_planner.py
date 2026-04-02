import json
from unittest.mock import patch

from biogen.generation.planner import WorkflowStep, plan_workflow


def test_plan_workflow_parses_steps_from_llm_json() -> None:
    payload = {
        "steps": [
            {
                "step_id": 1,
                "name": "load",
                "description": "Load data",
                "tool": "scanpy",
                "inputs": ["raw_data"],
                "output_type": "AnnData",
            }
        ]
    }
    with patch("biogen.generation.planner.call_llm_json", return_value=json.dumps(payload)):
        plan = plan_workflow("Cluster my scRNA data")

    assert len(plan.steps) == 1
    assert plan.steps[0] == WorkflowStep(
        step_id=1,
        name="load",
        description="Load data",
        tool="scanpy",
        inputs=["raw_data"],
        output_type="AnnData",
    )
    assert plan.analysis_type == "scrna_clustering"
