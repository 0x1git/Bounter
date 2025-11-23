## Bounter

Autonomous bug-bounty agent powered by Gemini. Recent wins: xben-005-24, XBEN-058-24, xben-039-24, xben-021-24, XBEN-102-24, xben-096-24, XBEN-076-24.

- API Rate Limit: The default Gemini quota is ~10 requests/minute for the primary model, so the agent now rotates across 2.5/2.0 Flash variants automatically.
- CLI (recommended): `python3 bounter.py <target> -d "description" -v`
- Built-in exploit reconnaissance: the agent now exposes a dedicated `searchsploit` tool so it can look up CVEs/shellcodes, mirror matching exploits locally, and optionally test PoCs automatically.

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
