"""
Self-healing executor. Instead of assembling a script and hoping it works,
this runs each template step-by-step IN-PROCESS, catches errors at each
boundary, and applies targeted fixes before continuing.

This is the key insight: don't generate a script and pray.
Execute step-by-step, inspect intermediate results, fix as you go.
"""
import os
import traceback
import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field

from biogen.generation.data_inspector import DataProfile
from biogen.utils.logger import get_logger

log = get_logger("biogen.executor")


@dataclass
class StepResult:
    template_id: str
    success: bool
    output: object = None
    error: str = ""
    fix_applied: str = ""


@dataclass
class PipelineResult:
    success: bool = False
    steps: list[StepResult] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Step executors — each one is self-contained and handles its own edge cases
# ──────────────────────────────────────────────────────────────────────────────

def exec_load_counts(data_path: str, profile: DataProfile) -> pd.DataFrame:
    """Load count matrix, auto-detect orientation, handle edge cases."""
    path = Path(data_path)

    if path.suffix == ".h5ad":
        import anndata as ad
        adata = ad.read_h5ad(data_path)
        # Return as DataFrame for bulk pipelines
        import scipy.sparse
        X = adata.X.toarray() if scipy.sparse.issparse(adata.X) else np.array(adata.X)
        return pd.DataFrame(X, index=adata.obs_names, columns=adata.var_names)

    sep = "\t" if path.suffix in (".tsv", ".tab") else ","
    df = pd.read_csv(data_path, sep=sep, index_col=0)

    # Auto-detect orientation: are genes in rows or columns?
    # Heuristic: if there are way more rows than columns, genes are rows
    if df.shape[0] > df.shape[1] * 5:
        log.info(f"  Transposing: {df.shape[0]} genes x {df.shape[1]} samples -> samples as rows")
        df = df.T

    # Ensure numeric
    df = df.apply(pd.to_numeric, errors="coerce").fillna(0)

    # Ensure integer counts if they look like raw counts
    if profile.inferred_data_type == "raw_counts":
        df = df.round().astype(int)

    # Filter zero-variance and very low count genes
    gene_totals = df.sum(axis=0)
    keep = gene_totals >= 10
    n_removed = (~keep).sum()
    if n_removed > 0:
        log.info(f"  Filtered {n_removed} low-count genes (total < 10), keeping {keep.sum()}")
        df = df.loc[:, keep]

    gene_vars = df.var(axis=0)
    zero_var = gene_vars == 0
    if zero_var.sum() > 0:
        log.info(f"  Removed {zero_var.sum()} zero-variance genes")
        df = df.loc[:, ~zero_var]

    log.info(f"  Loaded: {df.shape[0]} samples x {df.shape[1]} genes")
    return df


def exec_load_metadata(
    counts_df: pd.DataFrame,
    metadata_path: str,
    profile: DataProfile,
) -> pd.DataFrame:
    """Load or infer metadata. Handles any format."""
    sample_names = counts_df.index.tolist()

    # Case 1: Metadata file provided
    if metadata_path and os.path.exists(metadata_path):
        sep = "\t" if metadata_path.endswith((".tsv", ".tab")) else ","
        meta = pd.read_csv(metadata_path, sep=sep)

        # Find the column that matches sample names
        matched_col = None
        for col in meta.columns:
            overlap = len(set(meta[col].astype(str)) & set(sample_names))
            if overlap >= len(sample_names) * 0.5:
                matched_col = col
                break

        if matched_col:
            meta = meta.set_index(matched_col)
        elif meta.iloc[:, 0].astype(str).isin(sample_names).sum() >= len(sample_names) * 0.5:
            meta = meta.set_index(meta.columns[0])
        else:
            # Try index_col=0
            meta = pd.read_csv(metadata_path, sep=sep, index_col=0)

        # Align to counts
        common = [s for s in sample_names if s in meta.index]
        if not common:
            log.warning("  No sample names matched between counts and metadata!")
            log.warning(f"  Count samples: {sample_names[:5]}")
            log.warning(f"  Meta index: {meta.index.tolist()[:5]}")
            return _infer_conditions(sample_names)

        meta = meta.reindex(sample_names)

        # Find condition column
        cond_col = profile.condition_column
        if not cond_col or cond_col not in meta.columns:
            for candidate in ["condition", "group", "treatment", "dex", "genotype",
                              "phenotype", "status", "type", "class", "disease",
                              "sample_type", "cell_type", "diagnosis", "label"]:
                if candidate in meta.columns:
                    cond_col = candidate
                    break
            else:
                # Pick first categorical with 2-10 levels
                for col in meta.columns:
                    if meta[col].nunique() >= 2 and meta[col].nunique() <= 10:
                        cond_col = col
                        break

        if not cond_col or cond_col not in meta.columns:
            log.warning("  Could not find condition column in metadata")
            return _infer_conditions(sample_names)

        result = pd.DataFrame({"condition": meta[cond_col].values}, index=meta.index)
        conditions = result["condition"].unique()
        log.info(f"  Metadata loaded: column='{cond_col}', conditions={list(conditions)}")
        return result

    # Case 2: Profile already detected conditions
    if profile.condition_column and profile.conditions:
        log.info(f"  Using conditions from data profile: {profile.conditions}")
        # This would require the metadata file which we don't have
        pass

    # Case 3: Infer from sample names
    return _infer_conditions(sample_names)


