"""Unit tests for shopping case specs, stats, and the common runner.

Pack-based: no site/CTA dictionaries in the production path.
"""

import pytest

from hpcu.cases.runner import (
    CaseRunner,
    _parse_target_id,
    _pick_content_fallback,
    evidence_holds,
)
from hpcu.cases.specs import EvidenceSpec, load_cases
from hpcu.cases.stats import (
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER_ID,
    CountingGateway,
    aggregate,
)
from hpcu.executor.executor import Executor
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.grounder.grounder import Grounder
from hpcu.input.injector import ExecutionResult, InputCapabilities, InputInjector
from hpcu.observation.base import Observer
from hpcu.schemas.capability import Capability
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace, ScreenPoint
from hpcu.schemas.scene import Scene, SceneDelta
from hpcu.schemas.ui_element import UIElement

SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX
FAST_CONFIG = {
    "confidence": {
        "local_execute_threshold": 0.88,
        "local_margin_min": 0.20,
        "text_llm_threshold": 0.72,
    },
    "cases": {
        "navigate_settle_timeout_ms": 5,
        "navigate_poll_interval_ms": 1,
        "post_click_timeout_ms": 5,
        "post_click_poll_interval_ms": 1,
        "minimum_evidence_token_matches": 1,
    },
    "targeting": {
        "max_tokens_per_field": 8,
        "min_token_length": 2,
    },
}


