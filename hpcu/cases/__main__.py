"""Run shopping cases: python -m hpcu.cases --out DIR."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from hpcu.cases.runner import CaseRunner
from hpcu.cases.specs import load_cases
from hpcu.cases.stats import CountingGateway, dumps_stats
from hpcu.executor.executor import Executor
from hpcu.gateway.minimax_adapter import MiniMaxAdapter, load_minimax_api_key
from hpcu.grounder.grounder import Grounder
from hpcu.observation.facade import CompositeObserver
from hpcu.platform.linux.sandbox import create_sandbox_backends, probe


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run HPCU shopping cases")
    parser.add_argument("--out", type=Path, default=Path("."))
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--base-url", default="http://127.0.0.1:1337")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    specs = load_cases(args.cases)
    if args.limit > 0:
        specs = specs[: args.limit]
    cases_path = args.out / "cases.txt"
    cases_path.write_text("\n".join(spec.id for spec in specs) + "\n", encoding="utf-8")
    if not probe(args.base_url):
        print("sandbox unreachable", file=sys.stderr)
        return 2
    if not load_minimax_api_key():
        print("MINIMAX_API_KEY is not set", file=sys.stderr)
        return 2
    capture, structure, injector = create_sandbox_backends(
        "shopping", base_url=args.base_url
    )
    observer = CompositeObserver("shopping", capture, structure)
    executor = Executor(injector)
    gateway = CountingGateway(MiniMaxAdapter.from_env())
    runner = CaseRunner(observer, executor, Grounder(), gateway)
    rows = []
    for spec in specs:
        print(f"CASE_START {spec.id}", flush=True)
        stats = await runner.run_case(spec)
        rows.append(stats)
        print(
            f"CASE_END {spec.id} success={stats.success} "
            f"actions={stats.action_count} "
            f"minimax={stats.minimax_call_count} "
            f"errors={stats.minimax_error_count} "
            f"failure={stats.failure}",
            flush=True,
        )
    payload = dumps_stats(rows)
    (args.out / "stats.json").write_text(payload + "\n", encoding="utf-8")
    print(payload, flush=True)
    totals = json.loads(payload)["totals"]
    return 0 if totals["successes"] == totals["cases"] else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
