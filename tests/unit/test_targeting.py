"""Unit tests for TargetingPack, GenericMatcher, and GoalFallbackTokenizer.

All T1 components — model 0 call, pure data, pure string processing.
"""

import pytest

from hpcu.cases.stats import DEFAULT_PROVIDER_ID, CountingGateway
from hpcu.compiler.targeting_compiler import (
    GoalFallbackTokenizer,
    TargetingCompilationError,
    TargetingCompiler,
)
from hpcu.gateway.gateway import Gateway, GatewayResponse, ModelCallPurpose
from hpcu.perception.matcher import GenericMatcher, mentions
from hpcu.schemas.coordinates import BoundingBox, CoordinateSpace
from hpcu.schemas.failure_codes import FailureCode
from hpcu.schemas.scene import Scene
from hpcu.schemas.targeting import TargetingPack
from hpcu.schemas.ui_element import UIElement

SPACE = CoordinateSpace.SCREEN_PHYSICAL_PX


def _bbox(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(space=SPACE, x=x, y=y, width=width, height=height)


@pytest.mark.unit
def test_targeting_pack_construction():
    pack = TargetingPack(
        goal_id="test-1",
        source="goal_tokens",
        ready_any=("loaded",),
        success_any=("done",),
        forbid_any=("error",),
        pick_query="click me",
        pick_required=True,
        dismiss_any=("accept",),
        blocked_any=("captcha",),
        ignore_any=("ad",),
    )
    assert pack.goal_id == "test-1"
    assert pack.source == "goal_tokens"
    assert pack.ready_any == ("loaded",)
    assert pack.success_any == ("done",)
    assert pack.forbid_any == ("error",)
    assert pack.pick_query == "click me"
    assert pack.pick_required is True
    assert pack.dismiss_any == ("accept",)
    assert pack.blocked_any == ("captcha",)
    assert pack.ignore_any == ("ad",)


@pytest.mark.unit
def test_targeting_pack_rejects_empty_goal_id():
    with pytest.raises(ValueError, match="goal_id"):
        TargetingPack(goal_id="")


@pytest.mark.unit
def test_targeting_pack_rejects_bad_source():
    with pytest.raises(ValueError, match="source"):
        TargetingPack(goal_id="x", source="unknown")


@pytest.mark.unit
def test_targeting_pack_defaults():
    pack = TargetingPack(goal_id="defaults")
    assert pack.source == "goal_tokens"
    assert pack.ready_any == ()
    assert pack.success_any == ()
    assert pack.pick_required is True


@pytest.mark.unit
def test_targeting_pack_is_frozen():
    pack = TargetingPack(goal_id="frozen")
    with pytest.raises(Exception):
        pack.goal_id = "mutated"  # type: ignore[misc]


@pytest.mark.unit
def test_fallback_tokenizer_brand_in_goal_not_hardcoded():
    tokenizer = GoalFallbackTokenizer()
    pack = tokenizer.tokenize("case-1", "쿠팡에서 생수 골라줘")
    assert "쿠팡에서" in pack.ready_any
    assert "생수" in pack.ready_any
    assert "골라줘" in pack.ready_any
    assert pack.pick_query == "쿠팡에서 생수 골라줘"
    assert pack.source == "goal_tokens"
    assert pack.pick_required is True


@pytest.mark.unit
def test_fallback_tokenizer_unknown_brand():
    tokenizer = GoalFallbackTokenizer()
    pack = tokenizer.tokenize("case-2", "XYZ마트에서 우유 사줘")
    assert "XYZ마트에서" in pack.ready_any
    assert "우유" in pack.ready_any
    assert "사줘" in pack.ready_any


@pytest.mark.unit
def test_fallback_tokenizer_filters_short_tokens():
    tokenizer = GoalFallbackTokenizer()
    pack = tokenizer.tokenize("case-3", "a b c d e 한글")
    single_char = [token for token in pack.ready_any if len(token) < 2]
    assert single_char == []
    assert "한글" in pack.ready_any


@pytest.mark.unit
def test_fallback_tokenizer_empty_goal():
    tokenizer = GoalFallbackTokenizer()
    pack = tokenizer.tokenize("case-4", "   ")
    assert len(pack.ready_any) >= 1
    assert pack.ready_any[0] == ""


def _scene(*elements: UIElement) -> Scene:
    return Scene(version=1, elements={element.id: element for element in elements})


def _element(
    id: str,
    text: str,
    x: float = 10,
    y: float = 200,
    width: float = 200,
    height: float = 20,
) -> UIElement:
    return UIElement(
        id=id,
        scene_version=1,
        role="text",
        name=text,
        text=text,
        bbox=_bbox(x, y, width, height),
    )


@pytest.mark.unit
def test_mentions_detects_token_in_scene():
    scene = _scene(_element("e1", "쿠팡에서 생수 구매"))
    assert mentions(scene, ("쿠팡",)) is True
    assert mentions(scene, ("네이버",)) is False


@pytest.mark.unit
def test_mentions_empty_tokens_is_true():
    scene = _scene(_element("e1", "anything"))
    assert mentions(scene, ()) is True


@pytest.mark.unit
def test_mentions_normalizes_hangul_spaces():
    scene = _scene(_element("e1", "쿠 팡 프 레 시"))
    assert mentions(scene, ("쿠팡",)) is True


@pytest.mark.unit
def test_mentions_empty_scene():
    assert mentions(Scene(version=1, elements={}), ("hello",)) is False


@pytest.mark.unit
def test_matcher_blocked_detects_anti_bot():
    pack = TargetingPack(goal_id="test", blocked_any=("captcha", "비정상적인"))
    matcher = GenericMatcher(pack)
    clean = _scene(_element("e1", "안녕하세요"))
    assert matcher.blocked(clean) is False
    wall = _scene(_element("e1", "비정상적인 접근이 감지되었습니다"))
    assert matcher.blocked(wall) is True


@pytest.mark.unit
def test_matcher_consent_candidates_skips_forbid():
    pack = TargetingPack(
        goal_id="test",
        dismiss_any=("동의", "확인"),
        forbid_any=("결제",),
    )
    matcher = GenericMatcher(pack)
    accept = _element("a", "동의하고 계속")
    pay = _element("b", "결제하기")
    scene = _scene(accept, pay)
    candidates = matcher.consent_candidates(scene)
    assert [item.id for item in candidates] == ["a"]


@pytest.mark.unit
def test_matcher_consent_candidates_empty_pack():
    pack = TargetingPack(goal_id="test", dismiss_any=())
    matcher = GenericMatcher(pack)
    scene = _scene(_element("a", "동의하고 계속"))
    assert matcher.consent_candidates(scene) == []


@pytest.mark.unit
def test_matcher_pick_candidates_sorts_and_ignores():
    pack = TargetingPack(
        goal_id="test",
        pick_query="생수",
        ignore_any=("광고",),
    )
    matcher = GenericMatcher(pack)
    target = _element("target", "생수 500ml", y=300)
    ad = _element("ad", "생수 광고", y=200)
    other = _element("other", "딴 것", y=400)
    scene = _scene(target, ad, other)
    candidates = matcher.pick_candidates(scene)
    assert len(candidates) == 1
    assert candidates[0].id == "target"


@pytest.mark.unit
def test_matcher_pick_candidates_empty_query():
    pack = TargetingPack(goal_id="test", pick_query="")
    matcher = GenericMatcher(pack)
    scene = _scene(_element("e1", "anything"))
    assert matcher.pick_candidates(scene) == []


@pytest.mark.unit
def test_matcher_skips_elements_without_bbox():
    pack = TargetingPack(goal_id="test", dismiss_any=("동의",))
    matcher = GenericMatcher(pack)
    no_bbox = UIElement(
        id="no_bbox",
        scene_version=1,
        role="text",
        name="동의",
        text="동의",
        bbox=None,
    )
    scene = _scene(no_bbox)
    assert matcher.consent_candidates(scene) == []


@pytest.mark.unit
def test_same_scene_different_pack_different_outcome():
    scene = _scene(
        _element("e1", "생수 12,900원"),
        _element("e2", "동의하고 계속"),
    )
    pack_a = TargetingPack(
        goal_id="case-a",
        dismiss_any=("동의",),
        pick_query="생수",
    )
    pack_b = TargetingPack(
        goal_id="case-b",
        dismiss_any=(),
        pick_query="생수",
        ignore_any=("생수",),
    )
    matcher_a = GenericMatcher(pack_a)
    matcher_b = GenericMatcher(pack_b)
    assert len(matcher_a.consent_candidates(scene)) == 1
    assert len(matcher_b.consent_candidates(scene)) == 0
    assert len(matcher_a.pick_candidates(scene)) == 1
    assert len(matcher_b.pick_candidates(scene)) == 0


class _FakeGateway(Gateway):
    def __init__(self, content: str = "", error: bool = False):
        self.content = content
        self.error = error
        self.prompts: list[str] = []
        self.purposes: list[ModelCallPurpose] = []

    @property
    def provider_id(self) -> str:
        return DEFAULT_PROVIDER_ID

    @property
    def model_id(self) -> str:
        return "MiniMax-M3"

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
        return GatewayResponse(
            content=self.content,
            model="MiniMax-M3",
            provider=DEFAULT_PROVIDER_ID,
            tokens_used=42,
        )


def _valid_targeting_json(*, pick_required: str = "true") -> str:
    return (
        '{"ready_any":["쿠팡"],"success_any":["장바구니"],'
        '"forbid_any":["결제"],"pick_query":"생수",'
        f'"pick_required":{pick_required},'
        '"dismiss_any":["동의"],"blocked_any":["captcha"],'
        '"ignore_any":["광고"]}'
    )


@pytest.mark.unit
def test_compiler_valid_json_response():
    inner = _FakeGateway(
        content='{"ready_any":["쿠팡","원"],"success_any":["장바구니"],'
        '"forbid_any":["결제"],"pick_query":"생수","pick_required":true,'
        '"dismiss_any":["동의"],"blocked_any":["captcha"],"ignore_any":["광고"]}'
    )
    gateway = CountingGateway(inner)
    compiler = TargetingCompiler(gateway)
    pack = compiler.compile("case-1", "쿠팡에서 생수 골라줘", "https://example.com")
    assert pack.source == "model"
    assert pack.goal_id == "case-1"
    assert pack.ready_any == ("쿠팡", "원")
    assert pack.success_any == ("장바구니",)
    assert pack.forbid_any == ("결제",)
    assert pack.pick_query == "생수"
    assert pack.pick_required is True
    assert pack.dismiss_any == ("동의",)
    assert pack.blocked_any == ("captcha",)
    assert pack.ignore_any == ("광고",)


@pytest.mark.unit
def test_compiler_no_gateway_falls_back():
    compiler = TargetingCompiler()
    pack = compiler.compile("case-1", "쿠팡에서 생수 골라줘")
    assert pack.source == "goal_tokens"
    assert "생수" in pack.ready_any


@pytest.mark.unit
def test_compiler_broken_json_fails_closed():
    inner = _FakeGateway(content="not valid json at all {{{")
    gateway = CountingGateway(inner)
    compiler = TargetingCompiler(gateway)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case-1", "쿠팡에서 생수 골라줘")

    assert captured.value.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value


@pytest.mark.unit
def test_compiler_gateway_error_fails_closed():
    inner = _FakeGateway(error=True)
    gateway = CountingGateway(inner)
    compiler = TargetingCompiler(gateway)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case-1", "쿠팡에서 생수 골라줘")

    assert captured.value.failure_code == FailureCode.MODEL_FAILED.value


@pytest.mark.unit
def test_compiler_ignores_reasoning_text_before_unique_object():
    inner = _FakeGateway(
        content="<thinking>I should pick these tokens</thinking>"
        + _valid_targeting_json()
    )
    gateway = CountingGateway(inner)
    compiler = TargetingCompiler(gateway)
    pack = compiler.compile("case-1", "쿠팡에서 생수 골라줘")
    assert pack.source == "model"
    assert pack.ready_any == ("쿠팡",)
    assert pack.pick_query == "생수"


@pytest.mark.unit
def test_compiler_rejects_non_boolean_pick_required():
    inner = _FakeGateway(content=_valid_targeting_json(pick_required='"false"'))
    gateway = CountingGateway(inner)
    compiler = TargetingCompiler(gateway)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case-1", "item을 열어줘")

    assert captured.value.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value


@pytest.mark.unit
def test_compiler_schema_mismatch_fails_closed():
    inner = _FakeGateway(content='{"unknown_field": "value"}')
    gateway = CountingGateway(inner)
    compiler = TargetingCompiler(gateway)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case-1", "쿠팡에서 생수 골라줘")

    assert captured.value.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value


@pytest.mark.unit
def test_compiler_non_dict_response_fails_closed():
    inner = _FakeGateway(content="[1, 2, 3]")
    gateway = CountingGateway(inner)
    compiler = TargetingCompiler(gateway)

    with pytest.raises(TargetingCompilationError) as captured:
        compiler.compile("case-1", "쿠팡에서 생수 골라줘")

    assert captured.value.failure_code == FailureCode.MODEL_SCHEMA_INVALID.value
