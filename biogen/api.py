"""
FastAPI server for BioGen.

Run: uvicorn biogen.api:app --reload --port 8000
"""
import base64
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from biogen.generation.orchestrator import run_pipeline
from biogen.utils.logger import get_logger

log = get_logger("biogen.api")

app = FastAPI(title="BioGen", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerateResponse(BaseModel):
    status: str
    query: str
    analysis_type: str
    plan_steps: list[dict]
    generated_script: str
    verification: dict
    output_files: dict[str, str]  # filename → base64 content
    errors: list[str]


def _encode_file(path: Path) -> str:
    """Read file and return base64 string."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


@app.post("/generate", response_model=GenerateResponse)
async def generate(
    query: str = Form(...),
    data_file: UploadFile = File(...),
    metadata_file: UploadFile | None = File(default=None),
    data_info: str = Form("count matrix CSV"),
):
    """Generate a bioinformatics workflow from a natural language query."""
    tmp = Path(tempfile.mkdtemp(prefix="biogen_"))

    try:
        data_path = tmp / (data_file.filename or "data")
        data_path.write_bytes(await data_file.read())

        meta_path_str = ""
        if metadata_file and metadata_file.filename:
            meta_path = tmp / metadata_file.filename
            meta_path.write_bytes(await metadata_file.read())
            meta_path_str = str(meta_path)

        out_dir = tmp / "output"
        out_dir.mkdir()

        state = run_pipeline(
            query=query,
            data_path=str(data_path),
            output_dir=str(out_dir),
            data_info=data_info,
            metadata_path=meta_path_str,
        )

        output_files: dict[str, str] = {}
        if out_dir.exists():
            for f in out_dir.rglob("*"):
                if f.is_file():
                    output_files[f.name] = _encode_file(f)

        plan_steps: list[dict] = state.get("selected_steps") or []
        profile = state.get("data_profile")
        analysis_type = (
            profile.inferred_experiment if profile is not None else "unknown"
        )

        er = state.get("execution_result")
        verification = {
            "execution_ok": er.success if er else False,
            "passed": er.success if er else False,
        }

        errors = list(er.errors) if er and er.errors else []
        script = state.get("script") or ""

        return GenerateResponse(
            status=state.get("final_status", "unknown"),
            query=query,
            analysis_type=analysis_type,
            plan_steps=plan_steps,
            generated_script=script,
            verification=verification,
            output_files=output_files,
            errors=errors,
        )

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "biogen"}
