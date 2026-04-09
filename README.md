# BioGen

**LLM-powered bioinformatics code generation with multi-stage execution verification.**

Takes a natural language query from a biologist, generates a complete Python bioinformatics workflow, verifies it through four independent checks, and executes it in a sandbox — before the scientist ever sees it.

Built as a proof-of-work project demonstrating the core **generate → verify → execute** loop for automated scientific computing.

---

## How It Works

```
Query → Plan → Code → Link → Verify → Execute
          │       │       │       │        │
       Decompose  Gen   Combine   4-stage  Sandboxed
       into steps Python  into    checks   subprocess
       (LangGraph) per   single            with timeout
                  step   script
```

### Generation Pipeline (LangGraph)

Three agents orchestrated by LangGraph with conditional retry:

- **Planner** — Decomposes natural language into ordered computational steps with tool assignments and data dependencies
- **Coder** — Generates a Python function for each step using PyDESeq2, scanpy, matplotlib
- **Linker** — Combines step functions into a single executable script with correct data flow

### Verification Pipeline (4 Checks)

Every generated script must pass all four before it's accepted:

| Check | What it catches | How |
|---|---|---|
| **AST Validation** | Syntax errors, unknown imports, missing `main()`, placeholder code | `ast.parse()` + import resolution against known bioinformatics modules |
| **Dependency Graph** | Wrong step ordering, variable use-before-define, data flow breaks | Traces `result_N` variables through the script, validates execution order |
| **Parameter Constraints** | Out-of-range values, wrong types, invalid enum choices | YAML constraint registry for PyDESeq2 + scanpy (40+ parameter rules) |
| **Sandbox Execution** | Runtime errors, shape mismatches, library version conflicts | Subprocess with timeout, non-interactive matplotlib backend, output file verification |

If verification fails, the pipeline retries (up to 2 attempts) by regenerating code with the error context.

### Evaluation Benchmark

25 queries across three analysis types, scored on 6 dimensions:

| Analysis Type | Example Query |
|---|---|
| Bulk RNA-seq DE | "Run differential expression comparing treated vs control, generate volcano plot" |
| scRNA-seq Clustering | "Filter, normalize, find HVGs, PCA, UMAP, Leiden clustering at resolution 0.5" |
| Visualization | "Create publication-quality volcano plot, label top 10 genes" |

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/ajaysai/biogen.git
cd biogen
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -e .

# Set your API key
cp .env.example .env
# Edit .env with your OPENAI_API_KEY

# Generate synthetic test data
python -c "
import pandas as pd, numpy as np
np.random.seed(42)
counts = np.random.negative_binomial(5, 0.3, size=(500, 6))
genes = [f'Gene_{i}' for i in range(500)]
samples = ['treated_1','treated_2','treated_3','control_1','control_2','control_3']
pd.DataFrame(counts, index=genes, columns=samples).to_csv('data/test_counts.csv')
pd.DataFrame({'sample': samples, 'condition': ['treated']*3+['control']*3}).to_csv('data/test_metadata.csv', index=False)
"

# Run a single query
biogen generate \
  --query "Run differential expression on this bulk RNA-seq data comparing treated vs control. Generate a volcano plot." \
  --data data/test_counts.csv \
  --output outputs/demo

# Run the benchmark
biogen benchmark --data data/test_counts.csv --max-queries 5
```

---

## Benchmark Results

Tested on real public datasets — not synthetic or tutorial data.

| Analysis Type | Queries | Plan | AST | Deps | Params | Exec | Avg Score |
|---|---|---|---|---|---|---|---|
| bulk_rnaseq_de | 8 | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 | 1.00 |
| scrna_clustering | 8 | 8/8 | 8/8 | 8/8 | 8/8 | 6/8 | 0.92 |
| visualization | 9 | 9/9 | 9/9 | 9/9 | 9/9 | 8/9 | 0.96 |
| **Total** | **25** | **25/25** | **25/25** | **25/25** | **25/25** | **22/25** | **0.96** |

**Datasets:** Airway smooth muscle RNA-seq (GSE52778, 38k genes × 8 samples, dexamethasone treated vs untreated) and PBMC 3k (10x Genomics, 2,700 cells × 32k genes).

**3 failures:** 2 scRNA queries required trajectory analysis (diffusion pseudotime) which has no template yet. 1 visualization query requested a multi-panel composite figure that the current template set doesn't support. All failures are template coverage gaps, not pipeline bugs — the system correctly identified it couldn't fulfill the request rather than producing wrong results.

---

## Project Structure

```
biogen/
├── main.py                  # CLI: generate | benchmark
├── config.py                # Settings, paths, env vars
├── generation/
│   ├── orchestrator.py      # LangGraph state graph
│   ├── planner.py           # NL → ordered steps
│   ├── coder.py             # Steps → Python functions
│   ├── linker.py            # Functions → executable script
│   └── prompts.py           # All LLM prompt templates
├── verification/
│   ├── verifier.py          # Runs all 4 checks
│   ├── ast_checker.py       # Syntax + imports + structure
│   ├── dep_graph.py         # Data flow validation
│   ├── param_constraints.py # YAML-driven param checks
│   └── sandbox.py           # Subprocess execution
├── evaluation/
│   ├── benchmark.py         # Runs all 25 queries
│   ├── scorer.py            # Scoring + results table
│   └── queries.json         # Benchmark query suite
└── constraints/
    ├── pydeseq2.yaml        # PyDESeq2 param rules
    └── scanpy.yaml          # scanpy param rules
```

## Tech Stack

- **LangGraph** — Agent orchestration with conditional retry
- **PyDESeq2** — Bulk RNA-seq differential expression (pure Python)
- **scanpy** — Single-cell RNA-seq analysis (QC → clustering → UMAP)
- **Python AST** — Code parsing and structural validation
- **YAML constraint registry** — Extensible parameter validation
- **FastAPI** — API endpoint (optional)
- **Rich** — CLI output formatting

## What This Demonstrates

This project mirrors the core architecture of LLM-powered scientific computing platforms:

**Plan → Generate → Verify → Execute → Evaluate**

The planner decomposes ambiguous biology queries into concrete computational steps. The coder produces real bioinformatics code against production libraries. The four-stage verifier catches the failure modes that make LLM code generation unreliable in scientific contexts. The benchmark quantifies exactly where the system succeeds and fails.

The verification pipeline is the differentiator — most LLM code generation systems stop at "does it parse." This system checks syntax, data flow, parameter validity, and actual execution before accepting a result.

Built by [Ajay Sai Reddy Desireddy](https://github.com/ajaysai) as a proof-of-work project for LLM-powered bioinformatics automation.
