import json
import os
import re
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

try:
    from openrouter import OpenRouter
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "The 'openrouter' package is not installed in this Python environment. "
        "Run: python -m pip install openrouter"
    ) from exc


# -----------------------------
# Environment and configuration
# -----------------------------
load_dotenv(dotenv_path=".env", override=True)

API_KEY = (os.getenv("OPENROUTER_API_KEY") or "").strip()
ALPHAVANTAGE_API_KEY = (os.getenv("ALPHAVANTAGE_API_KEY") or "").strip()
MODEL_NAME = (os.getenv("OPENROUTER_MODEL") or "openrouter/free").strip()

MAX_AGENT_STEPS = int(os.getenv("PHASE5_MAX_AGENT_STEPS", "8"))
MAX_COMMAND_CHARS = int(os.getenv("PHASE5_MAX_COMMAND_CHARS", "5000"))
COMMAND_TIMEOUT_SECONDS = int(os.getenv("PHASE5_COMMAND_TIMEOUT_SECONDS", "30"))
SHOW_DEBUG = (os.getenv("PHASE5_DEBUG", "0").strip() == "1")

if not API_KEY:
    raise ValueError(
        "OPENROUTER_API_KEY was not found. Check your .env file or environment variables."
    )

if not ALPHAVANTAGE_API_KEY:
    raise ValueError(
        "ALPHAVANTAGE_API_KEY was not found. Check your .env file or environment variables."
    )


# -----------------------------
# Directive parsing
# -----------------------------
DIRECTIVE_RE = re.compile(
    r"^\s*(COMMAND|DONE|ASK):\s*(.*?)\s*$",
    re.DOTALL,
)

FORMAT_REMINDER = (
    "Respond using exactly one directive and nothing else.\n"
    "Allowed formats:\n"
    "COMMAND: <single shell command>\n"
    "ASK: <single clarification question>\n"
    "DONE: <final answer>"
)


def parse_agent_output(text: str) -> dict[str, str]:
    """
    Accept exactly one directive for the full response.
    This prevents mixed outputs like 'COMMAND: ... DONE: ...'.
    """
    text = (text or "").strip()
    match = DIRECTIVE_RE.fullmatch(text)
    if not match:
        raise ValueError(FORMAT_REMINDER)

    directive, payload = match.groups()
    payload = payload.strip()

    if directive == "COMMAND" and not payload:
        raise ValueError("COMMAND directive was empty.")
    if directive in {"ASK", "DONE"} and not payload:
        raise ValueError(f"{directive} directive was empty.")

    return {"type": directive, "value": payload}


# -----------------------------
# Prompt loading
# -----------------------------
def _load_text_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def discover_instruction_files(base_dir: Path) -> list[Path]:
    """
    Load AGENT.md first, then one or more skill files.

    Priority:
    1. PHASE5_PROMPT_FILES env var, comma-separated filenames
    2. AGENT.md + common skill filenames if present
    3. AGENT.md + all other *.md files in the working directory
    """
    env_value = (os.getenv("PHASE5_PROMPT_FILES") or "").strip()
    if env_value:
        files: list[Path] = []
        for raw_name in env_value.split(","):
            name = raw_name.strip()
            if not name:
                continue
            files.append(base_dir / name)
        return files

    ordered_names = [
        "AGENT.md",
        "STOCKAGENT.md",
        "COMPARESKILL.md",
        "SKILL.md",
    ]

    files = [base_dir / name for name in ordered_names if (base_dir / name).exists()]
    if files:
        return files

    markdowns = sorted(base_dir.glob("*.md"))
    if not markdowns:
        return []

    agent = [p for p in markdowns if p.name.lower() == "agent.md"]
    rest = [p for p in markdowns if p.name.lower() != "agent.md"]
    return agent + rest


DEFAULT_SYSTEM_PROMPT = """Your goal is to complete the user's task.

You must respond with exactly one directive and nothing else:
COMMAND: <single shell command>
ASK: <single clarification question>
DONE: <final answer>

Rules:
- Use COMMAND when fresh or external information must be fetched.
- Use ASK when the request is ambiguous and a brief clarification is required.
- Use DONE only when you have enough information to answer.
- Never output more than one directive in the same reply.
- Keep responses machine-readable and concise.
"""


