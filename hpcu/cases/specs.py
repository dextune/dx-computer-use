"""Load shopping case specs from YAML. Data, not loop logic.

Pack-based: only goal, url, budget.  No site-specific keywords.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "cases" / "shopping-cases.yaml"


@dataclass(frozen=True)
class EvidenceSpec:
    """Minimal evidence spec -- pack-based verification.

    Site-specific fields (ocr_any, title_any, forbid_ocr) are removed.
    Evidence is now driven by the TargetingPack's ready_any/success_any.
    """
    require_pick: bool = False


@dataclass(frozen=True)
class CaseSpec:
    id: str
    goal: str
    start_url: str
    evidence: EvidenceSpec = field(default_factory=EvidenceSpec)
    max_attempts: int = 6
    max_minimax_calls: int = 2


def load_cases(path: Path | None = None) -> list[CaseSpec]:
    target = path if path is not None else _DEFAULT_PATH
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    rows = raw.get("cases") if isinstance(raw, dict) else raw
    specs: list[CaseSpec] = []
    for item in rows:
        evidence_raw: dict[str, Any] = item.get("evidence") or {}
        specs.append(
            CaseSpec(
                id=str(item["id"]),
                goal=str(item["goal"]),
                start_url=str(item["start_url"]),
                evidence=EvidenceSpec(
                    require_pick=bool(evidence_raw.get("require_pick", False)),
                ),
                max_attempts=int(item.get("max_attempts", 6)),
                max_minimax_calls=int(item.get("max_minimax_calls", 2)),
            )
        )
    return specs