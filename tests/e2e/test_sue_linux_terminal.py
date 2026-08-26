"""Live Linux sandbox SUE terminal evidence path."""

import asyncio

import pytest

from hpcu.platform.linux.sandbox import SandboxHttpClient, GrokSandboxStructure, probe

pytestmark = [pytest.mark.e2e, pytest.mark.platform_linux_x11]


@pytest.fixture
def sandbox_up():
    if not probe():
        pytest.skip("grokbot-gateway-sandbox is not reachable on :1337")


async def test_sue_terminal_window_produces_structure_evidence(sandbox_up):
    client = SandboxHttpClient()
    probe_result = client.exec("sh", ["-lc", "command -v xterm >/dev/null 2>&1"])
    if not (probe_result.get("ok") is True or probe_result.get("code") in (0, "0")):
        pytest.skip("xterm is not installed in the Linux sandbox")

    title = "HPCU-SUE-Terminal-E2E"
    launch = client.exec(
        "sh",
        [
            "-lc",
            (
                f"nohup xterm -T '{title}' -e sh -lc "
                "'printf SUE-E2E; sleep 8' >/dev/null 2>&1 &"
            ),
        ],
    )
    assert launch.get("ok") is True or launch.get("code") in (0, "0")

    structure = GrokSandboxStructure("sue-terminal-e2e")
    for _ in range(40):
        elements = await structure.observe_structure()
        if any(title in (element.name or "") for element in elements):
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("terminal window evidence never appeared in sandbox structure")
