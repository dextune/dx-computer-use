"""Run data-defined cases through the single command runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from hpcu.cases.planning import CasePlanCompiler, CasePlanningContextProvider
from hpcu.cases.runner import CaseRunner
from hpcu.cases.specs import load_cases
from hpcu.cases.stats import CountingGateway, dumps_stats
from hpcu.executor.executor import Executor
from hpcu.gateway.gateway import RetryableGateway
from hpcu.gateway.registry import create_gateway
from hpcu.grounder.grounder import Grounder
from hpcu.observation.facade import CompositeObserver
from hpcu.platform.linux.sandbox import create_sandbox_backends, probe
from hpcu.recovery.loop_breaker import LoopBreaker
from hpcu.runtime_config import configured_semantic_identity, load_runtime_config
from hpcu.runtime_core.control_loop import ControlLoop
from hpcu.runtime_core.product_runtime import CommandRuntime
from hpcu.schemas.capability import Capability
from hpcu.schemas.strategy import CapabilitySnapshot
from hpcu.schemas.surface import ExecutionMode, SurfaceKind
from hpcu.verifier.verifier import Verifier


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run HPCU shopping cases")
    parser.add_argument("--out", type=Path, default=Path("."))
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--base-url", default="http://127.0.0.1:1337")
    return parser.parse_args(argv)


def _stable_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _source_revision() -> str:
    for key in ("GITHUB_SHA", "SOURCE_REVISION"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _case_contract(spec: object) -> dict[str, object]:
    entry = getattr(spec, "entry", None)
    evidence = getattr(spec, "evidence", None)
    surface = getattr(spec, "surface", "")
    return {
        "id": str(getattr(spec, "id", "")),
        "goal": str(getattr(spec, "goal", "")),
        "start_url": str(getattr(spec, "start_url", "")),
        "surface": str(getattr(surface, "value", surface)),
        "entry": {
            "kind": str(getattr(getattr(entry, "kind", ""), "value", "")),
            "value": str(getattr(entry, "value", "")),
        },
        "require_pick": bool(getattr(evidence, "require_pick", False)),
        "max_attempts": int(getattr(spec, "max_attempts", 0)),
        "max_model_calls": int(getattr(spec, "max_model_calls", 0)),
    }


def _manifest_payload(
    specs: list[object],
    runtime_config: dict,
    *,
    cases_argument: Path | None,
    limit: int,
    source_revision: str | None = None,
) -> dict[str, object]:
    contracts = [_case_contract(spec) for spec in specs]
    identity = configured_semantic_identity(runtime_config)
    return {
        "schema_version": 1,
        "source_revision": (
            _source_revision() if source_revision is None else source_revision
        ),
        "entrypoint": "hpcu/cases/__main__.py",
        "python_version": sys.version.split()[0],
        "cases_argument": str(cases_argument) if cases_argument else "<default>",
        "limit": int(limit),
        "provider": identity.provider_id,
        "model": identity.model_id,
        "case_count": len(contracts),
        "case_ids": [str(item["id"]) for item in contracts],
        "case_contract_sha256": _stable_sha256(contracts),
        "runtime_config_sha256": _stable_sha256(runtime_config),
        "cases": contracts,
    }


def _write_run_manifest(
    out_dir: Path,
    specs: list[object],
    runtime_config: dict,
    *,
    cases_argument: Path | None,
    limit: int,
) -> Path:
    manifest_path = out_dir / "run-manifest.json"
    payload = _manifest_payload(
        specs,
        runtime_config,
        cases_argument=cases_argument,
        limit=limit,
    )
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _build_gateway(runtime_config: dict) -> CountingGateway:
    """Compose one logical-call counter outside transport retries."""
    semantic_limits = runtime_config.get("semantic", {}).get(
        "request_limits", {}
    )
    retry_attempts = int(semantic_limits.get("action_decision_retry_attempts", 2))
    retry_base = int(semantic_limits.get("retry_base_delay_ms", 500))
    retry_max = int(semantic_limits.get("retry_max_delay_ms", 8000))
    transport = RetryableGateway(
        create_gateway(runtime_config),
        max_retries=retry_attempts,
        base_delay_ms=retry_base,
        max_delay_ms=retry_max,
    )
    return CountingGateway(transport)


def _capability_snapshot(capture, structure, injector) -> CapabilitySnapshot:
    capture_caps = capture.capabilities()
    structure_caps = structure.capabilities()
    input_caps = injector.capabilities()
    physical = (
        Capability.SUPPORTED
        if input_caps.physical_pointer is Capability.SUPPORTED
        and input_caps.physical_keyboard is Capability.SUPPORTED
        else Capability.DEGRADED
        if input_caps.physical_pointer is not Capability.UNSUPPORTED
        or input_caps.physical_keyboard is not Capability.UNSUPPORTED
        else Capability.UNSUPPORTED
    )
    return CapabilitySnapshot(
        active_surface=SurfaceKind.BROWSER,
        execution_mode=ExecutionMode.SCREEN_STRICT,
        capture=capture_caps.pixel_grab,
        structure=structure_caps.tree,
        semantic_input=input_caps.semantic_invoke,
        physical_input=physical,
        ocr=Capability.SUPPORTED,
        dirty_rects=capture_caps.dirty_rects,
    )


async def _run(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    specs = load_cases(args.cases)
    if args.limit > 0:
        specs = specs[: args.limit]
    cases_path = args.out / "cases.txt"
    cases_path.write_text(
        "\n".join(spec.id for spec in specs) + "\n",
        encoding="utf-8",
    )
    runtime_config = load_runtime_config()
    _write_run_manifest(
        args.out,
        specs,
        runtime_config,
        cases_argument=args.cases,
        limit=args.limit,
    )
    if not probe(args.base_url):
        print("sandbox unreachable", file=sys.stderr)
        return 2
    try:
        gateway = _build_gateway(runtime_config)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    capture, structure, injector = create_sandbox_backends(
        "shopping",
        base_url=args.base_url,
    )
    observer = CompositeObserver("shopping", capture, structure)
    control_loop = ControlLoop(
        observer=observer,
        grounder=Grounder(config=runtime_config),
        executor=Executor(injector, config=runtime_config),
        verifier=Verifier(),
        loop_breaker=LoopBreaker(),
        config=runtime_config,
    )
    runtime = CommandRuntime(
        lambda: control_loop,
        provider_gateway=gateway,
        planning_context_provider=CasePlanningContextProvider(
            config=runtime_config
        ),
        plan_compiler=CasePlanCompiler(),
        config=runtime_config,
    )
    runner = CaseRunner(
        runtime,
        _capability_snapshot(capture, structure, injector),
    )

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
