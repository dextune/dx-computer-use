"""Regression tests for provider JSON framing."""

import pytest

from hpcu.gateway.json_response import extract_json_objects, select_json_object

pytestmark = pytest.mark.unit


def test_nested_wrapper_objects_are_emitted_once():
    content = 'prefix {"response":{"ready":true}} suffix'

    objects = extract_json_objects(content)

    assert objects == (
        {"response": {"ready": True}},
        {"ready": True},
    )


def test_invalid_object_before_valid_object_does_not_hide_later_payload():
    content = 'draft {bad json} then {"ready":true}'

    assert select_json_object(content, required_keys={"ready"}) == {"ready": True}


def test_sequential_matching_objects_remain_fail_closed():
    content = '{"ready":true}\n{"ready":false}'

    with pytest.raises(ValueError, match="ambiguous"):
        select_json_object(content, required_keys={"ready"})


def test_invalid_json_is_not_repaired_by_transport_parser():
    assert extract_json_objects('{"ready":true,}') == ()