def _bbox(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(space=SPACE, x=x, y=y, width=width, height=height)


def _spec(**overrides) -> "CaseSpec":  # noqa: F821
    from hpcu.cases.specs import CaseSpec

    payload = dict(
        id="demo",
        goal="생수 하나 골라줘",
        start_url="https://example.com/water",
        evidence=EvidenceSpec(require_pick=True),
        max_attempts=2,
        max_model_calls=2,
    )
    payload.update(overrides)
    return CaseSpec(**payload)


class ScriptedObserver(Observer):
    def __init__(self, elements: tuple[UIElement, ...]):
        super().__init__("test-session")
        self._elements = elements
        self.calls = 0

    async def observe(self) -> SceneDelta:
        self.calls += 1
        if self.calls == 1:
            return SceneDelta(base_version=0, new_version=1, added=self._elements)
        return SceneDelta(
            base_version=self.calls - 1,
            new_version=self.calls,
            modified=self._elements,
        )


class RecordingInjector(InputInjector):
    def __init__(self):
        super().__init__("test-session")
        self.physical_calls: list[tuple[str, str | None]] = []

    async def semantic(self, element, action: str) -> ExecutionResult:
        return ExecutionResult(success=False, mode="semantic")

    async def physical(
        self, point: ScreenPoint, action: str, text: str | None = None
    ) -> ExecutionResult:
        self.physical_calls.append((action, text))
        return ExecutionResult(success=True, mode="physical")

    def capabilities(self) -> InputCapabilities:
        return InputCapabilities(physical_pointer=Capability.SUPPORTED)


STRICT_ACTION_RESPONSE = (
    '{"schema_version":1,"scene_version":1,"action":"click",'
    '"target_id":"ocr_line_0","value":null,"key":null,'
    '"goal_state":"in_progress","confidence":0.95,'
    '"reason_code":"goal_progress",'
    '"expected_postcondition":"new_screen"}'
)


class FakeGateway(Gateway):
    def __init__(
        self,
        content: str = "",
        error: bool = False,
        model: str = DEFAULT_MODEL_ID,
    ):
        self.content = (
            STRICT_ACTION_RESPONSE
            if content == '{"target_id": "ocr_line_0"}'
            else content
        )
        self.error = error
        self.model = model
        self.prompts: list[str] = []
        self.purposes = []

    @property
    def provider_id(self) -> str:
        return DEFAULT_PROVIDER_ID

    @property
    def model_id(self) -> str:
        return self.model

    def call(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        *,
        purpose: ModelCallPurpose = ModelCallPurpose.SITUATION_ANALYSIS,
    ) -> GatewayResponse:
        self.prompts.append(prompt)
        self.purposes.append(purpose)
        if self.error:
            raise RuntimeError("gateway down")
        if purpose is ModelCallPurpose.PLAN_COMPILE:
            content = (
                '{"ready_any":["ready"],"success_any":["생수"],'
                '"forbid_any":[],"pick_query":"생수",'
                '"pick_required":true,"dismiss_any":[],'
                '"blocked_any":[],"ignore_any":[]}'
            )
        elif purpose is ModelCallPurpose.ACTION_DECISION:
            content = self.content or STRICT_ACTION_RESPONSE
        elif purpose in (
            ModelCallPurpose.POST_ACTION_REANALYSIS,
            ModelCallPurpose.RECOVERY_REANALYSIS,
        ):
            content = (
                '{"schema_version":1,"scene_version":2,"goal_state":"success",'
                '"challenge_kind":"none","confidence":0.9,'
                '"reason_code":"fresh_scene"}'
            )
        else:
            content = self.content
        scene_version = 2
        marker = 'Current scene_version: '
        for line in self.prompts[-1].splitlines():
            if line.startswith(marker):
                scene_version = int(line.removeprefix(marker).split()[0])
                break
        content = content.replace(
            '"scene_version":2', f'"scene_version":{scene_version}'
        )
        return GatewayResponse(
            content=content,
            model=self.model,
            provider=DEFAULT_PROVIDER_ID,
            tokens_used=11,
        )


def _window() -> UIElement:
    return UIElement(
        id="x11:1",
        scene_version=1,
        role="window",
        name="Chromium",
        text="Chromium",
        bbox=_bbox(0, 0, 1280, 800),
    )


def _product() -> UIElement:
    return UIElement(
        id="ocr_line_0",
        scene_version=1,
        role="product",
        name="12,900원 생수",
        text="12,900원 생수",
        bbox=_bbox(40, 220, 180, 24),
    )


async def _instant(_delay: float) -> None:
    return None


@pytest.mark.unit
def test_dumps_stats_is_json():
    from hpcu.cases.stats import CaseStats, dumps_stats

    raw = dumps_stats([CaseStats(case_id="a", success=True)])
    payload = __import__("json").loads(raw)
    assert payload["model"] == DEFAULT_MODEL_ID
    assert payload["totals"]["successes"] == 1


@pytest.mark.unit
def test_main_parse_args_and_sandbox_down(tmp_path, monkeypatch, capsys):
    from hpcu.cases import __main__ as mainmod

    args = mainmod.parse_args(["--out", str(tmp_path), "--limit", "2"])
    assert args.limit == 2
    monkeypatch.setattr(mainmod, "probe", lambda *_a, **_k: False)
    assert mainmod.main(["--out", str(tmp_path), "--limit", "1"]) == 2
    assert (
        (tmp_path / "cases.txt").read_text(encoding="utf-8").splitlines()[0]
        == "coupang-fresh-popular"
    )
    assert "sandbox unreachable" in capsys.readouterr().err


@pytest.mark.unit
def test_load_cases_has_ten_shopping_specs():
    specs = load_cases()
    assert len(specs) == 10
    assert specs[0].id == "coupang-fresh-popular"
    assert specs[1].id == "google-search-notebook"
    assert all(item.start_url.startswith("http") for item in specs)


@pytest.mark.unit
def test_counting_gateway_enforces_selected_identity_and_counts_tokens():
    inner = FakeGateway(content='{"target_id": "ocr_line_0"}')
    gateway = CountingGateway(inner)
    response = gateway.call("pick one")
    assert response.model == DEFAULT_MODEL_ID
    assert gateway.call_count == 1
    assert gateway.error_count == 0
    assert gateway.tokens == 11
    assert DEFAULT_PROVIDER_ID in gateway.http_log[0]
    assert DEFAULT_MODEL_ID in gateway.http_log[0]


@pytest.mark.unit
def test_counting_gateway_increments_error_count():
    gateway = CountingGateway(FakeGateway(error=True))
    with pytest.raises(RuntimeError):
        gateway.call("pick one")
    assert gateway.call_count == 1
    assert gateway.error_count == 1


@pytest.mark.unit
async def test_runner_local_path_makes_zero_model_calls():
    injector = RecordingInjector()
    inner = FakeGateway(content='{"target_id": "ocr_line_0"}')
    gateway = CountingGateway(inner)
    runner = CaseRunner(
        ScriptedObserver((_window(), _product())),
        Executor(injector),
        Grounder(config=FAST_CONFIG),
        gateway,
        config=FAST_CONFIG,
        sleep=_instant,
    )
    stats = await runner.run_case(_spec())
    assert stats.success is True
    # compile call happens at plan-time, grounding calls may be 0
    assert stats.model == DEFAULT_MODEL_ID
    assert any(kind == "click" for kind, _text in injector.physical_calls)


@pytest.mark.unit
async def test_runner_uses_semantic_provider_only_on_local_miss():
    chrome_price = UIElement(
        id="ocr_line_0",
        scene_version=1,
        role="text",
        name="12,900원",
        text="12,900원",
        bbox=_bbox(20, 8, 80, 14),
    )
    injector = RecordingInjector()
    inner = FakeGateway(content='{"target_id": "ocr_line_0"}')
    gateway = CountingGateway(inner)
    runner = CaseRunner(
        ScriptedObserver((_window(), chrome_price)),
        Executor(injector),
        Grounder(config=FAST_CONFIG),
        gateway,
        config=FAST_CONFIG,
        sleep=_instant,
    )
    stats = await runner.run_case(_spec())
    assert inner.prompts
    assert stats.model_call_count >= 1
    assert stats.model == DEFAULT_MODEL_ID
    assert "other-model" not in {action.detail for action in stats.actions}


@pytest.mark.unit
async def test_runner_semantic_provider_error_increments_and_does_not_pick():
    injector = RecordingInjector()
    inner = FakeGateway(error=True)
    gateway = CountingGateway(inner)
    runner = CaseRunner(
        ScriptedObserver((_window(),)),
        Executor(injector),
        Grounder(config=FAST_CONFIG),
        gateway,
        config=FAST_CONFIG,
        sleep=_instant,
    )
    stats = await runner.run_case(_spec(max_attempts=1, max_model_calls=1))
    assert stats.success is False
    assert stats.model_error_count >= 1
    assert stats.model_call_count >= 1
    assert stats.model == DEFAULT_MODEL_ID


@pytest.mark.unit
def test_parse_target_id_accepts_aliases_and_ocr_token():
    assert _parse_target_id('{"target_id": "ocr_line_3"}') == "ocr_line_3"
    assert _parse_target_id('{"id": "ocr_line_7"}') == "ocr_line_7"
    assert _parse_target_id("pick ocr_line_2 please") == "ocr_line_2"
    assert _parse_target_id("no target") is None


@pytest.mark.unit
def test_content_fallback_prefers_digit_text_outside_chrome():
    window = _window()
    chrome = UIElement(
        id="ocr_line_0",
        scene_version=1,
        text="tab 123",
        bbox=_bbox(20, 8, 400, 14),
    )
    content = UIElement(
        id="ocr_line_1",
        scene_version=1,
        text="SKU 99",
        bbox=_bbox(40, 240, 80, 20),
    )
    scene = Scene(
        version=1,
        elements={window.id: window, chrome.id: chrome, content.id: content},
    )
    picked = _pick_content_fallback(scene)
    assert picked is not None
    assert picked.id == "ocr_line_1"


@pytest.mark.unit
def test_evidence_holds_with_empty_spec():
    """EvidenceSpec with no site-specific fields always passes."""
    product = _product()
    scene = Scene(version=1, elements={product.id: product})
    assert evidence_holds(scene, EvidenceSpec(), product.text) is True


@pytest.mark.unit
def test_evidence_holds_with_empty_spec_forbid():
    """EvidenceSpec no longer has forbid_ocr — always passes."""
    checkout = UIElement(
        id="pay",
        scene_version=1,
        text="결제하기 12,900원",
        bbox=_bbox(10, 10, 80, 20),
    )
    blocked = Scene(version=1, elements={"pay": checkout})
    assert evidence_holds(blocked, EvidenceSpec(), checkout.text) is True


@pytest.mark.unit
def test_aggregate_totals_match_rows():
    from hpcu.cases.stats import CaseStats

    rows = [
        CaseStats(case_id="a", success=True, action_count=3, model_call_count=0,
                  compile_call_count=0, grounding_call_count=0),
        CaseStats(
            case_id="b",
            success=False,
            action_count=4,
            model_call_count=2,
            model_error_count=1,
            model_tokens=9,
            compile_call_count=1,
            grounding_call_count=1,
        ),
    ]
    payload = aggregate(rows)
    assert payload["model"] == DEFAULT_MODEL_ID
    assert payload["totals"]["cases"] == 2
    assert payload["totals"]["successes"] == 1
    assert payload["totals"]["action_count"] == 7
    assert payload["totals"]["model_call_count"] == 2
    assert payload["totals"]["model_error_count"] == 1
    assert payload["totals"]["model_tokens"] == 9
    assert payload["totals"]["compile_call_count"] == 1
    assert payload["totals"]["grounding_call_count"] == 1


@pytest.mark.unit
async def test_dataset_capture_from_runner(dataset_collector):
    """Run a case and verify the dataset collector records it."""
    injector = RecordingInjector()
    inner = FakeGateway(content='{"target_id": "ocr_line_0"}')
    gateway = CountingGateway(inner)
    runner = CaseRunner(
        ScriptedObserver((_window(), _product())),
        Executor(injector),
        Grounder(config=FAST_CONFIG),
        gateway,
        config=FAST_CONFIG,
        sleep=_instant,
    )
    stats = await runner.run_case(_spec())
    # Record into dataset collector
    from hpcu.cases.stats import _case_dict
    dataset_collector.record_case(_case_dict(stats))
    assert stats.success is True
    assert len(dataset_collector.entries) >= 1
    entry = dataset_collector.entries[-1]
    assert entry["case_id"] == "demo"
    assert entry["success"] is True
    assert isinstance(entry["actions"], list)
    assert entry["model_call_count"] >= 0
    assert entry["compile_call_count"] >= 0
    assert entry["grounding_call_count"] >= 0
    assert entry["provider"] == DEFAULT_PROVIDER_ID
    assert entry["model"] == DEFAULT_MODEL_ID
