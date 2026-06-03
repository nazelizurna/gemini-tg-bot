**README**

### Telegram Gemini AI Assistant Bot

A personal AI assistant bot built with FastAPI, Google Gemini, and integrated tools for calendar management, reminders, and real-time web search.

### Features
- Natural conversation with memory (chat history stored in SQLite)
- Google Calendar integration (create events and list schedule)
- Proactive timed reminders via Telegram
- Real-time web search using DuckDuckGo
- Tool-calling support for Gemini to perform actions

### Requirements
- Python 3.9+
- Telegram Bot Token
- Gemini API Key
- Google Service Account JSON (with Calendar API access)

### Environment Variables
```env
TELEGRAM_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_SERVICE_ACCOUNT_JSON=your_service_account_json_as_string
```

### Setup & Run
1. Install dependencies:
   ```bash
   pip install fastapi uvicorn httpx google-genai google-api-python-client apscheduler duckduckgo-search
   ```

2. Initialize the database and run:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```

3. Set up webhook with Telegram:
   ```bash
   https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://yourdomain.com/webhook
   ```

The bot responds to normal messages in chats and uses tools when needed for calendar, reminders, or current information.
