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
    
    # Show real-time execution feedback
    print(f"\n🔧 EXECUTING COMMAND: {command}")
    print("-" * 40)
    
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
        
        # Show command output in real-time
        if result.stdout.strip():
            print(f"✅ STDOUT:\n{result.stdout.strip()}")
        if result.stderr.strip():
            print(f"⚠️  STDERR:\n{result.stderr.strip()}")
        
        print(f"📊 Return Code: {result.returncode}")
        print("-" * 40)
        
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "command_executed": command,
            "return_code": result.returncode,
            "success": True
        }
    except subprocess.CalledProcessError as e:
        print(f"❌ COMMAND FAILED:")
        print(f"Return Code: {e.returncode}")
        if e.stdout:
            print(f"STDOUT: {e.stdout.strip()}")
        if e.stderr:
            print(f"STDERR: {e.stderr.strip()}")
        print("-" * 40)
        
        return {
            "stdout": e.stdout.strip() if e.stdout else "",
            "stderr": e.stderr.strip() if e.stderr else "",
            "command_executed": command,
            "return_code": e.returncode,
            "error": str(e),
            "success": False
        }
    except subprocess.TimeoutExpired:
        print(f"⏰ COMMAND TIMED OUT after 30 seconds")
        print("-" * 40)
        
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
    system_instruction="""You are an autonomous Bug Bounty Hunter AI assistant with access to system commands. Your are inside a Windows 11 environment. Always choose the appropriate system commands based on the user's operating system. Your role is to:

Follow this comprehensive bug bounty methodology:
1. Understand user requests and execute them without asking for permission
2. Use available tools to gather information or perform actions autonomously 
3. Chain multiple function calls together when needed to complete complex tasks
4. Always crawl the full web application to discover endpoints and parameters and then think which attack vectors to test based on the discovered parameters and endpoints 
5. Analyze the request and response of the HTTP request to identify Attack Vectors and Vulnerabilities
6. Test all the endpoints and parameters discovered in the web application for vulnerabilities dont miss any of them pay close attention to the newly discovered parameters and endpoints (if any)
7. Always Confirm that the Vulnerability is present before reporting it. Don't make assumptions
8. Exit gracefully when the goal is achieved with a PoC of the found vulnerability

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
user_prompt = "Test the web app at http://localhost:65485/. DESCRIPTION: A simple IDOR vulnerability when updating the profile for a company, which allows a user to become an admin and see private jobs."

# Make the request with enhanced real-time feedback
print("\nAutonomous Bug Bounty Agent - Real-time Execution:")
print("=" * 60)

# Use non-streaming with automatic function calling (more reliable)
response = client.models.generate_content(
    model="gemini-2.5-flash",  # Using 2.5 Flash which supports thinking
    contents=user_prompt,
    config=config,
)

print("\n🧠 THINKING SUMMARY:")
print("-" * 50)

# Display thinking process and final answer
for part in response.candidates[0].content.parts:
    if not part.text:
        continue
    if part.thought:
        print(part.text)
        print("-" * 50)
    else:
        print("\n💡 FINAL ANALYSIS:")
        print("-" * 50)
        print(part.text)

# Display token usage information
if hasattr(response, 'usage_metadata'):
    print(f"\n📊 TOKEN USAGE:")
    print(f"Thinking tokens: {response.usage_metadata.thoughts_token_count}")
    print(f"Output tokens: {response.usage_metadata.candidates_token_count}")
    print(f"Total tokens: {response.usage_metadata.total_token_count}")

print("\n" + "=" * 60)