## Bounter

Autonomous bug-bounty agent powered by Gemini. Recent wins: xben-005-24, XBEN-058-24, xben-039-24, xben-021-24.

- API Rate Limit: The default Gemini quota is ~10 requests/minute for the primary model, so the agent now rotates across 2.5/2.0 Flash variants automatically.
- CLI: `python bounter.py <target> -d "description"`
- TUI: `python -m bounter.tui` launches the Terminal User Interface / Console UI interface.

### Running the TUI

```
python -m bounter.tui
```

Commands inside the TUI:
- `/run <target> [description]` — start a scan
- `/hil on|off` — toggle human-in-loop mode for manual guidance
- `/guidance` — pause the agent and enter your next instruction when HiL is enabled
- `/about`, `/help`, `/clear`, `/exit` — informational and utility commands

### Human-in-loop Workflow

1. `/run <target> [description]` to launch the initial scan.
2. `/hil on` to enable guided mode (optional but required for manual overrides).
3. When you need to steer the agent, use `/guidance` and then type the text prompt when asked.
4. Submit as many guidance messages as needed; `/hil off` returns to fully autonomous mode.

Reports are still written to the `reports/` folder (JSON + Markdown) after every scan.
