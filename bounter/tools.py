"""Tool factory functions used by the Gemini agent."""

import subprocess
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - runtime import avoided
    from .reporting import ScanReport


def build_system_command_tool(
    report: "ScanReport", timeout: int = 30, verbose: bool = False
) -> Callable[[str], dict[str, Any]]:
    """Return a callable that executes system commands and logs results."""

    def execute_system_command_impl(command: str) -> dict[str, Any]:
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
                timeout=timeout,
            )

            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            if stdout:
                print(f"STDOUT:\n{stdout}")
            print(f" Return Code: {result.returncode}")
            print("-" * 40)

            payload = {
                "stdout": stdout,
                "stderr": stderr,
                "command_executed": command,
                "return_code": result.returncode,
                "success": True,
            }
            report.log_command(payload)
            return payload
        except subprocess.CalledProcessError as exc:
            stdout = exc.stdout.strip() if exc.stdout else ""
            stderr = exc.stderr.strip() if exc.stderr else ""
            print("❌ COMMAND FAILED:")
            print(f"Return Code: {exc.returncode}")
            if stdout:
                print(f"STDOUT: {stdout}")
            print("-" * 40)
            payload = {
                "stdout": stdout,
                "stderr": stderr,
                "command_executed": command,
                "return_code": exc.returncode,
                "error": str(exc),
                "success": False,
            }
            report.log_command(payload)
            return payload
        except subprocess.TimeoutExpired:
            print(f" COMMAND TIMED OUT after {timeout} seconds")
            print("-" * 40)
            payload = {
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
                "command_executed": command,
                "error": "Timeout",
                "success": False,
            }
            report.log_command(payload)
            return payload

    return execute_system_command_impl
