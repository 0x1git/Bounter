"""Text-based user interface for the Bounter agent."""
from __future__ import annotations

import asyncio
import shlex
from datetime import datetime
from pathlib import Path
from textwrap import dedent

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Static, Log

from bounter.agent import BounterAgent
from bounter.config import BounterConfig
from bounter.reporting import ScanReport

BANNER_ART = dedent(
    r"""
██████╗  ██████╗ ██╗   ██╗███╗   ██╗████████╗███████╗██████╗ 
██╔══██╗██╔═══██╗██║   ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗
██████╔╝██║   ██║██║   ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝
██╔══██╗██║   ██║██║   ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗
██████╔╝╚██████╔╝╚██████╔╝██║ ╚████║   ██║   ███████╗██║  ██║
╚═════╝  ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝
                        
"""
).rstrip()

COMMANDS_TEXT_RAW = """/about     - System information\n/theme     - Change appearance\n/help      - Show help\n/run       - Run scan: /run <target> [description]\n/hil on|off - Toggle human-in-loop\n/guidance  - Provide manual input\n/clear     - Clear console\n/exit      - Exit application"""

SHORTCUTS_TEXT_RAW = """! shell - (reserved)\nc/y     - Copy selection (focus log)\nEsc     - Clear input\nCtrl+c  - Cancel/Exit\nCtrl+u  - Clear line\nCtrl+a  - Move cursor to start\nCtrl+e  - Move cursor to end"""


def _pad_help_text(text: str, target_rows: int) -> str:
    """Pad help text so both panels render with the same height."""

    lines = text.splitlines()
    padding_needed = target_rows - len(lines)
    if padding_needed > 0:
        lines.extend(["" for _ in range(padding_needed)])
    return "\n".join(lines)


HELP_PANEL_ROWS = max(
    len(COMMANDS_TEXT_RAW.splitlines()), len(SHORTCUTS_TEXT_RAW.splitlines())
)
COMMANDS_TEXT = _pad_help_text(COMMANDS_TEXT_RAW, HELP_PANEL_ROWS)
SHORTCUTS_TEXT = _pad_help_text(SHORTCUTS_TEXT_RAW, HELP_PANEL_ROWS)


class Banner(Static):
    """Stylized ASCII banner."""

    DEFAULT_CSS = """Banner {
        content-align: center middle;
        margin: 1 0;
        color: #f8e9b0;
        text-style: bold;
        border: none;
    }
    """

    def __init__(self) -> None:
        art = f"[b]{BANNER_ART}[/b]\n               Your Security Research Assistant\n ___________________________________________________________"
        super().__init__(art, id="banner")