def build_stock_helper_instructions() -> str:
    """
    Extra instructions so the agent can handle more than just stock tickers.
    """
    return f"""
# STOCK_COMMAND_HELPERS

When the user asks about a company, stock, ticker, market data, or comparisons:

1. The user may provide:
   - a ticker, like MSFT
   - a company name, like Microsoft
   - a natural-language request, like:
     "compare Apple and Microsoft"
     "what is Nvidia's latest price"
     "find the ticker for Palantir"
     "show me recent stock data for Tesla"

2. If the ticker is unknown but the company name is known, first resolve it with Alpha Vantage SYMBOL_SEARCH:
COMMAND: curl --silent --show-error --location "https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords=<URL_ENCODED_NAME>&apikey={ALPHAVANTAGE_API_KEY}"

3. If the ticker is known and the user wants current quote data, use:
COMMAND: curl --silent --show-error --location "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=<TICKER>&apikey={ALPHAVANTAGE_API_KEY}"

4. If the ticker is known and the user wants company profile / sector / industry / fundamentals, use:
COMMAND: curl --silent --show-error --location "https://www.alphavantage.co/query?function=OVERVIEW&symbol=<TICKER>&apikey={ALPHAVANTAGE_API_KEY}"

5. If the user wants daily historical prices, use:
COMMAND: curl --silent --show-error --location "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol=<TICKER>&outputsize=compact&apikey={ALPHAVANTAGE_API_KEY}"

6. For multi-company comparisons:
   - resolve unknown company names to tickers first
   - then fetch quote/overview/daily data as needed
   - then summarize with DONE

7. Keep the conversation natural:
   - use ASK when clarification is truly needed
   - otherwise continue using context from prior turns

8. Do not assume the user only inputs tickers.
"""


def build_system_prompt(base_dir: Path) -> str:
    files = discover_instruction_files(base_dir)
    loaded_sections: list[str] = []

    for file_path in files:
        content = _load_text_file(file_path)
        if content:
            loaded_sections.append(f"# {file_path.name}\n{content.strip()}")

    loaded_sections.append(build_stock_helper_instructions())

    if loaded_sections:
        return "\n\n".join(loaded_sections)

    return DEFAULT_SYSTEM_PROMPT + "\n\n" + build_stock_helper_instructions()


# -----------------------------
# Command execution and output shaping
# -----------------------------
def truncate_text(text: str, max_chars: int = MAX_COMMAND_CHARS) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[output truncated]"


