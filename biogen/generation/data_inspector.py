"""
Inspects uploaded data BEFORE planning — extracts schema, stats, quality
issues, and infers experiment type. This is what makes the pipeline
data-adaptive instead of templated.
"""
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from biogen.utils.logger import get_logger

log = get_logger("biogen.inspector")


@dataclass
class DataProfile:
    """Everything the planner/coder need to know about the actual data."""
    file_type: str = ""                     # csv, tsv, h5ad
    n_genes: int = 0
    n_samples: int = 0
    gene_id_column: str | None = None       # or index
    sample_names: list[str] = field(default_factory=list)
    column_names: list[str] = field(default_factory=list)

    # Metadata
    has_metadata: bool = False
    metadata_columns: list[str] = field(default_factory=list)
    condition_column: str | None = None     # auto-detected
    conditions: list[str] = field(default_factory=list)
    samples_per_condition: dict[str, int] = field(default_factory=dict)

    # Data quality
    dtype: str = ""                         # int (raw counts) or float (normalized)
    has_negative_values: bool = False
    zero_fraction: float = 0.0
    median_total_counts: float = 0.0
    min_total_counts: float = 0.0
    max_total_counts: float = 0.0
    low_count_genes: int = 0                # genes with total < 10
    constant_genes: int = 0                 # zero-variance genes

    # Inferred
    inferred_data_type: str = ""            # raw_counts, normalized, log_transformed, tpm
    inferred_experiment: str = ""           # bulk_rnaseq, scrna_seq, unknown
    quality_warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        """Format as context string for LLM prompts — this is the key output."""
        lines = [
            "DATA PROFILE (auto-inspected from uploaded file):",
            f"  File type: {self.file_type}",
            f"  Shape: {self.n_genes} genes × {self.n_samples} samples",
            f"  Sample names: {self.sample_names[:10]}{'...' if len(self.sample_names) > 10 else ''}",
            f"  Data type: {self.inferred_data_type}",
            f"  Inferred experiment: {self.inferred_experiment}",
        ]
        if self.has_metadata:
            lines.append(f"  Metadata columns: {self.metadata_columns}")
            if self.condition_column:
                lines.append(f"  Condition column: '{self.condition_column}'")
                lines.append(f"  Conditions: {self.conditions}")
                lines.append(f"  Samples per condition: {self.samples_per_condition}")

        lines.append(f"  Zero fraction: {self.zero_fraction:.1%}")
        lines.append(f"  Median total counts/sample: {self.median_total_counts:.0f}")
        lines.append(f"  Low-count genes (total < 10): {self.low_count_genes}")
        lines.append(f"  Constant (zero-variance) genes: {self.constant_genes}")

        if self.quality_warnings:
            lines.append("\n  ⚠️ QUALITY WARNINGS:")
            for w in self.quality_warnings:
                lines.append(f"    - {w}")

        if self.recommendations:
            lines.append("\n  💡 RECOMMENDATIONS (use these in your code):")
            for r in self.recommendations:
                lines.append(f"    - {r}")

        return "\n".join(lines)


def _detect_condition_column(meta_df: pd.DataFrame) -> tuple[str | None, list[str]]:
    """Auto-detect which column represents experimental conditions."""
    known_names = [
        "condition", "group", "treatment", "genotype", "phenotype",
        "status", "disease", "type", "class", "label", "sample_type",
        "cell_type", "celltype", "diagnosis",
    ]

    for col in meta_df.columns:
        if col.lower().strip() in known_names:
            vals = meta_df[col].dropna().unique().tolist()
            if 2 <= len(vals) <= 20:
                return col, [str(v) for v in vals]

    for col in meta_df.columns:
        if col.lower() in ("sample", "sample_id", "id", "name", "barcode"):
            continue
        if meta_df[col].dtype == object or meta_df[col].nunique() <= 10:
            vals = meta_df[col].dropna().unique().tolist()
            if 2 <= len(vals) <= 10:
                return col, [str(v) for v in vals]

    return None, []


def _infer_data_type(values: np.ndarray) -> str:
    """Infer whether data is raw counts, normalized, log-transformed, or TPM."""
    if np.any(values < 0):
        return "log_transformed"

    is_integer = np.allclose(values, np.round(values), equal_nan=True)
    max_val = np.nanmax(values)
    median_val = np.nanmedian(values[values > 0]) if np.any(values > 0) else 0

    if is_integer and max_val > 100:
        return "raw_counts"
    if not is_integer and max_val < 30:
        return "log_transformed"
    if not is_integer and median_val > 1 and max_val > 100:
        return "tpm_or_fpkm"
    if is_integer:
        return "raw_counts"
    return "normalized"


