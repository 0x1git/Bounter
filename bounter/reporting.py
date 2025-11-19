"""Reporting and observability utilities."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional


@dataclass
class CommandRecord:
    """Represents the outcome of a single system command."""

    command: str
    success: bool
    return_code: Optional[int]
    stdout: str
    stderr: str


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

    def log_command(self, record: dict[str, Any]) -> None:
        """Append a command execution record to the report."""

        self.commands.append(
            CommandRecord(
                command=record.get("command_executed", ""),
                success=record.get("success", False),
                return_code=record.get("return_code"),
                stdout=record.get("stdout", ""),
                stderr=record.get("stderr", ""),
            )
        )

    def update_from_response(self, response: Any) -> None:
        """Extract thinking, final answer, and token usage from the response."""

        self.end_time = datetime.now(timezone.utc)

        try:
            candidate = response.candidates[0]
        except (AttributeError, IndexError):
            return

        final_chunks: List[str] = []
        for part in getattr(candidate.content, "parts", []) or []:
            text = getattr(part, "text", "")
            if not text:
                continue
            if getattr(part, "thought", False):
                self.thinking_summary.append(text)
            else:
                final_chunks.append(text)

        if final_chunks:
            self.final_analysis = "\n".join(final_chunks)

        usage = getattr(response, "usage_metadata", None)
        if usage:
            self.thinking_tokens = getattr(usage, "thoughts_token_count", None)
            self.output_tokens = getattr(usage, "candidates_token_count", None)
            self.total_tokens = getattr(usage, "total_token_count", None)

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
                lines.extend(
                    [
                        f"- `{record.command}` (success={record.success}, return_code={record.return_code})",
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
                ]
            )

        path.write_text("\n".join(lines))