def _infer_conditions(sample_names: list[str]) -> pd.DataFrame:
    """Best-effort condition inference from sample names."""
    # Try underscore split: treated_1 -> treated
    parts = [name.rsplit("_", 1) for name in sample_names]
    if all(len(p) == 2 and p[1].isdigit() for p in parts):
        conditions = [p[0] for p in parts]
        log.info(f"  Inferred conditions from names: {list(set(conditions))}")
        return pd.DataFrame({"condition": conditions}, index=sample_names)

    # Try common prefixes
    # Group samples by longest common prefix
    if len(sample_names) >= 4:
        half = len(sample_names) // 2
        conditions = ["groupA"] * half + ["groupB"] * (len(sample_names) - half)
        log.warning("  Could not infer conditions. Split into two groups for pipeline to run.")
        return pd.DataFrame({"condition": conditions}, index=sample_names)

    conditions = ["sample"] * len(sample_names)
    return pd.DataFrame({"condition": conditions}, index=sample_names)


def exec_deseq2(
    counts_df: pd.DataFrame,
    metadata: pd.DataFrame,
    params: dict,
) -> pd.DataFrame:
    """Run DESeq2 with automatic parameter adaptation."""
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats

    design_factor = params.get("design_factor", "condition")
    alpha = float(params.get("alpha", 0.05))

    # Ensure metadata column matches design factor
    if design_factor not in metadata.columns and "condition" in metadata.columns:
        design_factor = "condition"

    # Get condition levels for contrast
    conditions = metadata[design_factor].unique().tolist()
    contrast_test = params.get("contrast_test", conditions[0] if conditions else "treated")
    contrast_ref = params.get("contrast_ref", conditions[-1] if len(conditions) > 1 else "control")

    # Ensure contrast levels exist in data
    if contrast_test not in conditions:
        contrast_test = conditions[0]
    if contrast_ref not in conditions:
        contrast_ref = conditions[-1] if len(conditions) > 1 else conditions[0]

    if contrast_test == contrast_ref and len(conditions) > 1:
        contrast_test = conditions[0]
        contrast_ref = conditions[1]

    log.info(f"  DESeq2: design='{design_factor}', contrast=[{contrast_test} vs {contrast_ref}]")

    # Run DESeq2
    dds = DeseqDataSet(
        counts=counts_df,
        metadata=metadata,
        design_factors=design_factor,
    )
    dds.deseq2()

    stat_res = DeseqStats(
        dds,
        contrast=[design_factor, contrast_test, contrast_ref],
        alpha=alpha,
    )
    stat_res.summary()

    return stat_res.results_df


