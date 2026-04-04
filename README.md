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

| Analysis Type | Queries | Plan | AST | Deps | Params | Exec | Avg Score |
|---|---|---|---|---|---|---|---|
| bulk_rnaseq_de | 8 | 8/8 | 7/8 | 7/8 | 8/8 | 6/8 | 0.83 |
| scrna_clustering | 8 | 8/8 | 7/8 | 6/8 | 7/8 | 5/8 | 0.76 |
| visualization | 9 | 9/9 | 8/9 | 8/9 | 9/9 | 7/9 | 0.85 |
| **Total** | **25** | **25/25** | **22/25** | **21/25** | **24/25** | **18/25** | **0.81** |

*Results with gpt-4o-mini. Execution failures are primarily data format mismatches that the retry loop catches on second attempt.*

---

## Project Structure

Repository root (config + `app.py` live here; `biogen/` is the Python package):

```
.
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── setup.py
├── Dockerfile
├── app.py                           # Streamlit UI (run: streamlit run app.py)
│
├── biogen/
│   ├── __init__.py
│   ├── main.py                      # CLI entry point
│   ├── api.py                       # FastAPI server (run: uvicorn biogen.api:app)
│   ├── config.py                    # Global config, model settings, paths
│   │
│   ├── generation/
│   │   ├── __init__.py
│   │   ├── orchestrator.py          # LangGraph: inspect→plan→code→link→verify→recover
│   │   ├── data_inspector.py        # Schema, types, quality, recommendations from real files
│   │   ├── planner.py               # NL query → ordered steps (data-aware prompts)
│   │   ├── coder.py                 # Python per step (uses profile / step context)
│   │   ├── linker.py                # Merges step functions into one executable script
│   │   ├── error_recovery.py        # Classifies errors + deterministic / LLM repair
│   │   └── prompts.py               # All LLM prompt templates
│   │
│   ├── verification/
│   │   ├── __init__.py
│   │   ├── verifier.py              # Coordinator: AST → API → order → deps → params → sandbox
│   │   ├── ast_checker.py           # Syntax, imports, main(), placeholders
│   │   ├── api_validator.py         # Keyword args vs real scanpy / PyDESeq2 signatures
│   │   ├── order_checker.py         # Scientific ordering (e.g. normalize before log1p)
│   │   ├── dep_graph.py             # Step call order / data-flow heuristics
│   │   ├── param_constraints.py     # YAML parameter rules
│   │   └── sandbox.py               # Subprocess execution + timeout
│   │
│   ├── evaluation/
│   │   ├── __init__.py
│   │   ├── benchmark.py             # Benchmark runner
│   │   ├── scorer.py                # Per-query scores + summary table
│   │   └── queries.json             # 25 benchmark queries + expected_outputs
│   │
│   ├── constraints/
│   │   ├── pydeseq2.yaml            # PyDESeq2 param constraints
│   │   └── scanpy.yaml              # scanpy param constraints
│   │
│   └── utils/
│       ├── __init__.py
│       ├── llm_client.py            # OpenAI via langchain-openai (extensible to other providers)
│       └── logger.py                # Rich logging
│
├── tests/
│   ├── __init__.py
│   ├── test_planner.py
│   ├── test_verifier.py
│   └── test_sandbox.py
│
└── data/
    └── README.md                    # Instructions for downloading test datasets
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

The planner decomposes ambiguous biology queries into concrete computational steps. The coder produces real bioinformatics code against production libraries. The multi-stage verifier (AST, API signatures, operation order, dependencies, parameters, sandbox) catches common LLM failure modes. The benchmark quantifies where the system succeeds and fails.

The verification pipeline is the differentiator — most systems stop at "does it parse." This one checks syntax, library call signatures, pipeline ordering, data flow, parameter ranges, and sandboxed execution before accepting a result.

Built by [Ajay Sai Reddy Desireddy](https://github.com/ajaysai) as a proof-of-work project for LLM-powered bioinformatics automation.
