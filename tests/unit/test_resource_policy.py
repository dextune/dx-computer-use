"""Tests for configuration-driven local CPU allocation."""

import pytest

from hpcu.runtime_core.resource_policy import LocalResourcePolicy

pytestmark = pytest.mark.unit


def test_policy_reserves_cores_and_caps_workers():
    config = {
        "performance": {
            "local_reserve_cores": 2,
            "max_perception_workers": 8,
            "perception_queue_multiplier": 3,
        }
    }

    policy = LocalResourcePolicy.from_config(config, cpu_count=6)

    assert policy.cpu_count == 6
    assert policy.reserve_cores == 2
    assert policy.perception_workers == 4
    assert policy.perception_queue_depth == 12


def test_policy_keeps_one_worker_on_single_core():
    policy = LocalResourcePolicy.from_config(
        {"performance": {"local_reserve_cores": 4}},
        cpu_count=1,
    )

    assert policy.reserve_cores == 0
    assert policy.perception_workers == 1
    assert policy.perception_queue_depth >= 1


def test_explicit_queue_depth_cannot_be_smaller_than_workers():
    policy = LocalResourcePolicy.from_config(
        {
            "performance": {
                "max_perception_workers": 4,
                "max_perception_queue_depth": 1,
            }
        },
        cpu_count=8,
    )

    assert policy.perception_workers == 4
    assert policy.perception_queue_depth == 4
