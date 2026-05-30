import os
import asyncio
from sanic import Sanic, response
from google import genai

app = Sanic("GeminiTelegramBot")

# Load environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

@app.post("/webhook")
async def handle_webhook(request):
    """This endpoint receives messages sent from Telegram"""
    update = request.json
    
    # Check if it's a standard text message
    if "message" in update and "text" in update["message"]:
        chat_id = update["message"]["chat"]["id"]
        user_text = update["message"]["text"]
        
        # Don't respond to commands like /start if you don't want to
        if user_text.startswith("/"):
            return response.json({"status": "ignored_command"})

        try:
            # 1. Ask Gemini
            ai_response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=user_text,
            )
            bot_reply = ai_response.text

            # 2. Reply to Telegram using their standard HTTP API
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": bot_reply}
                )
                
        except Exception as e:
            print(f"Error handling request: {e}")

    return response.json({"status": "ok"})

if __name__ == "__main__":
    # Render provides a PORT environment variable dynamically
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
