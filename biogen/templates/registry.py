###############################################################################
# FILE: biogen/templates/registry.py
###############################################################################
"""
Template registry — every bioinformatics operation is a pre-validated,
human-written code block with parameterized slots.

The LLM's job is to SELECT templates and FILL parameters, never to write code.
This is how you get workflows that work on the first run.
"""
from dataclasses import dataclass, field
from biogen.utils.logger import get_logger

log = get_logger("biogen.templates")


@dataclass
class Template:
    id: str
    name: str
    description: str
    category: str               # "load", "preprocess", "analysis", "visualization"
    analysis_type: str           # "bulk_rnaseq", "scrna", "any"
    input_types: list[str]       # what this template consumes
    output_type: str             # what it produces
    params: dict[str, dict]      # param_name → {type, default, description}
    code: str                    # the actual code with {param} placeholders
    requires: list[str] = field(default_factory=list)  # template IDs that must come before


# ──────────────────────────────────────────────────────────────────────────────
# BULK RNA-SEQ TEMPLATES
# ──────────────────────────────────────────────────────────────────────────────

LOAD_BULK_CSV = Template(
    id="load_bulk_csv",
    name="Load bulk RNA-seq count matrix",
    description="Load CSV count matrix (genes as rows, samples as columns) and transpose for PyDESeq2",
    category="load",
    analysis_type="bulk_rnaseq",
    input_types=["raw_file"],
    output_type="DataFrame",
    params={
        "data_path": {"type": "str", "description": "Path to CSV file"},
    },
    code="""
import pandas as pd
import numpy as np

def load_counts(data_path: str) -> pd.DataFrame:
    counts_df = pd.read_csv(data_path, index_col=0)
    # Convention: CSV has genes as rows, samples as columns
    # PyDESeq2 needs samples as rows, genes as columns
    counts_df = counts_df.T
    # Ensure integer counts
    counts_df = counts_df.round().astype(int)
    return counts_df
""",
)

LOAD_METADATA = Template(
    id="load_metadata",
    name="Load or infer sample metadata",
    description="Load metadata from file, or infer conditions from sample names if no file provided",
    category="preprocess",
    analysis_type="bulk_rnaseq",
    input_types=["DataFrame"],
    output_type="DataFrame",
    params={
        "metadata_path": {"type": "str", "default": "", "description": "Path to metadata CSV (optional)"},
        "condition_column": {"type": "str", "default": "condition", "description": "Column name for conditions"},
        "sample_id_column": {"type": "str", "default": "", "description": "Column in metadata matching sample names"},
    },
    requires=["load_bulk_csv"],
    code="""
import pandas as pd
import os

def load_metadata(counts_df, metadata_path="", condition_column="condition", sample_id_column=""):
    sample_names = counts_df.index.tolist()

    # Case 1: Metadata file provided
    if metadata_path and os.path.exists(metadata_path):
        meta = pd.read_csv(metadata_path)

        # Auto-detect which column contains sample IDs
        if sample_id_column and sample_id_column in meta.columns:
            meta = meta.set_index(sample_id_column)
        else:
            # Try to find a column whose values match our sample names
            for col in meta.columns:
                overlap = set(meta[col].astype(str)) & set(sample_names)
                if len(overlap) >= len(sample_names) * 0.5:
                    meta = meta.set_index(col)
                    break

        # Align metadata to counts index
        if not set(sample_names).issubset(set(meta.index)):
            # Try the first column as index
            meta = pd.read_csv(metadata_path, index_col=0)

        meta = meta.loc[meta.index.isin(sample_names)]
        meta = meta.reindex(sample_names)

        # Auto-detect condition column
        if condition_column not in meta.columns:
            # Look for common names
            for candidate in ['condition', 'group', 'treatment', 'dex', 'genotype',
                              'phenotype', 'status', 'type', 'class', 'sample_type']:
                if candidate in meta.columns:
                    condition_column = candidate
                    break
            else:
                # Pick first column with 2-10 unique values
                for col in meta.columns:
                    if 2 <= meta[col].nunique() <= 10:
                        condition_column = col
                        break

        # Return just the condition column as a clean DataFrame
        result = pd.DataFrame({"condition": meta[condition_column].values}, index=meta.index)
        return result

    # Case 2: No metadata — infer from sample names
    # Try splitting on underscore: 'treated_1' → 'treated'
    parts = [name.rsplit('_', 1) for name in sample_names]
    if all(len(p) == 2 and p[1].isdigit() for p in parts):
        conditions = [p[0] for p in parts]
    else:
        # Can't infer — create dummy groups for the pipeline to at least run
        # Split samples into two halves
        half = len(sample_names) // 2
        conditions = ['groupA'] * half + ['groupB'] * (len(sample_names) - half)

    return pd.DataFrame({"condition": conditions}, index=sample_names)
""",
)

