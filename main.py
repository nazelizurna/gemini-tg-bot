import os
import json
from fastapi import FastAPI, Request
import httpx
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

app = FastAPI()

# Load environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Load your service account JSON file from Render environment variables
# (Instead of uploading the file to GitHub, we save its raw text to an Env Var for safety!)
SERVICE_ACCOUNT_INFO = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))

# Initialize Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- GOOGLE CALENDAR TOOL DEFINITION ---
def create_calendar_event(summary: str, start_time: str, end_time: str) -> str:
    """
    Creates a new event or meeting on the user's primary Google Calendar.
    
    Args:
        summary: The title, name, or description of the event or meeting.
        start_time: ISO 8601 formatted start string (e.g., '2026-06-01T10:00:00').
        end_time: ISO 8601 formatted end string (e.g., '2026-06-01T11:00:00').
    """
    try:
        scopes = ['https://www.googleapis.com/auth/calendar']
        creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=scopes)
        service = build('calendar', 'v3', credentials=creds)
        
        event = {
            'summary': summary,
            'start': {'dateTime': start_time, 'timeZone': 'UTC'},
            'end': {'dateTime': end_time, 'timeZone': 'UTC'},
        }
        
        # Use 'primary' because you shared your calendar directly with the service account
        service.events().insert(calendarId='primary', body=event).execute()
        return f"Successfully added '{summary}' to your calendar."
    except Exception as e:
        return f"Failed to add event due to error: {str(e)}"


@app.get("/")
def home():
    return {"status": "Gemini Calendar Bot is up!"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        update = await request.json()
        if "message" in update and "text" in update["message"]:
            chat_id = update["message"]["chat"]["id"]
            user_text = update["message"]["text"]
            
            if user_text.startswith("/"):
                return {"status": "ignored"}

            # Pass the calendar function into tools. Gemini handles execution!
            ai_response = ai_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=user_text,
                config=types.GenerateContentConfig(
                    tools=[create_calendar_event]
                )
            )
            bot_reply = ai_response.text

            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": bot_reply}
                )
    except Exception as e:
        print(f"Error: {e}")

    return {"status": "ok"}
