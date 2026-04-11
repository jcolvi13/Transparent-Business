from openrouter import OpenRouter
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env", override=True)

api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()

if not api_key:
    raise ValueError("OPENROUTER_API_KEY was not found. Check your .env file or environment variables.")

with OpenRouter(
    api_key=api_key
) as client:
    while True:
        user_input = input("\nUser: ")
        response = client.chat.send(
            model="openrouter/free",
            messages=[
                {"role": "user", "content": user_input}
            ]
        )
        print(response.choices[0].message.content)
