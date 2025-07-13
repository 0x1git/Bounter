# Bounter - Autonomous Bug Bounty Hunter

## Project Overview
Bounter is an autonomous bug bounty hunting tool that leverages Google's Gemini AI with function calling capabilities to perform security testing. The core architecture consists of a single-file Python application (`bounter.py`) that creates an AI agent with system command execution capabilities.

## Key Architecture Patterns

### Gemini AI Integration
- **Primary API**: Use [Google Gemini API documentation](https://ai.google.dev/gemini-api/docs) for all AI-related code
- **Model**: `gemini-2.5-flash` with thinking capabilities enabled
- **Function Calling**: Uses `execute_system_command_impl` as the primary tool for autonomous system interaction
- **Configuration Pattern**: Use `types.GenerateContentConfig` with strict system instructions for bug bounty methodology

### System Command Execution
```python
# Pattern: Autonomous command execution with comprehensive error handling
result = subprocess.run(command, shell=True, check=True, 
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                       text=True, timeout=30)
```
- All system commands are executed through `execute_system_command_impl`
- Commands run with 30-second timeout for safety
- Rich console output with emoji indicators (🔧, ✅, ❌, ⚠️, ⏰, 📊)
- Returns structured dict with stdout, stderr, return_code, and success status

### Bug Bounty Methodology
The AI follows this specific methodology with clear stopping conditions:
1. Understand requests without asking permission
2. Use tools autonomously 
3. Chain function calls for complex tasks
4. **Critical**: Always crawl full web applications to discover endpoints/parameters
5. Analyze HTTP requests/responses for attack vectors
6. Test ALL discovered endpoints/parameters systematically
7. Confirm vulnerabilities before reporting (no assumptions)
8. Continue testing until exhaustive coverage is achieved

**STOPPING CONDITIONS:**
- STOP ONLY when vulnerability found with working PoC
- STOP ONLY when ALL endpoints/parameters tested exhaustively with NO vulnerabilities found
- DO NOT STOP just for having a plan - continue executing tests
- DO NOT STOP until comprehensive testing of entire attack surface is complete

## Development Patterns

### AI Configuration
- **Temperature**: Set to 0 for deterministic function calling
- **Thinking Config**: Use dynamic thinking budget (-1) with included thoughts
- **Tool Config**: AUTO mode for function calling


### Console Output Standards
- Use structured console output with visual separators (`-` * 40, `=` * 60)
- Include emoji indicators for different states
- Always show token usage when available (thinking, output, total tokens)
- Display both thinking summaries and final analysis separately

### Error Handling
- Handle `subprocess.CalledProcessError` and `subprocess.TimeoutExpired`
- Return consistent dict structure for all command results
- Log all command execution details in real-time

## Dependencies
- `google-genai` - Primary AI interface
- `subprocess` - System command execution (Python stdlib)

## Running the Tool
Execute directly: `python bounter.py`
- User prompt is hardcoded in the script (modify `user_prompt` variable)
- Tool operates autonomously without user interaction once started


