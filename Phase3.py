import os
import subprocess
from dotenv import load_dotenv
from openrouter import OpenRouter

load_dotenv(dotenv_path=".env", override=True)

# Initialize the client with your API key from .env
api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
model_name = os.getenv("OPENROUTER_MODEL", "openrouter/free")
max_agent_steps = 6
max_command_chars = 3000
format_reminder = (
    "Respond using exactly one line in one of these formats only:\n"
    "COMMAND: <command>\n"
    "DONE: <summary>"
)

if not api_key:
    raise ValueError("OPENROUTER_API_KEY was not found. Check your .env file or environment variables.")

try:
    with open("AGENT.md", "r", encoding="utf-8") as f:
        system_prompt = f.read()
except FileNotFoundError:
    system_prompt = (
        "Your goal is to complete the user's task.\n\n"
        "You must choose one of the following formats for every response:\n"
        "1. If a command needs to be executed, output `COMMAND: XXX`, where `XXX` is the command itself. Do not add any explanation or formatting.\n"
        "2. If no command is necessary, output `DONE: XXX`, where `XXX` is your final summary.\n\n"
        "Rules:\n"
        "- If the user asks for current or external information such as latest news, current prices, weather, recent events, or content from a website, do not answer from memory.\n"
        "- For those requests, first output a `COMMAND:` that fetches the needed information.\n"
        "- For recent news, prefer `curl` with a browser user agent against a news RSS feed. Google News RSS is acceptable.\n"
        "- Example news command:\n"
        "  `curl -L -A \"Mozilla/5.0\" \"https://news.google.com/rss/search?q=ASU&hl=en-US&gl=US&ceid=US:en\"`\n"
        "- After command output is returned to you, read it and then respond with `DONE:`.\n"
        "- Only use `DONE:` immediately when the task can be completed without running any command.\n"
        "- Keep your answer to a single line starting with `COMMAND:` or `DONE:` only."
    )

with OpenRouter(api_key=api_key) as client:
    print(f"[System] Using model: {model_name}")
    print(f"[System] Loaded API key prefix: {api_key[:12]}...")

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    while True:
        user_input = input("\n[T] ")
        if user_input.lower() in ["exit", "quit"]:
            break

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
                print("[System] If you see an auth error, the key is likely invalid, revoked, or not accepted by OpenRouter.")
                print("--- Agent Loop Ended ---")
                break

            reply = response.choices[0].message.content.strip()
            print(f"[AI] {reply}")

            if reply.startswith("DONE:"):
                messages.append({"role": "assistant", "content": reply})
                summary = reply.split("DONE:")[1].strip()
                print(f"\n[AI] Final Summary: {summary}")
                print("--- Agent Loop Ended ---")
                break
            elif reply.startswith("COMMAND:"):
                messages.append({"role": "assistant", "content": reply})
                command = reply.split("COMMAND:")[1].strip()

                try:
                    completed = subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=20
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
                    command_result = "\n\n".join(parts)
                except Exception as e:
                    command_result = f"Error executing command: {str(e)}"

                if len(command_result) > max_command_chars:
                    command_result = command_result[:max_command_chars] + "\n\n[output truncated]"

                content = f"Execution finished. Result: {command_result}"
                print(f"[Agent] {content}")
                messages.append({"role": "user", "content": content})
            else:
                print("[System] Format error. Asking the model to retry.")
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": format_reminder})
        else:
            print("[System] Step limit reached. Ending this task to avoid an infinite loop.")
            print("--- Agent Loop Ended ---")
