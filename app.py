"""
Streamlit UI for BioGen.

Run: streamlit run app.py
"""
import tempfile
import time
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="BioGen",
    page_icon="🧬",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .block-container { max-width: 1100px; }
    .stCodeBlock { font-size: 13px; }
    div[data-testid="stStatusWidget"] { display: none; }
    .step-badge {
        display: inline-block;
        background: #e8f4f8;
        border-radius: 6px;
        padding: 4px 10px;
        margin: 2px 4px;
        font-size: 13px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🧬 BioGen")
st.caption(
    "LLM-powered bioinformatics code generation with execution verification. "
    "Upload your data, describe what you want, get a verified workflow."
)

# ---------------------------------------------------------------------------
# Sidebar — data upload
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("📁 Data")

    data_file = st.file_uploader(
        "Count matrix (CSV or h5ad)",
        type=["csv", "tsv", "h5ad"],
    )
    metadata_file = st.file_uploader(
        "Metadata (optional CSV)",
        type=["csv", "tsv"],
    )

    st.divider()
    st.header("⚙️ Settings")
    data_info = st.text_input(
        "Describe your data format",
        value="CSV count matrix with genes as rows, samples as columns. "
              "Metadata CSV with condition column.",
    )

    st.divider()
    st.markdown("**Example queries:**")
    examples = [
        "Run differential expression comparing treated vs control, generate a volcano plot",
        "Process this scRNA-seq dataset: filter, normalize, HVG, PCA, UMAP, Leiden clustering",
        "Create a heatmap of the top 20 DE genes across samples",
    ]
    for ex in examples:
        if st.button(ex[:60] + "...", key=ex, use_container_width=True):
            st.session_state["query_input"] = ex

# ---------------------------------------------------------------------------
# Main area — query input
# ---------------------------------------------------------------------------
query = st.text_area(
    "🔬 What analysis do you want to run?",
    value=st.session_state.get("query_input", ""),
    height=80,
    placeholder="e.g., Run differential expression comparing treated vs control...",
)

run_btn = st.button("▶ Generate & Verify", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------
if run_btn and query and data_file:
    from biogen.generation.orchestrator import run_pipeline

    tmp = Path(tempfile.mkdtemp(prefix="biogen_ui_"))
    data_path = tmp / data_file.name
    data_path.write_bytes(data_file.getvalue())

    meta_path_str = ""
    if metadata_file:
        meta_path = tmp / metadata_file.name
        meta_path.write_bytes(metadata_file.getvalue())
        meta_path_str = str(meta_path)

    out_dir = tmp / "output"
    out_dir.mkdir()

    status = st.status("Running BioGen pipeline...", expanded=True)

    with status:
        st.write("🗺️ **Phase 1:** Planning workflow...")
        start = time.time()

        state = run_pipeline(
            query=query,
            data_path=str(data_path),
            output_dir=str(out_dir),
            data_info=data_info,
            metadata_path=meta_path_str,
        )

        elapsed = time.time() - start

    final_status = state.get("final_status", "unknown")

    if final_status == "success":
        status.update(label=f"✅ Workflow generated & verified ({elapsed:.1f}s)", state="complete")
    else:
        status.update(label=f"❌ Generation failed ({elapsed:.1f}s)", state="error")

    col_left, col_right = st.columns([1, 1])

    with col_left:
        plan = state.get("plan")
        if plan:
            st.subheader(f"📋 Workflow Plan ({len(plan.steps)} steps)")
            for s in plan.steps:
                st.markdown(
                    f"**Step {s.step_id}:** {s.name}  \n"
                    f"`{s.tool}` → `{s.output_type}`  \n"
                    f"_{s.description}_"
                )
                st.markdown("---")

        v = state.get("verification")
        if v:
            st.subheader("🛡️ Verification")
            checks = [
                ("AST Validation", v.ast_ok),
                ("API signatures", v.api_ok),
                ("Operation order", v.order_ok),
                ("Dependency graph", v.deps_ok),
                ("Parameter constraints", v.params_ok),
                ("Sandbox execution", v.execution_ok),
            ]
            for name, passed in checks:
                icon = "✅" if passed else "❌"
                st.markdown(f"{icon} {name}")

            if v.issues:
                with st.expander(f"⚠️ Issues ({len(v.issues)})"):
                    for issue in v.issues:
                        st.code(issue, language=None)

    image_files: list[Path] = []
    csv_files: list[Path] = []

    with col_right:
        script = state.get("script") or ""
        if script:
            st.subheader("🐍 Generated Workflow")
            st.code(script, language="python", line_numbers=True)

            st.download_button(
                "⬇️ Download workflow.py",
                data=script,
                file_name="workflow.py",
                mime="text/x-python",
            )

        if out_dir.exists():
            output_files = list(out_dir.rglob("*"))
            image_files = [f for f in output_files if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf")]
            csv_files = [f for f in output_files if f.suffix.lower() == ".csv"]

            if image_files:
                st.subheader("📊 Generated Plots")
                for img in image_files:
                    st.image(str(img), caption=img.name, use_container_width=True)

            if csv_files:
                st.subheader("📄 Result Tables")
                import pandas as pd
                for csv_f in csv_files:
                    try:
                        df = pd.read_csv(csv_f)
                        with st.expander(f"📄 {csv_f.name} ({len(df)} rows)"):
                            st.dataframe(df.head(50), use_container_width=True)
                            st.download_button(
                                f"⬇️ {csv_f.name}",
                                data=csv_f.read_bytes(),
                                file_name=csv_f.name,
                                mime="text/csv",
                                key=f"dl_{csv_f.resolve()}",
                            )
                    except Exception:
                        pass

        from biogen.config import SANDBOX_DIR
        if SANDBOX_DIR.exists():
            shown = {f.name for f in image_files}
            for run_dir in sorted(
                (p for p in SANDBOX_DIR.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ):
                sb_out = run_dir / "output"
                if sb_out.exists():
                    for img in sb_out.glob("*.png"):
                        if img.name not in shown:
                            st.image(str(img), caption=img.name, use_container_width=True)
                            shown.add(img.name)
                    break

elif run_btn and not data_file:
    st.warning("Please upload a data file in the sidebar first.")
elif run_btn and not query:
    st.warning("Please enter a query.")
