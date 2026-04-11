from openrouter import OpenRouter
import os
from dotenv import load_dotenv

load_dotenv()

with OpenRouter(
    api_key=os.getenv("OPENROUTER_API_KEY")
) as client:
    response = client.chat.send(
        model="openrouter/free",
        messages=[
            {"role": "user", "content": "Hello!"}
        ]
    )

print(response.choices[0].message.content)
