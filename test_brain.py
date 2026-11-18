import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Disable long 30-second SDK retries so fallback happens in 1 second
client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))
)

MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]

for model_name in MODELS:
    try:
        response = client.models.generate_content(
            model=model_name,
            contents="Reply with 'Brain online!' if you can hear me."
        )
        print(f"[{model_name}] {response.text}")
        break
    except Exception as e:
        print(f"[{model_name}] unavailable, trying next...")