###############################################################################
# FILE: biogen/generation/api_reference.py
###############################################################################
"""
Correct API usage snippets injected into coder prompts.
Prevents hallucination at generation time instead of catching it after.
"""

PYDESEQ2_REFERENCE = """
=== PyDESeq2 CORRECT API (DO NOT use R-style DESeq2 syntax) ===

# Loading and creating dataset:
import pandas as pd
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats

counts_df = pd.read_csv(data_path, index_col=0)
# counts_df: genes as rows, samples as columns (integers)

metadata = pd.DataFrame({
    'condition': ['treated', 'treated', 'treated', 'control', 'control', 'control']
}, index=['treated_1', 'treated_2', 'treated_3', 'control_1', 'control_2', 'control_3'])

# CORRECT constructor — use 'counts' and 'metadata', NOT 'countData'/'colData':
dds = DeseqDataSet(
    counts=counts_df,
    metadata=metadata,
    design_factors="condition",   # NOT design_formula, NOT formula
)

# Run analysis:
dds.deseq2()

# Get results:
stat_res = DeseqStats(dds, contrast=["condition", "treated", "control"], alpha=0.05)
stat_res.summary()
results_df = stat_res.results_df  # DataFrame with baseMean, log2FoldChange, padj, etc.

# NEVER USE: colData, countData, design_formula, resultsNames, lfcShrink
# These are R/DESeq2 functions that DO NOT exist in PyDESeq2.
"""

SCANPY_REFERENCE = """
=== scanpy CORRECT API and CORRECT ORDER ===

import scanpy as sc
import anndata as ad

# Load:
adata = sc.read_h5ad(data_path)
# OR from CSV: adata = ad.AnnData(pd.read_csv(path, index_col=0).T)

# CORRECT ORDER — do NOT rearrange:
# 1. Filter cells
sc.pp.filter_cells(adata, min_genes=200)
# 2. Filter genes
sc.pp.filter_genes(adata, min_cells=3)
# 3. Calculate QC metrics (for mito filtering)
adata.var['mt'] = adata.var_names.str.startswith(('MT-', 'mt-'))
sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, log1p=False, inplace=True)
adata = adata[adata.obs.pct_counts_mt < 20, :].copy()
# 4. Normalize
sc.pp.normalize_total(adata, target_sum=1e4)
# 5. Log transform (ONLY if data is raw counts, NEVER on already-logged data)
sc.pp.log1p(adata)
# 6. HVG
sc.pp.highly_variable_genes(adata, n_top_genes=2000)
adata = adata[:, adata.var.highly_variable].copy()
# 7. Scale (optional)
sc.pp.scale(adata, max_value=10)
# 8. PCA
sc.pp.pca(adata, n_comps=50)
# 9. Neighbors
sc.pp.neighbors(adata, n_neighbors=15, n_pcs=40)
# 10. UMAP
sc.tl.umap(adata)
# 11. Cluster
sc.tl.leiden(adata, resolution=0.5)

# Marker genes:
sc.tl.rank_genes_groups(adata, 'leiden', method='wilcoxon')

# Plotting — always save, never show:
sc.pl.umap(adata, color=['leiden'], save='_clusters.png', show=False)
"""

VISUALIZATION_REFERENCE = """
=== Matplotlib/Seaborn for bioinformatics plots ===

import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

# Volcano plot:
fig, ax = plt.subplots(figsize=(10, 8))
results_df['-log10_padj'] = -np.log10(results_df['padj'].clip(lower=1e-300))
sig = (results_df['padj'] < 0.05) & (results_df['log2FoldChange'].abs() > 1)
ax.scatter(results_df.loc[~sig, 'log2FoldChange'], results_df.loc[~sig, '-log10_padj'],
           c='grey', alpha=0.5, s=10)
ax.scatter(results_df.loc[sig, 'log2FoldChange'], results_df.loc[sig, '-log10_padj'],
           c='red', alpha=0.7, s=10)
ax.set_xlabel('log2 Fold Change')
ax.set_ylabel('-log10(adjusted p-value)')
ax.axhline(-np.log10(0.05), ls='--', c='black', alpha=0.3)
ax.axvline(-1, ls='--', c='black', alpha=0.3)
ax.axvline(1, ls='--', c='black', alpha=0.3)
plt.tight_layout()
plt.savefig(f'{output_dir}/volcano_plot.png', dpi=150)
plt.close()

# ALWAYS use: plt.savefig() then plt.close()
# NEVER use: plt.show()
"""


def get_reference_for_tool(tool: str) -> str:
    """Return the correct API reference for a given tool."""
    tool_lower = tool.lower()
    if "pydeseq2" in tool_lower or "deseq" in tool_lower:
        return PYDESEQ2_REFERENCE
    elif "scanpy" in tool_lower or "sc." in tool_lower:
        return SCANPY_REFERENCE
    elif "matplotlib" in tool_lower or "seaborn" in tool_lower or "plot" in tool_lower:
        return VISUALIZATION_REFERENCE
    return ""


def get_reference_for_plan(analysis_type: str) -> str:
    """Return all relevant references for an analysis type."""
    if analysis_type == "bulk_rnaseq_de":
        return PYDESEQ2_REFERENCE + VISUALIZATION_REFERENCE
    elif analysis_type == "scrna_clustering":
        return SCANPY_REFERENCE + VISUALIZATION_REFERENCE
    elif analysis_type == "visualization":
        return VISUALIZATION_REFERENCE
    return PYDESEQ2_REFERENCE + SCANPY_REFERENCE + VISUALIZATION_REFERENCE