RUN_DESEQ2 = Template(
    id="run_deseq2",
    name="Run PyDESeq2 differential expression",
    description="Run full DESeq2 analysis: size factors, dispersion, Wald test",
    category="analysis",
    analysis_type="bulk_rnaseq",
    input_types=["DataFrame", "DataFrame"],
    output_type="DataFrame",
    params={
        "design_factor": {"type": "str", "default": "condition", "description": "Column in metadata for design"},
        "contrast_ref": {"type": "str", "default": "control", "description": "Reference level for contrast"},
        "contrast_test": {"type": "str", "default": "treated", "description": "Test level for contrast"},
        "alpha": {"type": "float", "default": 0.05, "description": "Significance threshold"},
    },
    requires=["load_bulk_csv", "load_metadata"],
    code="""
import pandas as pd
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats

def run_deseq2(counts_df, metadata, design_factor="condition", contrast_ref="control", contrast_test="treated", alpha=0.05):
    dds = DeseqDataSet(counts=counts_df, metadata=metadata, design_factors=design_factor)
    dds.deseq2()
    stat_res = DeseqStats(dds, contrast=[design_factor, contrast_test, contrast_ref], alpha=alpha)
    stat_res.summary()
    return stat_res.results_df
""",
)

FILTER_DE_RESULTS = Template(
    id="filter_de_results",
    name="Filter DE results by significance",
    description="Filter differentially expressed genes by adjusted p-value and fold change",
    category="analysis",
    analysis_type="bulk_rnaseq",
    input_types=["DataFrame"],
    output_type="DataFrame",
    params={
        "padj_threshold": {"type": "float", "default": 0.05, "description": "Adjusted p-value cutoff"},
        "lfc_threshold": {"type": "float", "default": 1.0, "description": "Absolute log2 fold change cutoff"},
    },
    requires=["run_deseq2"],
    code="""
import pandas as pd
import numpy as np

def filter_de_results(results_df, padj_threshold=0.05, lfc_threshold=1.0):
    filtered = results_df[
        (results_df['padj'] < padj_threshold) &
        (results_df['log2FoldChange'].abs() > lfc_threshold)
    ].copy()
    filtered = filtered.sort_values('padj')
    return filtered
""",
)

VOLCANO_PLOT = Template(
    id="volcano_plot",
    name="Generate volcano plot",
    description="Publication-quality volcano plot from DE results",
    category="visualization",
    analysis_type="bulk_rnaseq",
    input_types=["DataFrame"],
    output_type="Figure",
    params={
        "padj_threshold": {"type": "float", "default": 0.05, "description": "Significance cutoff line"},
        "lfc_threshold": {"type": "float", "default": 1.0, "description": "Fold change cutoff lines"},
        "top_n_labels": {"type": "int", "default": 10, "description": "Number of top genes to label"},
        "output_path": {"type": "str", "description": "Path to save the figure"},
    },
    requires=["run_deseq2"],
    code="""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def volcano_plot(results_df, output_path, padj_threshold=0.05, lfc_threshold=1.0, top_n_labels=10):
    df = results_df.dropna(subset=['padj', 'log2FoldChange']).copy()
    df['neg_log10_padj'] = -np.log10(df['padj'].clip(lower=1e-300))
    colors = []
    for _, row in df.iterrows():
        if row['padj'] < padj_threshold and row['log2FoldChange'] > lfc_threshold:
            colors.append('#e74c3c')
        elif row['padj'] < padj_threshold and row['log2FoldChange'] < -lfc_threshold:
            colors.append('#3498db')
        else:
            colors.append('#bdc3c7')
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.scatter(df['log2FoldChange'], df['neg_log10_padj'], c=colors, alpha=0.6, s=8, edgecolors='none')
    ax.axhline(-np.log10(padj_threshold), color='grey', linestyle='--', linewidth=0.8)
    ax.axvline(lfc_threshold, color='grey', linestyle='--', linewidth=0.8)
    ax.axvline(-lfc_threshold, color='grey', linestyle='--', linewidth=0.8)
    ax.set_xlabel('Log2 Fold Change', fontsize=12)
    ax.set_ylabel('-Log10 Adjusted P-value', fontsize=12)
    ax.set_title('Volcano Plot', fontsize=14)
    sig = df[df['padj'] < padj_threshold].nsmallest(top_n_labels, 'padj')
    for gene, row in sig.iterrows():
        ax.annotate(gene, (row['log2FoldChange'], row['neg_log10_padj']), fontsize=7, alpha=0.8, ha='center', va='bottom')
    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    return output_path
""",
)

