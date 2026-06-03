This is a Telegram bot built with Sanic that forwards user messages to Gemini AI (gemini-2.5-flash) and replies with the generated response. Mail purpose is Google Calendar management.

**Setup**
1. Set environment variables: `TELEGRAM_TOKEN` and `GEMINI_API_KEY`
2. Deploy the app (Render or any platform supporting Sanic)
3. Configure Telegram webhook to point to your `/webhook` endpoint

**Features**
- Receives messages via POST /webhook
- Ignores commands starting with /
- Uses Google Gemini API for responses
- Sends replies via Telegram Bot API

**Dependencies**
- sanic
- google-genai
- httpx
- asyncio

Run with: `main app.py`
