## Bounter

Autonomous bug-bounty agent powered by Gemini. Recent wins: xben-005-24, XBEN-072-24, XBEN-058-24, xben-039-24, xben-021-24, XBEN-102-24, XBEN-045-24, xben-096-24, XBEN-076-24, XBEN-077-24, xben-083-24, XBEN-020-24
xben-061-24, XBEN-036-24, XBEN-019-24: solved by filename hint.

- API Rate Limit: The default Gemini quota is ~10 requests/minute for the primary model, so the agent now rotates across 2.5/2.0 Flash variants automatically.
- CLI (recommended): `python3 bounter.py <target> -d "description" -v`
- Built-in exploit reconnaissance: the agent now exposes a dedicated `searchsploit` tool so it can look up CVEs/shellcodes, mirror matching exploits locally, and optionally test PoCs automatically.
- Iterative Python automation: the Gemini agent can spin up persistence-aware Python sessions to generate/run custom fuzzing loops that issue many HTTP requests per run via the built-in `python_code_executor` function tool.
- Reverse-shell listeners: the `start_listener` background tool spawns `nc -lnvp <port>` listeners so the agent can catch callbacks from uploaded reverse shells without blocking the main CLI.

All execution now happens directly in your terminal so Rich renderables (progress, panels, token tables) animate correctly without a Textual shim. Reports continue to write to `reports/` (JSON + Markdown) at the end of every run.

### Running the CLI

```
python bounter.py https://target.example --description "CMS recon" -v
```

Useful flags:
- `-d/--description` add extra context for the agent
- `--report-dir` change where the JSON/Markdown artifacts are stored
- `--report-prefix` customize output filenames
- `-v/--verbose` prints additional agent logging

The CLI prints thinking summaries, final analysis, token usage, and saves the structured report artifacts automatically.
