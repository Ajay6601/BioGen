from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QueryScore:
    query_id: str
    query: str
    analysis_type: str
    plan_ok: bool = False
    ast_ok: bool = False
    api_ok: bool = False
    order_ok: bool = False
    deps_ok: bool = False
    params_ok: bool = False
    execution_ok: bool = False
    output_correct: bool = False
    error: str = ""

    @property
    def passed(self) -> bool:
        return all([
            self.plan_ok,
            self.ast_ok,
            self.api_ok,
            self.order_ok,
            self.deps_ok,
            self.params_ok,
            self.execution_ok,
        ])

    @property
    def score(self) -> float:
        """Score from 0-1 based on how many checks passed."""
        checks = [
            self.plan_ok,
            self.ast_ok,
            self.api_ok,
            self.order_ok,
            self.deps_ok,
            self.params_ok,
            self.execution_ok,
            self.output_correct,
        ]
        return sum(checks) / len(checks)


@dataclass
class BenchmarkResults:
    scores: list[QueryScore] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.scores)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.scores if s.passed)

    @property
    def execution_pass_rate(self) -> float:
        if not self.scores:
            return 0.0
        return sum(1 for s in self.scores if s.execution_ok) / len(self.scores)

    @property
    def avg_score(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.score for s in self.scores) / len(self.scores)

    def by_type(self) -> dict[str, list[QueryScore]]:
        groups: dict[str, list[QueryScore]] = {}
        for s in self.scores:
            groups.setdefault(s.analysis_type, []).append(s)
        return groups

    def summary_table(self) -> str:
        """Generate a markdown results table."""
        lines = [
            "| Analysis Type | Queries | Plan | AST | API | Order | Deps | Params | Exec | Avg Score |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]

        for atype, group in self.by_type().items():
            n = len(group)
            plan = sum(1 for s in group if s.plan_ok)
            ast_ = sum(1 for s in group if s.ast_ok)
            api_ = sum(1 for s in group if s.api_ok)
            order_ = sum(1 for s in group if s.order_ok)
            deps = sum(1 for s in group if s.deps_ok)
            params = sum(1 for s in group if s.params_ok)
            exec_ = sum(1 for s in group if s.execution_ok)
            avg = sum(s.score for s in group) / n if n else 0

            lines.append(
                f"| {atype} | {n} | {plan}/{n} | {ast_}/{n} | {api_}/{n} | {order_}/{n} | "
                f"{deps}/{n} | {params}/{n} | {exec_}/{n} | {avg:.2f} |"
            )

        n = self.total
        if n:
            lines.append(
                f"| **Total** | **{n}** | "
                f"**{sum(1 for s in self.scores if s.plan_ok)}/{n}** | "
                f"**{sum(1 for s in self.scores if s.ast_ok)}/{n}** | "
                f"**{sum(1 for s in self.scores if s.api_ok)}/{n}** | "
                f"**{sum(1 for s in self.scores if s.order_ok)}/{n}** | "
                f"**{sum(1 for s in self.scores if s.deps_ok)}/{n}** | "
                f"**{sum(1 for s in self.scores if s.params_ok)}/{n}** | "
                f"**{sum(1 for s in self.scores if s.execution_ok)}/{n}** | "
                f"**{self.avg_score:.2f}** |"
            )

        return "\n".join(lines)
