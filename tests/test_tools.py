from __future__ import annotations

from unittest.mock import MagicMock, patch

from bounter.reporting import ScanReport
from bounter.tools import build_system_command_tool


def test_build_system_command_tool_emits_callback_payload():
    report = ScanReport(target="t", description="d")
    events: list[dict] = []
    tool = build_system_command_tool(report, timeout=5, on_command=events.append)

    fake_result = MagicMock(stdout="ok", stderr="", returncode=0)

    with patch("bounter.tools.subprocess.run", return_value=fake_result):
        tool("echo ok")

    assert events, "tool callback should be invoked"
    payload = events[0]
    assert payload["tool_name"] == "build_system_command_tool"
    assert payload["command_executed"] == "echo ok"
    assert payload["stdout"] == "ok"
    assert payload["success"] is True