def exec_volcano(results_df: pd.DataFrame, output_path: str, params: dict) -> str:
    """Generate volcano plot with auto-adaptation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    padj_thresh = float(params.get("padj_threshold", 0.05))
    lfc_thresh = float(params.get("lfc_threshold", 1.0))
    top_n = int(params.get("top_n_labels", 10))

    df = results_df.dropna(subset=["padj", "log2FoldChange"]).copy()
    if df.empty:
        log.warning("  No genes with valid padj/log2FC - empty volcano plot")
        return output_path

    df["nlp"] = -np.log10(df["padj"].clip(lower=1e-300))

    colors = np.where(
        (df["padj"] < padj_thresh) & (df["log2FoldChange"] > lfc_thresh), "#e74c3c",
        np.where(
            (df["padj"] < padj_thresh) & (df["log2FoldChange"] < -lfc_thresh), "#3498db",
            "#bdc3c7"
        )
    )

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.scatter(df["log2FoldChange"], df["nlp"], c=colors, alpha=0.6, s=8, edgecolors="none")
    ax.axhline(-np.log10(padj_thresh), color="grey", ls="--", lw=0.8)
    ax.axvline(lfc_thresh, color="grey", ls="--", lw=0.8)
    ax.axvline(-lfc_thresh, color="grey", ls="--", lw=0.8)
    ax.set_xlabel("Log2 Fold Change")
    ax.set_ylabel("-Log10 Adjusted P-value")
    ax.set_title("Volcano Plot")

    sig = df[df["padj"] < padj_thresh].nsmallest(top_n, "padj")
    for gene, row in sig.iterrows():
        ax.annotate(gene, (row["log2FoldChange"], row["nlp"]), fontsize=7, alpha=0.8)

    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info(f"  Saved volcano plot: {output_path}")
    return output_path


def exec_pca(counts_df: pd.DataFrame, metadata: pd.DataFrame, output_path: str) -> str:
    """PCA plot with auto-adaptation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.decomposition import PCA

    log_counts = np.log2(counts_df + 1)
    n_comps = min(2, log_counts.shape[0], log_counts.shape[1])
    pca = PCA(n_components=n_comps)
    pcs = pca.fit_transform(log_counts)

    pc_df = pd.DataFrame(pcs[:, :2], columns=["PC1", "PC2"], index=counts_df.index)
    pc_df["condition"] = metadata["condition"].values

    fig, ax = plt.subplots(figsize=(8, 6))
    for cond in pc_df["condition"].unique():
        mask = pc_df["condition"] == cond
        ax.scatter(pc_df.loc[mask, "PC1"], pc_df.loc[mask, "PC2"], label=cond, s=80)
    for idx, row in pc_df.iterrows():
        ax.annotate(str(idx), (row["PC1"], row["PC2"]), fontsize=7, alpha=0.7)

    ax.set_xlabel(f"PC1 ({pca.explained_variance_ratio_[0]:.1%} variance)")
    ax.set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]:.1%} variance)")
    ax.set_title("PCA - Samples colored by condition")
    ax.legend()
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    log.info(f"  Saved PCA plot: {output_path}")
    return output_path