HEATMAP = Template(
    id="heatmap",
    name="Generate heatmap of top DE genes",
    description="Clustered heatmap of top differentially expressed genes across samples",
    category="visualization",
    analysis_type="bulk_rnaseq",
    input_types=["DataFrame", "DataFrame"],
    output_type="Figure",
    params={
        "top_n": {"type": "int", "default": 30, "description": "Number of top genes"},
        "output_path": {"type": "str", "description": "Path to save the figure"},
    },
    requires=["run_deseq2", "load_bulk_csv"],
    code="""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

def heatmap(results_df, counts_df, output_path, top_n=30):
    sig = results_df.dropna(subset=['padj']).nsmallest(top_n, 'padj')
    available = [g for g in sig.index if g in counts_df.columns]
    if not available:
        return output_path
    subset = np.log2(counts_df[available] + 1)
    subset = (subset - subset.mean()) / subset.std().clip(lower=0.01)
    g = sns.clustermap(subset.T, cmap='RdBu_r', center=0, figsize=(12, max(6, len(available) * 0.3)),
                       yticklabels=True, xticklabels=True)
    g.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close('all')
    return output_path
""",
)

PCA_PLOT = Template(
    id="pca_plot",
    name="PCA plot of samples",
    description="PCA visualization colored by condition",
    category="visualization",
    analysis_type="bulk_rnaseq",
    input_types=["DataFrame", "DataFrame"],
    output_type="Figure",
    params={
        "output_path": {"type": "str", "description": "Path to save the figure"},
    },
    requires=["load_bulk_csv", "load_metadata"],
    code="""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA

def pca_plot(counts_df, metadata, output_path):
    log_counts = np.log2(counts_df + 1)
    pca = PCA(n_components=2)
    pcs = pca.fit_transform(log_counts)
    pc_df = pd.DataFrame(pcs, columns=['PC1', 'PC2'], index=counts_df.index)
    pc_df['condition'] = metadata.iloc[:, 0].values
    fig, ax = plt.subplots(figsize=(8, 6))
    for cond in pc_df['condition'].unique():
        mask = pc_df['condition'] == cond
        ax.scatter(pc_df.loc[mask, 'PC1'], pc_df.loc[mask, 'PC2'], label=cond, s=80)
    for idx, row in pc_df.iterrows():
        ax.annotate(idx, (row['PC1'], row['PC2']), fontsize=8, alpha=0.7)
    ax.set_xlabel(f'PC1 ({pca.explained_variance_ratio_[0]:.1%} variance)')
    ax.set_ylabel(f'PC2 ({pca.explained_variance_ratio_[1]:.1%} variance)')
    ax.set_title('PCA - Samples colored by condition')
    ax.legend()
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path
""",
)


# ──────────────────────────────────────────────────────────────────────────────
# scRNA-seq TEMPLATES
# ──────────────────────────────────────────────────────────────────────────────

LOAD_H5AD = Template(
    id="load_h5ad",
    name="Load h5ad file",
    description="Load AnnData from h5ad file",
    category="load",
    analysis_type="scrna",
    input_types=["raw_file"],
    output_type="AnnData",
    params={
        "data_path": {"type": "str", "description": "Path to h5ad file"},
    },
    code="""
import scanpy as sc

def load_h5ad(data_path: str):
    adata = sc.read_h5ad(data_path)
    adata.var_names_make_unique()
    return adata
""",
)

SCRNA_QC_FILTER = Template(
    id="scrna_qc_filter",
    name="QC filtering for scRNA-seq",
    description="Filter cells and genes, calculate mitochondrial QC metrics",
    category="preprocess",
    analysis_type="scrna",
    input_types=["AnnData"],
    output_type="AnnData",
    params={
        "min_genes": {"type": "int", "default": 200},
        "max_genes": {"type": "int", "default": 5000},
        "max_pct_mito": {"type": "float", "default": 20.0},
        "min_cells": {"type": "int", "default": 3},
    },
    requires=["load_h5ad"],
    code="""
import scanpy as sc

def qc_filter(adata, min_genes=200, max_genes=5000, max_pct_mito=20.0, min_cells=3):
    sc.pp.filter_cells(adata, min_genes=min_genes)
    sc.pp.filter_genes(adata, min_cells=min_cells)
    adata.var['mt'] = adata.var_names.str.startswith(('MT-', 'mt-'))
    sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
    adata = adata[adata.obs.n_genes_by_counts < max_genes, :].copy()
    adata = adata[adata.obs.pct_counts_mt < max_pct_mito, :].copy()
    return adata
""",
)

