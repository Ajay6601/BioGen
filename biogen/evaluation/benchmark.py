import json
import time
from pathlib import Path

from biogen.evaluation.scorer import QueryScore, BenchmarkResults
from biogen.generation.orchestrator import run_pipeline
from biogen.config import OUTPUT_DIR
from biogen.utils.logger import get_logger

log = get_logger("biogen.benchmark")

QUERIES_PATH = Path(__file__).parent / "queries.json"


def load_queries() -> list[dict]:
    with open(QUERIES_PATH) as f:
        data = json.load(f)
    return data["queries"]


def run_benchmark(data_path: str, max_queries: int | None = None) -> BenchmarkResults:
    """Run all benchmark queries and collect scores."""
    queries = load_queries()
    if max_queries:
        queries = queries[:max_queries]

    results = BenchmarkResults()
    log.info(f"[bold]Running benchmark: {len(queries)} queries[/]")

    for i, q in enumerate(queries):
        qid = q["id"]
        query = q["query"]
        atype = q["analysis_type"]
        data_info = q.get("data_info", "count matrix CSV")

        log.info(f"\n{'='*60}")
        log.info(f"[bold]Query {i+1}/{len(queries)}: {qid}[/]")
        log.info(f"  {query[:80]}...")

        score = QueryScore(query_id=qid, query=query, analysis_type=atype)
        out_dir = str(OUTPUT_DIR / f"bench_{qid}")

        start = time.time()
        try:
            state = run_pipeline(
                query=query,
                data_path=data_path,
                output_dir=out_dir,
                data_info=data_info,
            )

            # Extract verification results
            if state.get("plan"):
                score.plan_ok = True
            v = state.get("verification")
            if v:
                score.ast_ok = v.ast_ok
                score.deps_ok = v.deps_ok
                score.params_ok = v.params_ok
                score.execution_ok = v.execution_ok

            if state.get("final_status") == "success":
                score.output_correct = True

        except Exception as e:
            score.error = str(e)
            log.error(f"  Query failed: {e}")

        elapsed = time.time() - start
        log.info(
            f"  Score: {score.score:.2f} | "
            f"Passed: {score.passed} | "
            f"Time: {elapsed:.1f}s"
        )
        results.scores.append(score)

    log.info(f"\n{'='*60}")
    log.info("[bold]Benchmark Results[/]")
    log.info(f"\n{results.summary_table()}")

    return results
