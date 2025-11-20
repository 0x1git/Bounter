from __future__ import annotations

from types import SimpleNamespace

from bounter.reporting import ScanReport


class DummyPart:
    def __init__(self, text: str = "", *, thought=None, role: str | None = None, parts=None):
        self.text = text
        self.thought = thought
        self.role = role
        self.parts = parts or []


class DummyResponse:
    def __init__(self, parts, *, usage=None):
        content = SimpleNamespace(parts=parts)
        candidate = SimpleNamespace(content=content)
        self.candidates = [candidate]
        self.usage_metadata = usage


def test_update_from_response_captures_thought_flags():
    thought_part = DummyPart("Need auth bypass", thought=True)
    answer_part = DummyPart("Final summary here")
    response = DummyResponse([thought_part, answer_part])

    report = ScanReport(target="t", description="d")
    report.update_from_response(response)

    assert report.thinking_summary == ["Need auth bypass"]
    assert report.final_analysis == "Final summary here"


def test_update_from_response_captures_nested_thought_content():
    nested = DummyPart("follow-up reasoning")
    container = DummyPart(parts=[nested])
    thought_wrapper = DummyPart(thought=container)
    answer_part = DummyPart("Done")
    usage = SimpleNamespace(
        thoughts_token_count=10,
        candidates_token_count=5,
        total_token_count=15,
    )
    response = DummyResponse([thought_wrapper, answer_part], usage=usage)

    report = ScanReport(target="t", description="d")
    report.update_from_response(response)

    assert report.thinking_summary == ["follow-up reasoning"]
    assert report.final_analysis == "Done"
    assert report.total_tokens == 15
