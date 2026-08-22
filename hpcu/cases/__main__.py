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
from hpcu.gateway.gateway import RetryableGateway
from hpcu.gateway.registry import create_gateway
from hpcu.grounder.grounder import Grounder
from hpcu.observation.facade import CompositeObserver
from hpcu.platform.linux.sandbox import create_sandbox_backends, probe
from hpcu.runtime_config import load_runtime_config


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
    runtime_config = load_runtime_config()
    try:
        semantic_limits = runtime_config.get("semantic", {}).get("request_limits", {})
        retry_attempts = int(semantic_limits.get("action_decision_retry_attempts", 2))
        retry_base = int(semantic_limits.get("retry_base_delay_ms", 500))
        retry_max = int(semantic_limits.get("retry_max_delay_ms", 8000))
        gateway = RetryableGateway(
            CountingGateway(create_gateway(runtime_config)),
            max_retries=retry_attempts,
            base_delay_ms=retry_base,
            max_delay_ms=retry_max,
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    capture, structure, injector = create_sandbox_backends(
        "shopping", base_url=args.base_url
    )
    observer = CompositeObserver("shopping", capture, structure)
    executor = Executor(injector)
    runner = CaseRunner(observer, executor, Grounder(), gateway, config=runtime_config)
    rows = []
    for spec in specs:
        print(f"CASE_START {spec.id}", flush=True)
        stats = await runner.run_case(spec)
        rows.append(stats)
        case_path = args.out / f"{spec.id}.json"
        evidence_frame_id = stats.evidence_frame_id
        if evidence_frame_id and evidence_frame_id in capture.store:
            frame_path = args.out / f"{spec.id}-evidence.png"
            frame_path.write_bytes(capture.store.get(evidence_frame_id))
            stats.artifact_manifest.append(str(frame_path))
        stats.artifact_manifest.append(str(case_path))
        case_path.write_text(
            json.dumps(stats.__dict__, ensure_ascii=False, default=str, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(
            f"CASE_END {spec.id} success={stats.success} "
            f"actions={stats.action_count} "
            f"model_calls={stats.model_call_count} "
            f"model_errors={stats.model_error_count} "
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
