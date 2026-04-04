"""
BioGen CLI — LLM bioinformatics code generation with execution verification.

Usage:
    biogen generate --query "..." --data path/to/data.csv --output outputs/
    biogen benchmark --data path/to/data.csv [--max-queries 5]
"""
import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from biogen.config import OUTPUT_DIR
from biogen.utils.logger import get_logger

log = get_logger("biogen")
console = Console()


def cmd_generate(args):
    """Run the generation + verification pipeline on a single query."""
    from biogen.generation.orchestrator import run_pipeline

    console.print(Panel(
        f"[bold cyan]BioGen[/] — generating workflow\n\n"
        f"Query: {args.query}\n"
        f"Data:  {args.data}\n"
        f"Output: {args.output}",
        title="BioGen",
    ))

    state = run_pipeline(
        query=args.query,
        data_path=args.data,
        output_dir=args.output,
        data_info=args.data_info or "count matrix CSV",
        metadata_path=args.metadata or "",
    )

    # Print results
    status = state.get("final_status", "unknown")
    if status == "success":
        console.print("\n[bold green]✓ Workflow generated and verified successfully![/]")

        # Save the script
        out_path = Path(args.output) / "workflow.py"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(state["script"])
        console.print(f"  Script saved to: {out_path}")
    else:
        console.print(f"\n[bold red]✗ Workflow generation failed (status: {status})[/]")
        v = state.get("verification")
        if v and v.issues:
            for issue in v.issues:
                console.print(f"  [red]• {issue}[/]")

        # Save the script anyway for debugging
        if state.get("script"):
            debug_path = Path(args.output) / "workflow_debug.py"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(state["script"])
            console.print(f"  Debug script saved to: {debug_path}")

    return 0 if status == "success" else 1


def cmd_benchmark(args):
    """Run the benchmark evaluation suite."""
    from biogen.evaluation.benchmark import run_benchmark

    console.print(Panel(
        f"[bold cyan]BioGen Benchmark[/]\n\n"
        f"Data: {args.data}\n"
        f"Max queries: {args.max_queries or 'all'}",
        title="BioGen Benchmark",
    ))

    results = run_benchmark(
        data_path=args.data,
        max_queries=args.max_queries,
    )

    # Rich table output
    table = Table(title="Benchmark Results")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total queries", str(results.total))
    table.add_row("Passed (all checks)", str(results.passed))
    table.add_row("Execution pass rate", f"{results.execution_pass_rate:.1%}")
    table.add_row("Average score", f"{results.avg_score:.2f}")

    console.print(table)
    console.print("\n[bold]Per-type breakdown:[/]")
    console.print(results.summary_table())

    # Save results
    results_path = Path(args.output) / "benchmark_results.md"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        f"# BioGen Benchmark Results\n\n{results.summary_table()}\n"
    )
    console.print(f"\nResults saved to: {results_path}")

    return 0


def cli():
    parser = argparse.ArgumentParser(
        prog="biogen",
        description="BioGen — LLM bioinformatics code generation with verification",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # generate
    gen = sub.add_parser("generate", help="Generate a workflow from natural language")
    gen.add_argument("--query", "-q", required=True, help="Natural language query")
    gen.add_argument("--data", "-d", required=True, help="Path to input data")
    gen.add_argument("--output", "-o", default=str(OUTPUT_DIR), help="Output directory")
    gen.add_argument("--data-info", help="Description of the data format")
    gen.add_argument(
        "--metadata",
        "-m",
        default="",
        help="Optional metadata CSV path (e.g. sample → condition)",
    )

    # benchmark
    bench = sub.add_parser("benchmark", help="Run the evaluation benchmark")
    bench.add_argument("--data", "-d", required=True, help="Path to test data")
    bench.add_argument("--max-queries", "-n", type=int, help="Max queries to run")
    bench.add_argument("--output", "-o", default=str(OUTPUT_DIR), help="Output directory")

    args = parser.parse_args()

    if args.command == "generate":
        sys.exit(cmd_generate(args))
    elif args.command == "benchmark":
        sys.exit(cmd_benchmark(args))


if __name__ == "__main__":
    cli()
