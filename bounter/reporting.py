"""Reporting and observability utilities."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence


@dataclass
class CommandRecord:
    """Represents the outcome of a single tool invocation."""

    command: str
    success: bool
    return_code: Optional[int]
    stdout: str
    stderr: str
    tool_name: Optional[str] = None


@dataclass
class ScanReport:
    """Captures everything relevant about a scan session."""

    target: str
    description: str
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    commands: List[CommandRecord] = field(default_factory=list)
    thinking_summary: List[str] = field(default_factory=list)
    final_analysis: Optional[str] = None
    thinking_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    python_executor_invocations: int = 0
    total_tool_invocations: int = 0

    def log_command(self, record: dict[str, Any]) -> None:
        """Append a command execution record to the report."""

        tool_name = record.get("tool_name")
        self.commands.append(
            CommandRecord(
                command=record.get("command_executed", ""),
                success=record.get("success", False),
                return_code=record.get("return_code"),
                stdout=record.get("stdout", ""),
                stderr=record.get("stderr", ""),
                tool_name=tool_name,
            )
        )
        self.total_tool_invocations += 1
        if tool_name == "python_code_executor":
            self.python_executor_invocations += 1

    def update_from_response(self, response: Any) -> None:
        """Extract thinking, final answer, and token usage from the response."""

        self.end_time = datetime.now(timezone.utc)

        try:
            candidate = response.candidates[0]
        except (AttributeError, IndexError):
            return

        final_chunks: List[str] = []
        parts = getattr(candidate.content, "parts", []) or []
        for part in parts:
            is_thought, source = self._resolve_thought_source(part)
            target = self.thinking_summary if is_thought else final_chunks
            for chunk in self._extract_text_segments(source):
                trimmed = chunk.strip()
                if trimmed:
                    target.append(trimmed)

        if final_chunks:
            self.final_analysis = "\n".join(final_chunks)

        usage = getattr(response, "usage_metadata", None)
        if usage:
            self.thinking_tokens = getattr(usage, "thoughts_token_count", None)
            self.output_tokens = getattr(usage, "candidates_token_count", None)
            self.total_tokens = getattr(usage, "total_token_count", None)

    def _resolve_thought_source(self, part: Any) -> tuple[bool, Any]:
        """Determine if a part represents a thought and return the source to parse."""

        marker = getattr(part, "thought", None)
        if isinstance(marker, bool):
            return marker, part
        if marker not in (None, False):
            return True, marker

        role = getattr(part, "role", None)
        if isinstance(role, str) and role.lower() == "thought":
            return True, part

        kind = getattr(part, "kind_", None)
        if isinstance(kind, str) and "thought" in kind.lower():
            return True, part

        return False, part

    def _extract_text_segments(self, node: Any, depth: int = 0) -> list[str]:
        """Recursively pull text segments from arbitrary response structures."""

        if node is None or depth > 5:
            return []

        segments: list[str] = []

        if isinstance(node, str):
            return [node]

        if isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            for item in node:
                segments.extend(self._extract_text_segments(item, depth + 1))
            return segments

        text_attr = getattr(node, "text", None)
        if text_attr:
            segments.extend(self._extract_text_segments(text_attr, depth + 1))

        parts_attr = getattr(node, "parts", None)
        if parts_attr:
            segments.extend(self._extract_text_segments(parts_attr, depth + 1))

        if hasattr(node, "model_dump"):
            try:
                dumped = node.model_dump()
            except Exception:  # pragma: no cover - defensive
                dumped = None
        elif hasattr(node, "to_dict"):
            try:
                dumped = node.to_dict()
            except Exception:  # pragma: no cover - defensive
                dumped = None
        else:
            dumped = None

        if isinstance(dumped, dict):
            if "text" in dumped:
                segments.extend(self._extract_text_segments(dumped["text"], depth + 1))
            if "parts" in dumped:
                segments.extend(self._extract_text_segments(dumped["parts"], depth + 1))

        return segments

    def _as_serializable(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the report."""

        return {
            "target": self.target,
            "description": self.description,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "commands": [record.__dict__ for record in self.commands],
            "thinking_summary": self.thinking_summary,
            "final_analysis": self.final_analysis,
            "thinking_tokens": self.thinking_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "python_executor_invocations": self.python_executor_invocations,
            "total_tool_invocations": self.total_tool_invocations,
        }

    def save_json(self, path: Path) -> None:
        """Persist the report as JSON."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._as_serializable(), indent=2))

    def save_markdown(self, path: Path) -> None:
        """Persist the report as Markdown for quick reading."""

        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Bounter Scan Report",
            "",
            f"**Target:** {self.target}",
            f"**Description:** {self.description or 'N/A'}",
            f"**Started:** {self.start_time.isoformat()}",
            f"**Finished:** {self.end_time.isoformat() if self.end_time else 'N/A'}",
            f"**Commands Executed:** {len(self.commands)}",
            "",
            "## Thinking Summary",
            "" if self.thinking_summary else "_No thinking output recorded._",
        ]
        if self.thinking_summary:
            lines.extend(f"- {thought}" for thought in self.thinking_summary)
        lines.extend(
            [
                "",
                "## Final Analysis",
                self.final_analysis or "_No final analysis provided._",
                "",
                "## Commands",
            ]
        )
        if self.commands:
            for record in self.commands:
                tool_hint = f" tool={record.tool_name}" if record.tool_name else ""
                lines.extend(
                    [
                        f"- `{record.command}` (success={record.success}, return_code={record.return_code}{tool_hint})",
                        "  - stdout: " + (record.stdout or "<empty>"),
                        "  - stderr: " + (record.stderr or "<empty>"),
                    ]
                )
        else:
            lines.append("_No system commands executed._")

        if self.total_tokens is not None:
            lines.extend(
                [
                    "",
                    "## Token Usage",
                    f"- Thinking tokens: {self.thinking_tokens}",
                    f"- Output tokens: {self.output_tokens}",
                    f"- Total tokens: {self.total_tokens}",
                    "",
                    "## Tool Usage",
                    f"- python_code_executor invocations: {self.python_executor_invocations}",
                    f"- Total tool invocations: {self.total_tool_invocations}",
                ]
            )

        path.write_text("\n".join(lines))