def inspect_csv(
    data_path: str,
    metadata_path: str | None = None,
) -> DataProfile:
    """Inspect a CSV/TSV count matrix and optional metadata."""
    profile = DataProfile()
    path = Path(data_path)

    sep = "\t" if path.suffix in (".tsv", ".tab") else ","
    profile.file_type = "tsv" if sep == "\t" else "csv"

    df = pd.read_csv(path, sep=sep, index_col=0)
    profile.n_genes = df.shape[0]
    profile.n_samples = df.shape[1]
    profile.sample_names = df.columns.tolist()
    profile.column_names = df.columns.tolist()
    profile.gene_id_column = df.index.name or "index"

    numeric = df.select_dtypes(include=[np.number])
    if numeric.empty:
        profile.quality_warnings.append("No numeric columns found in data")
        return profile

    values = numeric.values.astype(float)
    profile.has_negative_values = bool(np.any(values < 0))
    profile.zero_fraction = float(np.mean(values == 0))
    totals = np.nansum(values, axis=0)
    profile.median_total_counts = float(np.median(totals))
    profile.min_total_counts = float(np.min(totals))
    profile.max_total_counts = float(np.max(totals))

    gene_totals = np.nansum(values, axis=1)
    profile.low_count_genes = int(np.sum(gene_totals < 10))
    gene_vars = np.nanvar(values, axis=1)
    profile.constant_genes = int(np.sum(gene_vars == 0))

    profile.inferred_data_type = _infer_data_type(values)
    profile.dtype = "int" if np.allclose(values, np.round(values), equal_nan=True) else "float"

    if profile.n_samples > 50 and profile.zero_fraction > 0.7:
        profile.inferred_experiment = "scrna_seq"
    else:
        profile.inferred_experiment = "bulk_rnaseq"

    if metadata_path and Path(metadata_path).exists():
        meta_sep = "\t" if Path(metadata_path).suffix in (".tsv", ".tab") else ","
        meta_df = pd.read_csv(metadata_path, sep=meta_sep)
        profile.has_metadata = True
        profile.metadata_columns = meta_df.columns.tolist()

        cond_col, conditions = _detect_condition_column(meta_df)
        if cond_col:
            profile.condition_column = cond_col
            profile.conditions = conditions
            profile.samples_per_condition = (
                meta_df[cond_col].value_counts().to_dict()
            )

    if profile.low_count_genes > profile.n_genes * 0.5:
        profile.quality_warnings.append(
            f"{profile.low_count_genes}/{profile.n_genes} genes have very low counts "
            f"(total < 10). Filter these before DE analysis."
        )
        profile.recommendations.append(
            "Add a gene filtering step: keep genes where total counts >= 10"
        )

    if profile.constant_genes > 0:
        profile.quality_warnings.append(
            f"{profile.constant_genes} genes have zero variance. Remove before PCA/DE."
        )
        profile.recommendations.append(
            f"Remove {profile.constant_genes} zero-variance genes before analysis"
        )

    lib_size_ratio = profile.max_total_counts / max(profile.min_total_counts, 1)
    if lib_size_ratio > 5:
        profile.quality_warnings.append(
            f"Library size varies {lib_size_ratio:.1f}x across samples "
            f"(min={profile.min_total_counts:.0f}, max={profile.max_total_counts:.0f}). "
            f"Normalization is critical."
        )
        profile.recommendations.append(
            "Apply size factor normalization (DESeq2 handles this internally)"
        )

    if profile.inferred_data_type != "raw_counts" and profile.inferred_experiment == "bulk_rnaseq":
        profile.quality_warnings.append(
            f"Data appears to be {profile.inferred_data_type}, not raw counts. "
            f"DESeq2 requires raw integer counts."
        )
        profile.recommendations.append(
            "If running DESeq2, ensure input is raw counts, not normalized values"
        )

    if profile.has_metadata and not profile.condition_column:
        profile.quality_warnings.append(
            "Could not auto-detect condition column in metadata. "
            f"Available columns: {profile.metadata_columns}"
        )
        profile.recommendations.append(
            "Specify the condition column explicitly or rename it to 'condition'"
        )

    if profile.n_samples < 3:
        profile.quality_warnings.append(
            f"Only {profile.n_samples} samples. DE analysis needs ≥2 per group."
        )

    if not profile.has_metadata and profile.inferred_experiment == "bulk_rnaseq":
        profile.quality_warnings.append(
            "No metadata file provided. Will attempt to infer conditions from sample names."
        )
        profile.recommendations.append(
            "Try to parse condition from sample names (e.g. 'treated_1' → condition='treated')"
        )

    log.info(f"Inspected {path.name}: {profile.n_genes} genes × {profile.n_samples} samples")
    log.info(f"  Type: {profile.inferred_data_type} | Experiment: {profile.inferred_experiment}")
    if profile.quality_warnings:
        for w in profile.quality_warnings:
            log.warning(f"  ⚠️ {w}")

    return profile