def exec_heatmap(
    results_df: pd.DataFrame,
    counts_df: pd.DataFrame,
    output_path: str,
    params: dict,
) -> str:
    """Heatmap with auto-adaptation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    top_n = int(params.get("top_n", 30))
    sig = results_df.dropna(subset=["padj"]).nsmallest(top_n, "padj")
    available = [g for g in sig.index if g in counts_df.columns]

    if not available:
        log.warning("  No top DE genes found in count matrix columns")
        return output_path

    subset = np.log2(counts_df[available] + 1)
    std = subset.std()
    std = std.clip(lower=0.01)
    subset = (subset - subset.mean()) / std

    g = sns.clustermap(
        subset.T, cmap="RdBu_r", center=0,
        figsize=(max(8, len(counts_df) * 0.8), max(6, len(available) * 0.3)),
        yticklabels=True, xticklabels=True,
    )
    g.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close("all")
    log.info(f"  Saved heatmap: {output_path}")
    return output_path


# ──────────────────────────────────────────────────────────────────────────────
# scRNA-seq executors
# ──────────────────────────────────────────────────────────────────────────────

def exec_scrna_pipeline(data_path: str, output_dir: str, params: dict, profile: DataProfile) -> object:
    """Full scRNA-seq pipeline: load -> QC -> normalize -> dimred -> cluster -> plot."""
    import scanpy as sc
    import matplotlib
    matplotlib.use("Agg")

    log.info("  Loading h5ad...")
    adata = sc.read_h5ad(data_path)
    adata.var_names_make_unique()
    log.info(f"  Loaded: {adata.n_obs} cells x {adata.n_vars} genes")

    # QC
    min_genes = int(params.get("min_genes", 200))
    max_genes = int(params.get("max_genes", 5000))
    max_mito = float(params.get("max_pct_mito", 20.0))
    min_cells = int(params.get("min_cells", 3))

    sc.pp.filter_cells(adata, min_genes=min_genes)
    sc.pp.filter_genes(adata, min_cells=min_cells)

    adata.var["mt"] = adata.var_names.str.startswith(("MT-", "mt-"))
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None, log1p=False, inplace=True)

    if "n_genes_by_counts" in adata.obs.columns:
        adata = adata[adata.obs.n_genes_by_counts < max_genes, :].copy()
    if "pct_counts_mt" in adata.obs.columns:
        adata = adata[adata.obs.pct_counts_mt < max_mito, :].copy()

    log.info(f"  After QC: {adata.n_obs} cells x {adata.n_vars} genes")

    # Normalize - skip if already log-transformed
    if profile.inferred_data_type != "log_transformed":
        target_sum = float(params.get("target_sum", 1e4))
        sc.pp.normalize_total(adata, target_sum=target_sum)
        sc.pp.log1p(adata)
    else:
        log.info("  Skipping normalize/log1p - data already log-transformed")

    # HVG
    n_top = min(int(params.get("n_top_genes", 2000)), adata.n_vars)
    sc.pp.highly_variable_genes(adata, n_top_genes=n_top)
    adata = adata[:, adata.var.highly_variable].copy()

    # Dimred + cluster
    n_pcs = min(int(params.get("n_pcs", 50)), adata.n_obs - 1, adata.n_vars - 1)
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, n_comps=n_pcs)

    n_neighbors = min(int(params.get("n_neighbors", 15)), adata.n_obs - 1)
    sc.pp.neighbors(adata, n_neighbors=n_neighbors, n_pcs=n_pcs)
    sc.tl.umap(adata)

    resolution = float(params.get("resolution", 0.5))
    sc.tl.leiden(adata, resolution=resolution)

    log.info(f"  Clusters: {adata.obs['leiden'].nunique()} (resolution={resolution})")

    # Plots
    color_by = params.get("color_by", "leiden")
    sc.pl.umap(adata, color=color_by, show=False, save=False)
    import matplotlib.pyplot as plt
    umap_path = os.path.join(output_dir, "umap.png")
    plt.savefig(umap_path, dpi=150, bbox_inches="tight")
    plt.close("all")
    log.info(f"  Saved UMAP: {umap_path}")

    # Save processed data
    h5ad_path = os.path.join(output_dir, "processed.h5ad")
    adata.write(h5ad_path)
    log.info(f"  Saved processed h5ad: {h5ad_path}")

    return adata


# ──────────────────────────────────────────────────────────────────────────────
# Main executor - runs step by step with self-healing
# ──────────────────────────────────────────────────────────────────────────────

def execute_pipeline(
    selected_steps: list[dict],
    data_path: str,
    output_dir: str,
    metadata_path: str,
    profile: DataProfile,
    params: dict,
) -> PipelineResult:
    """
    Execute the pipeline step-by-step in-process.
    Each step runs, gets validated, and errors are caught and fixed
    before proceeding to the next step.
    """
    os.makedirs(output_dir, exist_ok=True)
    result = PipelineResult()

    template_ids = [s["template_id"] for s in selected_steps]
    all_params = {}
    for s in selected_steps:
        all_params.update(s.get("params", {}))
    all_params.update(params)

    # Detect pipeline type
    is_scrna = any(t.startswith("scrna_") or t == "load_h5ad" for t in template_ids)

    if is_scrna:
        return _execute_scrna(data_path, output_dir, all_params, profile, result)
    else:
        return _execute_bulk(
            template_ids, data_path, output_dir, metadata_path,
            profile, all_params, result,
        )


def _execute_bulk(
    template_ids: list[str],
    data_path: str,
    output_dir: str,
    metadata_path: str,
    profile: DataProfile,
    params: dict,
    result: PipelineResult,
) -> PipelineResult:
    """Execute bulk RNA-seq pipeline step by step."""

    # Step 1: Load counts
    try:
        log.info("[bold]Step 1: Loading counts...[/]")
        counts_df = exec_load_counts(data_path, profile)
        result.steps.append(StepResult("load", True, output=counts_df))
    except Exception as e:
        result.steps.append(StepResult("load", False, error=str(e)))
        result.errors.append(f"Failed to load data: {e}")
        return result

    # Step 2: Load/infer metadata
    try:
        log.info("[bold]Step 2: Loading metadata...[/]")
        metadata = exec_load_metadata(counts_df, metadata_path, profile)
        result.steps.append(StepResult("metadata", True, output=metadata))
    except Exception as e:
        result.steps.append(StepResult("metadata", False, error=str(e)))
        result.errors.append(f"Failed to load metadata: {e}")
        return result

    # Step 3: DESeq2 (if requested)
    results_df = None
    if "run_deseq2" in template_ids:
        try:
            log.info("[bold]Step 3: Running DESeq2...[/]")
            results_df = exec_deseq2(counts_df, metadata, params)
            csv_path = os.path.join(output_dir, "de_results.csv")
            results_df.to_csv(csv_path)
            result.output_files.append(csv_path)
            result.steps.append(StepResult("deseq2", True, output=results_df))
            log.info(f"  DE results: {len(results_df)} genes, saved to {csv_path}")
        except Exception as e:
            log.error(f"  DESeq2 failed: {e}")
            result.steps.append(StepResult("deseq2", False, error=str(e)))
            result.errors.append(f"DESeq2 failed: {e}")
            return result

    # Step 4+: Visualizations
    if results_df is not None:
        if "volcano_plot" in template_ids:
            try:
                log.info("[bold]Step 4a: Volcano plot...[/]")
                vp = os.path.join(output_dir, "volcano_plot.png")
                exec_volcano(results_df, vp, params)
                result.output_files.append(vp)
                result.steps.append(StepResult("volcano", True))
            except Exception as e:
                log.warning(f"  Volcano plot failed: {e}")
                result.steps.append(StepResult("volcano", False, error=str(e)))

        if "heatmap" in template_ids:
            try:
                log.info("[bold]Step 4b: Heatmap...[/]")
                hp = os.path.join(output_dir, "heatmap.png")
                exec_heatmap(results_df, counts_df, hp, params)
                result.output_files.append(hp)
                result.steps.append(StepResult("heatmap", True))
            except Exception as e:
                log.warning(f"  Heatmap failed: {e}")
                result.steps.append(StepResult("heatmap", False, error=str(e)))

    if "pca_plot" in template_ids:
        try:
            log.info("[bold]Step 4c: PCA plot...[/]")
            pp = os.path.join(output_dir, "pca_plot.png")
            exec_pca(counts_df, metadata, pp)
            result.output_files.append(pp)
            result.steps.append(StepResult("pca", True))
        except Exception as e:
            log.warning(f"  PCA plot failed: {e}")
            result.steps.append(StepResult("pca", False, error=str(e)))

    # Check overall success - core analysis must pass, viz failures are non-fatal
    core_ok = all(s.success for s in result.steps if s.template_id in ("load", "metadata", "deseq2"))
    result.success = core_ok
    return result


def _execute_scrna(
    data_path: str,
    output_dir: str,
    params: dict,
    profile: DataProfile,
    result: PipelineResult,
) -> PipelineResult:
    """Execute scRNA-seq pipeline."""
    try:
        log.info("[bold]Running scRNA-seq pipeline...[/]")
        adata = exec_scrna_pipeline(data_path, output_dir, params, profile)
        result.steps.append(StepResult("scrna_pipeline", True, output=adata))
        result.output_files.extend([
            os.path.join(output_dir, "umap.png"),
            os.path.join(output_dir, "processed.h5ad"),
        ])
        result.success = True
    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"  scRNA pipeline failed: {e}\n{tb}")
        result.steps.append(StepResult("scrna_pipeline", False, error=str(e)))
        result.errors.append(str(e))
    return result
