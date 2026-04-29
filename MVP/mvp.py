"""
Phase 6 Stock Agent Web App

This file wraps the working Phase 5 stock-agent backend in a small local web app.
It preserves the Phase 5 agent behavior as much as possible:
- persistent messages chat history per browser session while the app is running
- OpenRouter client
- default model: openrouter/free
- Alpha Vantage integration
- strict one-directive parser: COMMAND, ASK, DONE
- shell command execution with timeout and output formatting
- instruction-file discovery and loading
- natural-language stock requests

Run:
    python -m pip install flask python-dotenv openrouter
    python phase6.py

Then open:
    http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

try:
    from flask import Flask, jsonify, request, Response
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "The 'flask' package is not installed. Run: python -m pip install flask"
    ) from exc

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

HOST = (os.getenv("PHASE6_HOST") or "127.0.0.1").strip()
PORT = int(os.getenv("PHASE6_PORT", "8000"))

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
    This preserves the strict Phase 5 behavior and prevents mixed outputs like
    'COMMAND: ... DONE: ...'.
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


# -----------------------------
# Phase 6 session-aware agent wrapper
# -----------------------------
BASE_DIR = Path.cwd()
INSTRUCTION_FILES = discover_instruction_files(BASE_DIR)
SYSTEM_PROMPT = build_system_prompt(BASE_DIR)

# In-memory sessions are intentionally local and simple for Phase 6.
# Each browser session gets its own persistent messages list while the server runs.
_SESSIONS: dict[str, list[dict[str, str]]] = {}
_SESSIONS_LOCK = threading.Lock()


def new_messages() -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def get_session_messages(session_id: str) -> list[dict[str, str]]:
    with _SESSIONS_LOCK:
        if session_id not in _SESSIONS:
            _SESSIONS[session_id] = new_messages()
        return _SESSIONS[session_id]


def reset_session(session_id: str) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS[session_id] = new_messages()


def run_agent_turn(client: OpenRouter, messages: list[dict[str, str]], user_input: str) -> dict[str, str | int]:
    """
    Run one user turn through the existing Phase 5 command loop.

    Return a JSON-friendly result for the web UI.
    The messages list is mutated in-place to preserve conversation history.
    """
    messages.append({"role": "user", "content": user_input})

    for step in range(1, MAX_AGENT_STEPS + 1):
        if SHOW_DEBUG:
            print(f"[Debug] Agent step {step}/{MAX_AGENT_STEPS}")

        try:
            reply = get_reply(client, messages)
        except Exception as exc:
            return {
                "type": "error",
                "answer": f"OpenRouter request failed: {exc}",
                "steps": step,
            }

        if SHOW_DEBUG:
            print(f"[AI] {reply}")

        messages.append({"role": "assistant", "content": reply})

        try:
            parsed = parse_agent_output(reply)
        except ValueError:
            messages.append({"role": "user", "content": FORMAT_REMINDER})
            continue

        directive = parsed["type"]
        payload = parsed["value"]

        if directive == "DONE":
            return {"type": "done", "answer": payload, "steps": step}

        if directive == "ASK":
            return {"type": "ask", "answer": payload, "steps": step}

        command_result = execute_command(payload)
        execution_msg = f"Execution finished. Result:\n{command_result}"
        if SHOW_DEBUG:
            print(f"[Agent] {execution_msg}")
        messages.append({"role": "user", "content": execution_msg})

    return {
        "type": "error",
        "answer": "Step limit reached. Ending this task to avoid an infinite loop.",
        "steps": MAX_AGENT_STEPS,
    }


# -----------------------------
# Frontend
# -----------------------------
HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MVP Stock Agent</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f7fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d6ddea;
      --user: #e8f1ff;
      --assistant: #f6f7f9;
      --accent: #2454d6;
      --accent-2: #0f8f69;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, #dce8ff 0, transparent 32%),
        radial-gradient(circle at bottom right, #dff7ef 0, transparent 28%),
        var(--bg);
      color: var(--ink);
    }
    main {
      max-width: 980px;
      margin: 0 auto;
      padding: 32px 16px;
    }
    .shell {
      background: rgba(255,255,255,0.88);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 20px 60px rgba(23, 32, 51, 0.10);
      overflow: hidden;
    }
    header {
      padding: 22px 24px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.72);
    }
    h1 { margin: 0 0 6px; font-size: clamp(1.8rem, 4vw, 3rem); letter-spacing: -0.04em; }
    .subtitle { margin: 0; color: var(--muted); line-height: 1.45; }
    #chat {
      min-height: 430px;
      max-height: 60vh;
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .message {
      max-width: 84%;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 13px 15px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .message.user { align-self: flex-end; background: var(--user); }
    .message.assistant { align-self: flex-start; background: var(--assistant); }
    .message.system { align-self: center; background: #fff7e6; color: #7a4b00; max-width: 96%; }
    .role { display: block; font-size: 0.75rem; font-weight: 700; color: var(--muted); margin-bottom: 5px; text-transform: uppercase; letter-spacing: 0.06em; }
    .composer {
      border-top: 1px solid var(--line);
      padding: 16px;
      background: var(--panel);
    }
    textarea {
      width: 100%;
      min-height: 84px;
      resize: vertical;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      font: inherit;
      color: var(--ink);
      background: #fff;
      outline: none;
    }
    textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(36,84,214,0.12); }
    .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 12px; }
    button {
      border: 0;
      border-radius: 999px;
      padding: 11px 16px;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .primary { background: var(--accent); color: #fff; }
    .secondary { background: var(--accent-2); color: #fff; }
    .ghost { background: #eef2f7; color: var(--ink); }
    #status { margin-left: auto; color: var(--muted); font-size: 0.95rem; }
    @media (max-width: 640px) {
      main { padding: 12px; }
      .message { max-width: 96%; }
      #status { margin-left: 0; width: 100%; }
    }
  </style>
</head>
<body>
  <main>
    <section class="shell">
      <header>
        <h1>MVP Stock Agent</h1>
        <p class="subtitle">Local browser UI for your stock agent. Type or use browser voice input, then continue the same conversation.</p>
      </header>

      <div id="chat" aria-live="polite"></div>

      <section class="composer">
        <textarea id="prompt" placeholder="Try: compare Microsoft and Apple"></textarea>
        <div class="row">
          <button class="secondary" id="voiceBtn" type="button">🎙 Voice Input</button>
          <button class="primary" id="sendBtn" type="button">Send</button>
          <button class="ghost" id="newChatBtn" type="button">New Chat</button>
          <span id="status">Idle.</span>
        </div>
      </section>
    </section>
  </main>

  <script>
    const chatEl = document.getElementById("chat");
    const promptEl = document.getElementById("prompt");
    const statusEl = document.getElementById("status");
    const voiceBtn = document.getElementById("voiceBtn");
    const sendBtn = document.getElementById("sendBtn");
    const newChatBtn = document.getElementById("newChatBtn");

    let sessionId = localStorage.getItem("phase6_stock_session_id");
    if (!sessionId) {
      sessionId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
      localStorage.setItem("phase6_stock_session_id", sessionId);
    }

    function addMessage(role, text) {
      const el = document.createElement("div");
      el.className = "message " + role;
      const roleEl = document.createElement("span");
      roleEl.className = "role";
      roleEl.textContent = role;
      el.appendChild(roleEl);
      el.appendChild(document.createTextNode(text));
      chatEl.appendChild(el);
      chatEl.scrollTop = chatEl.scrollHeight;
    }

    addMessage("system", "Ready. This chat keeps its message history while the local server is running.");

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    let recognition = null;
    let listening = false;

    if (SpeechRecognition) {
      recognition = new SpeechRecognition();
      recognition.lang = "en-US";
      recognition.interimResults = true;
      recognition.continuous = false;

      recognition.onstart = () => {
        listening = true;
        voiceBtn.textContent = "Listening...";
        statusEl.textContent = "Listening for speech...";
      };

      recognition.onresult = (event) => {
        let transcript = "";
        for (const result of event.results) {
          transcript += result[0].transcript;
        }
        promptEl.value = transcript.trim();
      };

      recognition.onerror = (event) => {
        statusEl.textContent = "Voice error: " + event.error;
      };

      recognition.onend = () => {
        listening = false;
        voiceBtn.textContent = "🎙 Voice Input";
        statusEl.textContent = promptEl.value.trim() ? "Voice captured." : "Idle.";
      };
    } else {
      voiceBtn.disabled = true;
      statusEl.textContent = "Voice input is not supported in this browser. Use Chrome or Edge.";
    }

    voiceBtn.addEventListener("click", () => {
      if (!recognition) return;
      if (listening) {
        recognition.stop();
        return;
      }
      recognition.start();
    });

    async function sendMessage() {
      const message = promptEl.value.trim();
      if (!message) {
        statusEl.textContent = "Enter or speak a stock question first.";
        return;
      }

      promptEl.value = "";
      addMessage("user", message);
      statusEl.textContent = "Running agent...";
      sendBtn.disabled = true;
      voiceBtn.disabled = true;

      try {
        const response = await fetch("/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, message })
        });

        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Request failed.");
        }

        addMessage("assistant", data.answer || "");
        statusEl.textContent = data.type === "ask" ? "Clarification requested." : "Done.";
      } catch (error) {
        addMessage("system", String(error));
        statusEl.textContent = "Error.";
      } finally {
        sendBtn.disabled = false;
        voiceBtn.disabled = !recognition;
        promptEl.focus();
      }
    }

    sendBtn.addEventListener("click", sendMessage);

    promptEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
      }
    });

    newChatBtn.addEventListener("click", async () => {
      statusEl.textContent = "Resetting chat...";
      try {
        await fetch("/reset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId })
        });
        chatEl.innerHTML = "";
        addMessage("system", "New chat started. Message history has been reset for this browser session.");
        statusEl.textContent = "Idle.";
      } catch (error) {
        addMessage("system", String(error));
        statusEl.textContent = "Error.";
      }
    });
  </script>
</body>
</html>
"""


