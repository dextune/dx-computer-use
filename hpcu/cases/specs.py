"""Load shopping case specs from YAML. Data, not loop logic.

Pack-based: only goal, url, budget.  No site-specific keywords.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hpcu.cases.screen_contract import (
    MAX_CASE_ATTEMPTS,
    EntryContract,
    EntryKind,
    ScreenCaseSpec,
    SurfaceKind,
)

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
    start_url: str = ""
    surface: SurfaceKind = SurfaceKind.BROWSER
    entry: EntryContract | None = None
    evidence: EvidenceSpec = field(default_factory=EvidenceSpec)
    max_attempts: int = MAX_CASE_ATTEMPTS
    max_model_calls: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= MAX_CASE_ATTEMPTS:
            raise ValueError(
                f"case max_attempts must be between 1 and {MAX_CASE_ATTEMPTS}"
            )
        if self.max_model_calls < 0:
            raise ValueError("case model-call budget must be non-negative")
        if self.entry is None:
            if self.surface is not SurfaceKind.BROWSER:
                object.__setattr__(
                    self,
                    "entry",
                    EntryContract(EntryKind.EXISTING_SCREEN),
                )
                return
            if not self.start_url:
                raise ValueError("browser CaseSpec requires start_url")
            object.__setattr__(
                self,
                "entry",
                EntryContract(EntryKind.URL, self.start_url),
            )
        if self.surface is SurfaceKind.BROWSER and self.entry.kind is EntryKind.URL:
            if not self.start_url:
                object.__setattr__(self, "start_url", self.entry.value)

    @property
    def screen_case(self) -> ScreenCaseSpec:
        return ScreenCaseSpec(
            id=self.id,
            surface=self.surface,
            goal=self.goal,
            entry=self.entry or EntryContract(EntryKind.EXISTING_SCREEN),
            max_attempts=self.max_attempts,
            max_model_calls=self.max_model_calls,
        )


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
                start_url=str(item.get("start_url", "")),
                surface=SurfaceKind(
                    str(item.get("surface", SurfaceKind.BROWSER.value))
                ),
                entry=(
                    EntryContract(
                        EntryKind(
                            str(
                                (item.get("entry") or {}).get(
                                    "kind", EntryKind.URL.value
                                )
                            )
                        ),
                        str(
                            (item.get("entry") or {}).get(
                                "value", item.get("start_url", "")
                            )
                        ),
                    )
                    if item.get("entry")
                    else None
                ),
                evidence=EvidenceSpec(
                    require_pick=bool(evidence_raw.get("require_pick", False)),
                ),
                max_attempts=int(item.get("max_attempts", MAX_CASE_ATTEMPTS)),
                max_model_calls=int(item.get("max_model_calls", 2)),
            )
        )
    return specs