class HelpPanel(Static):
    """Panel showing commands or shortcuts."""

    def __init__(self, title: str, body: str, *, panel_id: str) -> None:
        super().__init__("", id=panel_id)
        self.title = title
        self.body = body

    DEFAULT_CSS = """
    HelpPanel {
        border: solid #f8e9b0;
        padding: 1;
        width: 1fr;
        color: #f8e9b0;
        min-height: 9;
    }
    HelpPanel>#title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(f"[b]{self.title}[/b]", id="title")
        yield Static(self.body, id="body")


class ConsoleLog(Log):
    """Log widget with clipboard shortcuts when focused."""

    BINDINGS = [
        Binding("c", "copy_highlight", "Copy", show=False),
        Binding("y", "copy_highlight", "Copy", show=False),
    ]

    def action_copy_highlight(self) -> None:
        selection = self.text_selection
        if not selection:
            self.write("[dim]Select text before pressing c/y to copy.[/dim]")
            return

        extracted = self.get_selection(selection)
        if not extracted:
            self.write("[dim]Unable to read the current selection.[/dim]")
            return

        text, ending = extracted
        payload = f"{text}{ending or ''}".rstrip("\n")
        if not payload:
            self.write("[dim]Selection was empty.[/dim]")
            return

        if self.app:
            self.app.copy_to_clipboard(payload)
        self.write("[dim]Copied selection to clipboard.[/dim]")


class BounterTUI(App):
    """Main Textual application."""

    CSS = """
    Screen {
        background: #050505;
        color: #f8e9b0;
    }

    Header, Footer {
        background: #0b0b0b;
        color: #f8e9b0;
        text-style: bold;
    }

    /* Log styling */
    Log {
        height: 1fr;
        border: solid #f8e9b0;
        margin: 1 0;
        padding: 1;
        background: #010101;
        color: #f8e9b0;
    }

    Input {
        border: tall #f8e9b0;
        height: 3;
        margin-top: 1;
        background: #010101;
        color: #f8e9b0;
    }

    Horizontal#help-panels {
        margin: 1 0;
    }

    Horizontal#help-panels > * {
        margin-right: 2;
    }

    Horizontal#help-panels > *:last-child {
        margin-right: 0;
    }

    .hidden-log,
    .hidden-panel {
        display: none;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("escape", "clear_input", "Clear Input"),
        Binding("ctrl+u", "clear_input", "Clear Line"),
        Binding("ctrl+a", "cursor_home", "Home"),
        Binding("ctrl+e", "cursor_end", "End"),
    ]

    scanning: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Banner()
        yield Horizontal(
            HelpPanel("Commands", COMMANDS_TEXT, panel_id="commands"),
            HelpPanel("Shortcuts", SHORTCUTS_TEXT, panel_id="shortcuts"),
            id="help-panels",
        )
        yield ConsoleLog(id="console", highlight=False, classes="hidden-log")
        yield Input(placeholder="Type /help for available commands", id="command-input")
        yield Footer()

    async def on_mount(self) -> None:
        self._output_visible = False
        self._intro_hidden = False
        self._command_count = 0
        self._session_target: str | None = None
        self._session_description: str = ""
        self._guidance_history: list[str] = []
        self._hil_mode: bool = False
        self._awaiting_guidance: bool = False
        self.set_focus(self.query_one(Input))

    @property
    def log_view(self) -> ConsoleLog:
        return self.query_one(ConsoleLog)

    @property
    def command_input(self) -> Input:
        return self.query_one(Input)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        event.input.value = ""
        if not command:
            return
        if command.startswith("/"):
            await self.handle_command(command)
        else:
            await self.handle_guidance(command)

    async def handle_command(self, command: str) -> None:
        self._begin_command_output(command)
        if command.startswith("/help"):
            self._log(
                "Available commands: /run, /about, /theme, /clear, /exit, /hil, /guidance"
            )
        elif command.startswith("/about"):
            self._log("Bounter - Autonomous Bug Bounty Hunter powered by Gemini")
        elif command.startswith("/clear"):
            self.log_view.clear()
            self._command_count = 0
            return
        elif command.startswith("/exit"):
            await self.action_quit()
        elif command.startswith("/run"):
            await self._handle_run(command)
        elif command.startswith("/hil"):
            await self._toggle_hil(command)
        elif command.startswith("/guidance"):
            await self._request_guidance()
        elif command.startswith("/theme"):
            self._log("Theme switching not implemented yet.")
        else:
            self._log(f"Unknown command: {command}")

    async def handle_guidance(self, instruction: str) -> None:
        if not self._hil_mode:
            self._log("Human-in-loop mode is disabled. Enable it with /hil on first.")
            return
        if self.scanning:
            self._log("A scan is already running. Please wait before adding guidance.")
            return
        if not self._session_target:
            self._log("Start a scan with /run before sending additional guidance.")
            return
        if not self._awaiting_guidance:
            self._log("Use /guidance when the agent requests input before typing instructions.")
            return

        self._begin_command_output(instruction)
        description = self._compose_description(extra_instruction=instruction)
        started = self._start_scan(
            target=self._session_target,
            description=description,
            instruction_label=instruction,
        )
        if started:
            self._guidance_history.append(instruction)
            self._awaiting_guidance = False

    async def _handle_run(self, command: str) -> None:
        if self.scanning:
            self._log("A scan is already running. Please wait.")
            return

        parts = shlex.split(command)
        if len(parts) < 2:
            self._log("Usage: /run <target> [description]")
            return

        target = parts[1]
        description = " ".join(parts[2:]) if len(parts) > 2 else ""

        self._session_target = target
        self._session_description = description
        self._guidance_history = []

        instruction_label = description or f"Investigate {target}"
        compiled_description = self._compose_description()
        self._start_scan(target, compiled_description, instruction_label)

    def _start_scan(self, target: str, description: str, instruction_label: str) -> bool:
        if self.scanning:
            self._log("A scan is already running. Please wait.")
            return False
        self._log(f"▶ Starting scan for {target}")
        if description and description != instruction_label:
            self._log(f"  Context: {description}")
        self.scanning = True
        asyncio.create_task(
            self._run_scan(target, description, instruction_label)
        )
        return True

    async def _run_scan(self, target: str, description: str, instruction_label: str) -> None:
        try:
            response, report, transcript_lines = await asyncio.to_thread(
                self._execute_scan, target, description, instruction_label
            )
            for line in transcript_lines:
                self._log(line)

            if response is not None:
                self._emit_response_summary(report)
            paths = self._persist_report(report)
            self._log(f"Reports saved to {paths[0]} and {paths[1]}")
        except Exception as exc:  # pragma: no cover - runtime diagnostics
            self._log(f"❌ Scan failed: {exc}")
        finally:
            self.scanning = False
            if self._hil_mode:
                self._awaiting_guidance = False

    def _emit_response_summary(self, report: ScanReport) -> None:
        if report.final_analysis:
            self._log("--- Final Analysis ---")
            self._log(report.final_analysis)
        if report.total_tokens is not None:
            self._log("--- Token Usage ---")
            self._log(
                f"Thinking: {report.thinking_tokens}, Output: {report.output_tokens}, Total: {report.total_tokens}"
            )

    def _execute_scan(self, target: str, description: str, instruction_label: str):
        config = BounterConfig.from_env()
        report = ScanReport(target=target, description=description)
        agent = BounterAgent(config=config, report=report, verbose=False)
        response = agent.run(target=target, description=description)
        transcript = self._build_transcript(instruction_label, report)
        return response, report, transcript

    def _persist_report(self, report: ScanReport) -> tuple[str, str]:
        report_dir = Path("reports")
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = (report.end_time or datetime.utcnow()).strftime("%Y%m%d-%H%M%S")
        base = report_dir / f"scan-{timestamp}"
        json_path = base.with_suffix(".json")
        md_path = base.with_suffix(".md")
        report.save_json(json_path)
        report.save_markdown(md_path)
        return str(json_path), str(md_path)

    def _log(self, message: str) -> None:
        text = message if message.endswith("\n") else f"{message}\n"
        self.log_view.write(text)

    def _ensure_output_visible(self) -> None:
        if not getattr(self, "_output_visible", False):
            self.log_view.remove_class("hidden-log")
            self._output_visible = True

    def _begin_command_output(self, command: str) -> None:
        self._ensure_output_visible()
        if not getattr(self, "_intro_hidden", False):
            self._hide_intro_panels()
        if self._command_count:
            self.log_view.write("")
        self._command_count += 1
        self._log(f"$ {command}")

    def _hide_intro_panels(self) -> None:
        self.query_one(Banner).add_class("hidden-panel")
        self.query_one('#help-panels', Horizontal).add_class("hidden-panel")
        self._intro_hidden = True

    async def _toggle_hil(self, command: str) -> None:
        parts = command.split()
        if len(parts) != 2 or parts[1].lower() not in {"on", "off"}:
            self._log("Usage: /hil on|off")
            return
        enable = parts[1].lower() == "on"
        self._hil_mode = enable
        state = "enabled" if enable else "disabled"
        self._awaiting_guidance = False
        self._log(f"Human-in-loop mode {state}.")

    async def _request_guidance(self) -> None:
        if not self._hil_mode:
            self._log("Enable human-in-loop mode first using /hil on.")
            return
        if self.scanning:
            self._log("Wait for the current scan to finish before guiding.")
            return
        if not self._session_target:
            self._log("Start a scan with /run before requesting guidance.")
            return
        if self._awaiting_guidance:
            self._log("Already waiting for operator guidance.")
            return
        self._awaiting_guidance = True
        self._log("Agent paused. Enter new guidance text to continue.")

    def _compose_description(self, extra_instruction: str | None = None) -> str:
        parts: list[str] = []
        base = self._session_description.strip()
        if base:
            parts.append(base)
        for idx, entry in enumerate(self._guidance_history, start=1):
            parts.append(f"Operator guidance {idx}: {entry}")
        if extra_instruction:
            parts.append(
                f"Operator guidance {len(self._guidance_history) + 1}: {extra_instruction}"
            )
        return "\n".join(parts).strip()

    def _build_transcript(self, instruction: str, report: ScanReport) -> list[str]:
        lines: list[str] = []
        display_instruction = instruction.strip() or "Investigate the target"
        lines.append(f"Instruction: {display_instruction}")

        if report.thinking_summary:
            for thought in report.thinking_summary:
                thought_lines = thought.splitlines() or [""]
                for idx, chunk in enumerate(thought_lines):
                    prefix = "✧ Thought: " if idx == 0 else "  "
                    lines.append(f"{prefix}{chunk}")
        else:
            lines.append("✧ Thought: _model did not share its reasoning._")

        if report.commands:
            for record in report.commands:
                lines.append(f"🔧 Command: {record.command}")
                stdout = record.stdout.strip()
                stderr = record.stderr.strip()
                if stdout:
                    for chunk in stdout.splitlines():
                        lines.append(f"    stdout: {chunk}")
                else:
                    lines.append("    stdout: <empty>")
                if stderr:
                    for chunk in stderr.splitlines():
                        lines.append(f"    stderr: {chunk}")
        else:
            lines.append("🔧 No tool execution was required.")

        return lines

    async def on_focus(self, event: events.Focus) -> None:  # pragma: no cover - UI interaction only
        if event.sender is self:
            await self.set_focus(self.command_input)

    # Shortcut action handlers
    def action_clear_input(self) -> None:
        field = self.command_input
        field.value = ""
        field.cursor_position = 0

    def action_cursor_home(self) -> None:
        self.command_input.cursor_position = 0

    def action_cursor_end(self) -> None:
        self.command_input.cursor_position = len(self.command_input.value)


if __name__ == "__main__":
    BounterTUI().run()
