import os
from fastapi import FastAPI, Request
import httpx
from google import genai

app = FastAPI()

# Load environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

@app.get("/")
def home():
    return {"status": "Gemini Telegram Bot Server is up and running!"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    """This endpoint receives messages sent from Telegram"""
    try:
        update = await request.json()
        
        if "message" in update and "text" in update["message"]:
            chat_id = update["message"]["chat"]["id"]
            user_text = update["message"]["text"]
            
            # Ignore commands like /start if desired
            if user_text.startswith("/"):
                return {"status": "ignored_command"}

            # 1. Ask Gemini
            ai_response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=user_text,
            )
            bot_reply = ai_response.text

            # 2. Reply to Telegram
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": bot_reply}
                )
                
    except Exception as e:
        print(f"Error handling request: {e}")

    return {"status": "ok"}
