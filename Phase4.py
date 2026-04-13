import os
import subprocess

try:
    from openrouter import OpenRouter
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "The 'openrouter' package is not installed in this Python environment. "
        "Run: python -m pip install openrouter"
    ) from exc

from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(dotenv_path=".env", override=True)

# Read configuration from environment
api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
model_name = (os.getenv("OPENROUTER_MODEL") or "openrouter/free").strip()

# Agent loop settings
max_agent_steps = 6
max_command_chars = 3000
command_timeout_seconds = 20

format_reminder = (
    "Respond using exactly one line in one of these formats only:\n"
    "COMMAND: <command>\n"
    "DONE: <summary>"
)

if not api_key:
    raise ValueError(
        "OPENROUTER_API_KEY was not found. Check your .env file or environment variables."
    )

# Load system instructions from AGENT.md if present
try:
    with open("AGENT.md", "r", encoding="utf-8") as f:
        system_prompt = f.read()
except FileNotFoundError:
    system_prompt = (
        "Your goal is to complete the user's task.\n\n"
        "You must choose one of the following formats for every response:\n"
        "1. If a command needs to be executed, output `COMMAND: XXX`, where `XXX` is the command itself. "
        "Do not add any explanation or formatting.\n"
        "2. If no command is necessary, output `DONE: XXX`, where `XXX` is your final summary.\n\n"
        "Rules:\n"
        "- If the user asks for current or external information such as latest news, current prices, weather, "
        "recent events, or content from a website, do not answer from memory.\n"
        "- For those requests, first output a `COMMAND:` that fetches the needed information.\n"
        "- For recent news, prefer `curl` with a browser user agent against a news RSS feed. "
        "Google News RSS is acceptable.\n"
        "- Example news command:\n"
        "  `curl -L -A \"Mozilla/5.0\" "
        "\"https://news.google.com/rss/search?q=ASU&hl=en-US&gl=US&ceid=US:en\"`\n"
        "- After command output is returned to you, read it and then respond with `DONE:`.\n"
        "- Only use `DONE:` immediately when the task can be completed without running any command.\n"
        "- Keep your answer to a single line starting with `COMMAND:` or `DONE:` only."
    )


def execute_command(command: str) -> str:
    """
    Execute a shell command and return a formatted result string.
    Captures stdout/stderr, enforces a timeout, and truncates long output.
    """
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=command_timeout_seconds
        )

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()

        parts = []
        if stdout:
            parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            parts.append(f"STDERR:\n{stderr}")
        if not parts:
            parts.append("Command completed with no output.")

        result = "\n\n".join(parts)

    except subprocess.TimeoutExpired:
        result = (
            f"Error executing command: timed out after "
            f"{command_timeout_seconds} seconds."
        )
    except Exception as e:
        result = f"Error executing command: {str(e)}"

    if len(result) > max_command_chars:
        result = result[:max_command_chars] + "\n\n[output truncated]"

    return result


def main() -> None:
    with OpenRouter(api_key=api_key) as client:
        print(f"[System] Using model: {model_name}")
        print(f"[System] Loaded API key prefix: {api_key[:12]}...")

        # Persistent conversation history, compatible with Phase2/Phase3 style
        messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]

        while True:
            user_input = input("\nUser: ").strip()

            if user_input.lower() in {"exit", "quit"}:
                print("[System] Exiting.")
                break

            if not user_input:
                continue

            messages.append({"role": "user", "content": user_input})
            print("\n--- Agent Loop Started ---")

            for step in range(max_agent_steps):
                try:
                    response = client.chat.send(
                        model=model_name,
                        messages=messages
                    )
                except Exception as e:
                    print(f"[System] OpenRouter request failed: {e}")
                    print("--- Agent Loop Ended ---")
                    break

                reply = response.choices[0].message.content.strip()
                print(f"[AI] {reply}")

                # Store assistant reply in conversation history
                messages.append({"role": "assistant", "content": reply})

                if reply.startswith("DONE:"):
                    summary = reply[len("DONE:"):].strip()
                    print(f"\nAssistant: {summary}")
                    print("--- Agent Loop Ended ---")
                    break

                elif reply.startswith("COMMAND:"):
                    command = reply[len("COMMAND:"):].strip()

                    if not command:
                        error_msg = "Execution finished. Result: Error executing command: empty command."
                        print(f"[Agent] {error_msg}")
                        messages.append({"role": "user", "content": error_msg})
                        continue

                    command_result = execute_command(command)
                    execution_msg = f"Execution finished. Result: {command_result}"

                    print(f"[Agent] {execution_msg}")
                    messages.append({"role": "user", "content": execution_msg})

                else:
                    print("[System] Format error. Asking the model to retry.")
                    messages.append({"role": "user", "content": format_reminder})

            else:
                print("[System] Step limit reached. Ending this task to avoid an infinite loop.")
                print("--- Agent Loop Ended ---")


if __name__ == "__main__":
    main()