import os
try:
    from openrouter import OpenRouter
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "The 'openrouter' package is not installed in this Python environment. "
        "Run: python -m pip install openrouter"
    ) from exc

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env", override=True)

api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()

if not api_key:
    raise ValueError("OPENROUTER_API_KEY was not found. Check your .env file or environment variables.")

with OpenRouter(
    api_key=api_key
) as client:
    response = client.chat.send(
        model="openrouter/free",
        messages=[
            {"role": "user", "content": "Hello!"}
        ]
    )

print(response.choices[0].message.content)