def try_format_json(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    if not (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    return truncate_text(pretty)


def try_format_feed(text: str) -> str | None:
    text = text.strip()
    if "<rss" not in text and "<feed" not in text:
        return None

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None

    items: list[str] = []

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title and link:
            items.append(f"- {title} | {link}")
        if len(items) >= 5:
            return "\n".join(items)

    atom_ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f".//{atom_ns}entry"):
        title = (entry.findtext(f"{atom_ns}title") or "").strip()
        link = ""
        for node in entry.findall(f"{atom_ns}link"):
            href = node.attrib.get("href", "").strip()
            if href:
                link = href
                break
        if title and link:
            items.append(f"- {title} | {link}")
        if len(items) >= 5:
            break

    if items:
        return "\n".join(items)
    return None


def try_format_alpha_vantage_json(text: str) -> str | None:
    """
    Make Alpha Vantage JSON easier for the model to read.
    """
    text = text.strip()
    if not text.startswith("{"):
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if "bestMatches" in data:
        matches = data.get("bestMatches", [])
        rows = []
        for item in matches[:5]:
            symbol = item.get("1. symbol", "")
            name = item.get("2. name", "")
            region = item.get("4. region", "")
            currency = item.get("8. currency", "")
            rows.append(f"- {symbol} | {name} | {region} | {currency}")
        return "\n".join(rows) if rows else truncate_text(json.dumps(data, indent=2))

    if "Global Quote" in data:
        quote = data.get("Global Quote", {})
        symbol = quote.get("01. symbol", "")
        price = quote.get("05. price", "")
        change = quote.get("09. change", "")
        change_pct = quote.get("10. change percent", "")
        latest_day = quote.get("07. latest trading day", "")
        return (
            f"Symbol: {symbol}\n"
            f"Price: {price}\n"
            f"Change: {change}\n"
            f"Change Percent: {change_pct}\n"
            f"Latest Trading Day: {latest_day}"
        ).strip()

    if "Symbol" in data and "Name" in data:
        lines = [
            f"Symbol: {data.get('Symbol', '')}",
            f"Name: {data.get('Name', '')}",
            f"Sector: {data.get('Sector', '')}",
            f"Industry: {data.get('Industry', '')}",
            f"MarketCapitalization: {data.get('MarketCapitalization', '')}",
            f"PERatio: {data.get('PERatio', '')}",
            f"EPS: {data.get('EPS', '')}",
            f"ProfitMargin: {data.get('ProfitMargin', '')}",
        ]
        return "\n".join(lines).strip()

    for key in ("Time Series (Daily)", "Weekly Adjusted Time Series", "Monthly Adjusted Time Series"):
        if key in data:
            series = data.get(key, {})
            rows = []
            for date, values in list(series.items())[:5]:
                open_price = values.get("1. open", "")
                close_price = values.get("4. close", "")
                adjusted_close = values.get("5. adjusted close", values.get("4. close", ""))
                rows.append(
                    f"- {date} | open={open_price} | close={close_price} | adjusted_close={adjusted_close}"
                )
            return "\n".join(rows) if rows else truncate_text(json.dumps(data, indent=2))

    return None


def format_command_output(stdout: str, stderr: str, returncode: int) -> str:
    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()

    formatted_stdout = (
        try_format_alpha_vantage_json(stdout)
        or try_format_json(stdout)
        or try_format_feed(stdout)
        or truncate_text(stdout)
    )

    parts: list[str] = [f"RETURN_CODE: {returncode}"]

    if formatted_stdout:
        parts.append(f"STDOUT:\n{formatted_stdout}")
    if stderr:
        parts.append(f"STDERR:\n{truncate_text(stderr)}")
    if not stdout and not stderr:
        parts.append("STDOUT:\nCommand completed with no output.")

    return "\n\n".join(parts)


def execute_command(command: str) -> str:
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return format_command_output(
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        details = format_command_output(stdout=stdout, stderr=stderr, returncode=-1)
        return (
            f"Error executing command: timed out after {COMMAND_TIMEOUT_SECONDS} seconds.\n\n"
            f"{details}"
        )
    except Exception as exc:
        return f"Error executing command: {exc}"


# -----------------------------
# OpenRouter interaction
# -----------------------------
def get_reply(client: OpenRouter, messages: list[dict[str, str]]) -> str:
    response = client.chat.send(
        model=MODEL_NAME,
        messages=messages,
    )
    return (response.choices[0].message.content or "").strip()


def print_banner(system_prompt: str, instruction_files: Iterable[Path]) -> None:
    print(f"[System] Using model: {MODEL_NAME}")
    print(f"[System] Loaded OpenRouter key prefix: {API_KEY[:12]}...")
    print(f"[System] Loaded Alpha Vantage key prefix: {ALPHAVANTAGE_API_KEY[:8]}...")
    if instruction_files:
        names = ", ".join(path.name for path in instruction_files)
        print(f"[System] Loaded prompt files: {names}")
    else:
        print("[System] No prompt files found. Using built-in prompt logic.")
    if SHOW_DEBUG:
        print(f"[Debug] System prompt length: {len(system_prompt)} characters")


def run_agent_loop(client: OpenRouter, messages: list[dict[str, str]]) -> None:
    print("\n--- Agent Loop Started ---")

    for step in range(1, MAX_AGENT_STEPS + 1):
        if SHOW_DEBUG:
            print(f"[Debug] Agent step {step}/{MAX_AGENT_STEPS}")

        try:
            reply = get_reply(client, messages)
        except Exception as exc:
            print(f"[System] OpenRouter request failed: {exc}")
            print("--- Agent Loop Ended ---")
            return

        print(f"[AI] {reply}")
        messages.append({"role": "assistant", "content": reply})

        try:
            parsed = parse_agent_output(reply)
        except ValueError:
            print("[System] Format error. Asking the model to retry.")
            messages.append({"role": "user", "content": FORMAT_REMINDER})
            continue

        directive = parsed["type"]
        payload = parsed["value"]

        if directive == "DONE":
            print(f"\nAssistant: {payload}")
            print("--- Agent Loop Ended ---")
            return

        if directive == "ASK":
            print(f"\nAssistant: {payload}")
            print("--- Agent Loop Ended ---")
            return

        command_result = execute_command(payload)
        execution_msg = f"Execution finished. Result:\n{command_result}"
        print(f"[Agent] {execution_msg}")
        messages.append({"role": "user", "content": execution_msg})

    print("[System] Step limit reached. Ending this task to avoid an infinite loop.")
    print("--- Agent Loop Ended ---")


# -----------------------------
# Entrypoint
# -----------------------------
def main() -> None:
    base_dir = Path.cwd()
    instruction_files = discover_instruction_files(base_dir)
    system_prompt = build_system_prompt(base_dir)

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": system_prompt,
        }
    ]

    with OpenRouter(api_key=API_KEY) as client:
        print_banner(system_prompt, instruction_files)
        print("[System] Type 'exit' or 'quit' to leave.")

        while True:
            try:
                user_input = input("\nUser: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[System] Exiting.")
                break

            if user_input.lower() in {"exit", "quit"}:
                print("[System] Exiting.")
                break

            if not user_input:
                continue

            messages.append({"role": "user", "content": user_input})
            run_agent_loop(client, messages)


if __name__ == "__main__":
    main()