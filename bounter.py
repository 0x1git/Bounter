import subprocess
from google import genai
from google.genai import types

def execute_system_command_impl(command: str) -> dict:
    """Executes a system command autonomously.

    Args:
        command: The command to execute. For directory queries, use 'pwd' on Unix-like systems or 'cd' on Windows.

    Returns:
        A dictionary containing the command output and execution details.
    """
     # Let the AI agent decide the appropriate command based on the platform
    # No hardcoding - the agent will autonomously determine the correct command
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30  # Add timeout for safety
        )
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "command_executed": command,
            "return_code": result.returncode,
            "success": True
        }
    except subprocess.CalledProcessError as e:
        return {
            "stdout": e.stdout.strip() if e.stdout else "",
            "stderr": e.stderr.strip() if e.stderr else "",
            "command_executed": command,
            "return_code": e.returncode,
            "error": str(e),
            "success": False
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "Command timed out after 30 seconds",
            "command_executed": command,
            "error": "Timeout",
            "success": False
        }

# Configure the client for autonomous operation with thinking enabled
client = genai.Client()
config = types.GenerateContentConfig(
    system_instruction="""You are an autonomous Bug Bounty Hunter AI assistant with access to system commands. Your role is to:

1. Understand user requests and execute them without asking for permission
2. Use available tools to gather information or perform actions autonomously 
3. Chain multiple function calls together when needed to complete complex tasks
4. Provide clear, helpful responses based on the results
5. Exit gracefully when the goal is achieved
6. Always choose the appropriate system commands based on the user's operating system
7. Execute commands efficiently and report results clearly

You have access to a system command execution tool that can run any shell command. Use it wisely and autonomously to fulfill user requests.""",
    tools=[execute_system_command_impl],  # Only the system command execution tool
    temperature=0,  # Low temperature for more deterministic function calls
    thinking_config=types.ThinkingConfig(
        thinking_budget=-1,  # Dynamic thinking: model decides when and how much to think
        include_thoughts=True  # Include thought summaries to show reasoning process
    ),
    tool_config=types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(
            mode="AUTO"  # Let model decide when to use functions
        )
    )
)

# User prompt - this is what the user actually wants
user_prompt = "make a curl request to http://localhost:57478/"

# Make the request - the SDK will automatically handle function calls
response = client.models.generate_content(
    model="gemini-2.5-flash",  # Using 2.5 Flash which supports thinking
    contents=user_prompt,
    config=config,
)

print("\nAutonomous System Command Execution with Thinking:")
print("=" * 60)

# Display thinking process and final answer
for part in response.candidates[0].content.parts:
    if not part.text:
        continue
    if part.thought:
        print("🧠 THINKING PROCESS:")
        print("-" * 40)
        print(part.text)
        print("-" * 40)
    else:
        print("💡 FINAL ANSWER:")
        print(part.text)

# Display token usage information
if hasattr(response, 'usage_metadata'):
    print(f"\n📊 TOKEN USAGE:")
    print(f"Thinking tokens: {response.usage_metadata.thoughts_token_count}")
    print(f"Output tokens: {response.usage_metadata.candidates_token_count}")
    print(f"Total tokens: {response.usage_metadata.total_token_count}")