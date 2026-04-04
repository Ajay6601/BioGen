# Test Data for BioGen

## Bulk RNA-seq test data

Download the TCGA BRCA example dataset:

```bash
# Option 1: Use the included synthetic data generator
python -m biogen.utils.generate_test_data

# Option 2: Download from GEO
# GEO accession: GSE183947 (small breast cancer RNA-seq dataset)
# wget https://ftp.ncbi.nlm.nih.gov/geo/series/GSE183nnn/GSE183947/suppl/GSE183947_fpkm.csv.gz
```

## Single-cell RNA-seq test data

```bash
# PBMC 3k from 10x Genomics (standard scanpy tutorial dataset)
python -c "import scanpy as sc; adata = sc.datasets.pbmc3k(); adata.write('data/pbmc3k.h5ad')"
```

## Synthetic test data

For CI/testing without downloads, BioGen can generate synthetic data:

```bash
python -c "
import pandas as pd
import numpy as np

np.random.seed(42)
n_genes, n_samples = 500, 6
counts = np.random.negative_binomial(5, 0.3, size=(n_genes, n_samples))
genes = [f'Gene_{i}' for i in range(n_genes)]
samples = ['treated_1','treated_2','treated_3','control_1','control_2','control_3']
pd.DataFrame(counts, index=genes, columns=samples).to_csv('data/test_counts.csv')
pd.DataFrame({'sample': samples, 'condition': ['treated']*3 + ['control']*3}).to_csv('data/test_metadata.csv', index=False)
print('Created data/test_counts.csv and data/test_metadata.csv')
"
```