def inspect_h5ad(data_path: str) -> DataProfile:
    """Inspect an h5ad (AnnData) file."""
    import anndata as ad
    import scipy.sparse

    profile = DataProfile()
    profile.file_type = "h5ad"

    adata = ad.read_h5ad(data_path)
    profile.n_genes = adata.n_vars
    profile.n_samples = adata.n_obs
    profile.sample_names = adata.obs_names.tolist()[:20]
    profile.column_names = adata.var_names.tolist()[:20]

    if len(adata.obs.columns) > 0:
        profile.has_metadata = True
        profile.metadata_columns = adata.obs.columns.tolist()

        cond_col, conditions = _detect_condition_column(adata.obs)
        if cond_col:
            profile.condition_column = cond_col
            profile.conditions = conditions
            profile.samples_per_condition = (
                adata.obs[cond_col].value_counts().to_dict()
            )

    X = adata.X
    if scipy.sparse.issparse(X):
        X = X.toarray()
    values = np.asarray(X, dtype=float)

    profile.has_negative_values = bool(np.any(values < 0))
    profile.zero_fraction = float(np.mean(values == 0))
    totals = np.nansum(values, axis=1)
    profile.median_total_counts = float(np.median(totals))
    profile.min_total_counts = float(np.min(totals))
    profile.max_total_counts = float(np.max(totals))

    gene_totals = np.nansum(values, axis=0)
    profile.low_count_genes = int(np.sum(gene_totals < 10))
    gene_vars = np.nanvar(values, axis=0)
    profile.constant_genes = int(np.sum(gene_vars == 0))

    profile.inferred_data_type = _infer_data_type(values)
    profile.dtype = "int" if np.allclose(values, np.round(values), equal_nan=True) else "float"

    if profile.n_samples > 100 and profile.zero_fraction > 0.5:
        profile.inferred_experiment = "scrna_seq"
    elif profile.n_samples > 50:
        profile.inferred_experiment = "scrna_seq"
    else:
        profile.inferred_experiment = "bulk_rnaseq"

    mito_genes = [g for g in adata.var_names if str(g).startswith(("MT-", "mt-"))]
    if mito_genes and profile.inferred_experiment == "scrna_seq":
        profile.recommendations.append(
            f"Found {len(mito_genes)} mitochondrial genes (MT-). "
            f"Calculate percent_mito and filter cells with high mitochondrial content."
        )

    if profile.zero_fraction > 0.9:
        profile.quality_warnings.append(
            f"Extreme sparsity ({profile.zero_fraction:.1%} zeros). "
            f"Verify this is expected for your assay."
        )

    if profile.inferred_data_type == "log_transformed":
        profile.quality_warnings.append(
            "Data appears already log-transformed. Skip sc.pp.log1p()."
        )
        profile.recommendations.append(
            "Do NOT call sc.pp.log1p() — data is already log-scale"
        )

    if profile.inferred_data_type == "raw_counts":
        profile.recommendations.append(
            "Data is raw counts. Apply sc.pp.normalize_total() then sc.pp.log1p()"
        )

    log.info(
        f"Inspected {Path(data_path).name}: {profile.n_genes} genes × {profile.n_samples} cells"
    )
    return profile


def inspect_data(data_path: str, metadata_path: str | None = None) -> DataProfile:
    """Auto-detect file type and inspect accordingly."""
    path = Path(data_path)

    if path.suffix == ".h5ad":
        return inspect_h5ad(data_path)
    if path.suffix in (".csv", ".tsv", ".tab", ".txt"):
        return inspect_csv(data_path, metadata_path)
    log.warning(f"Unknown file type: {path.suffix}, trying CSV")
    return inspect_csv(data_path, metadata_path)