# -----------------------------
# Flask app
# -----------------------------
app = Flask(__name__)

# Reuse one OpenRouter client context while the app runs.
# Calls are guarded by a lock so concurrent browser clicks do not interleave one session's agent loop.
_CLIENT = OpenRouter(api_key=API_KEY)
_CLIENT_LOCK = threading.Lock()


@app.get("/")
def index() -> Response:
    return Response(HTML, mimetype="text/html")


@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    session_id = str(data.get("session_id", "")).strip() or str(uuid.uuid4())

    if not message:
        return jsonify({"error": "Message is required."}), 400

    messages = get_session_messages(session_id)

    with _CLIENT_LOCK:
        result = run_agent_turn(_CLIENT, messages, message)

    status = 200 if result.get("type") in {"done", "ask"} else 500
    return jsonify({"session_id": session_id, **result}), status


@app.post("/reset")
def reset():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id", "")).strip() or str(uuid.uuid4())
    reset_session(session_id)
    return jsonify({"session_id": session_id, "status": "reset"})


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "model": MODEL_NAME,
            "prompt_files": [path.name for path in INSTRUCTION_FILES],
            "sessions": len(_SESSIONS),
        }
    )


# -----------------------------
# Entrypoint
# -----------------------------
def main() -> None:
    print_banner(SYSTEM_PROMPT, INSTRUCTION_FILES)
    print(f"[System] Serving http://{HOST}:{PORT}")
    print("[System] Press Ctrl+C to stop.")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            _CLIENT.close()
        except Exception:
            pass