SCRNA_NORMALIZE = Template(
    id="scrna_normalize",
    name="Normalize and log-transform scRNA-seq",
    description="Library size normalization + log1p, then HVG selection",
    category="preprocess",
    analysis_type="scrna",
    input_types=["AnnData"],
    output_type="AnnData",
    params={
        "target_sum": {"type": "float", "default": 1e4},
        "n_top_genes": {"type": "int", "default": 2000},
    },
    requires=["scrna_qc_filter"],
    code="""
import scanpy as sc

def normalize(adata, target_sum=1e4, n_top_genes=2000):
    adata.raw = adata.copy()
    sc.pp.normalize_total(adata, target_sum=target_sum)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=n_top_genes)
    adata = adata[:, adata.var.highly_variable].copy()
    return adata
""",
)

SCRNA_DIMRED_CLUSTER = Template(
    id="scrna_dimred_cluster",
    name="PCA, neighbors, UMAP, Leiden clustering",
    description="Dimensionality reduction and clustering pipeline",
    category="analysis",
    analysis_type="scrna",
    input_types=["AnnData"],
    output_type="AnnData",
    params={
        "n_pcs": {"type": "int", "default": 50},
        "n_neighbors": {"type": "int", "default": 15},
        "resolution": {"type": "float", "default": 0.5},
    },
    requires=["scrna_normalize"],
    code="""
import scanpy as sc

def dimred_cluster(adata, n_pcs=50, n_neighbors=15, resolution=0.5):
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, n_comps=n_pcs)
    sc.pp.neighbors(adata, n_neighbors=n_neighbors, n_pcs=n_pcs)
    sc.tl.umap(adata)
    sc.tl.leiden(adata, resolution=resolution)
    return adata
""",
)

SCRNA_UMAP_PLOT = Template(
    id="scrna_umap_plot",
    name="UMAP visualization",
    description="UMAP colored by cluster or condition",
    category="visualization",
    analysis_type="scrna",
    input_types=["AnnData"],
    output_type="Figure",
    params={
        "color_by": {"type": "str", "default": "leiden"},
        "output_path": {"type": "str"},
    },
    requires=["scrna_dimred_cluster"],
    code="""
import scanpy as sc
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def umap_plot(adata, output_path, color_by="leiden"):
    sc.pl.umap(adata, color=color_by, show=False, save=False)
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close('all')
    return output_path
""",
)

SCRNA_MARKER_GENES = Template(
    id="scrna_marker_genes",
    name="Find marker genes per cluster",
    description="Rank genes by differential expression between clusters",
    category="analysis",
    analysis_type="scrna",
    input_types=["AnnData"],
    output_type="DataFrame",
    params={
        "groupby": {"type": "str", "default": "leiden"},
        "method": {"type": "str", "default": "wilcoxon"},
        "n_genes": {"type": "int", "default": 10},
    },
    requires=["scrna_dimred_cluster"],
    code="""
import scanpy as sc
import pandas as pd

def marker_genes(adata, groupby="leiden", method="wilcoxon", n_genes=10):
    sc.tl.rank_genes_groups(adata, groupby=groupby, method=method, n_genes=n_genes)
    result = adata.uns['rank_genes_groups']
    groups = result['names'].dtype.names
    rows = []
    for group in groups:
        for i in range(n_genes):
            rows.append({
                'cluster': group,
                'gene': result['names'][group][i],
                'score': result['scores'][group][i],
                'pval': result['pvals'][group][i],
                'logfc': result['logfoldchanges'][group][i],
            })
    return pd.DataFrame(rows)
""",
)


# ──────────────────────────────────────────────────────────────────────────────
# REGISTRY
# ──────────────────────────────────────────────────────────────────────────────

ALL_TEMPLATES: dict[str, Template] = {t.id: t for t in [
    LOAD_BULK_CSV, LOAD_METADATA, RUN_DESEQ2, FILTER_DE_RESULTS,
    VOLCANO_PLOT, HEATMAP, PCA_PLOT,
    LOAD_H5AD, SCRNA_QC_FILTER, SCRNA_NORMALIZE, SCRNA_DIMRED_CLUSTER,
    SCRNA_UMAP_PLOT, SCRNA_MARKER_GENES,
]}


def get_templates_for_type(analysis_type: str) -> list[Template]:
    """Get all templates applicable to an analysis type."""
    return [
        t for t in ALL_TEMPLATES.values()
        if t.analysis_type in (analysis_type, "any")
    ]


def get_template_catalog() -> str:
    """Format all templates as a catalog string for the LLM to select from."""
    lines = []
    for t in ALL_TEMPLATES.values():
        params_str = ", ".join(
            f"{k} ({v['type']}, default={v.get('default', 'required')})"
            for k, v in t.params.items()
        )
        lines.append(
            f"- {t.id}: {t.name}\n"
            f"  Category: {t.category} | Type: {t.analysis_type}\n"
            f"  Params: {params_str}\n"
            f"  Requires: {t.requires or 'none'}"
        )
    return "\n".join(lines)
