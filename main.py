import os
import json
from datetime import datetime
from fastapi import FastAPI, Request
import httpx
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

app = FastAPI()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SERVICE_ACCOUNT_INFO = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))

# Initialize the official Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- GOOGLE CALENDAR TOOL ---
def create_calendar_event(summary: str, start_time: str, end_time: str) -> str:
    """
    Creates a new event or meeting on the user's primary Google Calendar.
    
    Args:
        summary: The title, name, or description of the event.
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
        
        service.events().insert(calendarId='primary', body=event).execute()
        return f"SUCCESS: System added '{summary}' to the calendar."
    except Exception as e:
        return f"ERROR: Could not add event due to error: {str(e)}"


@app.get("/")
def home():
    return {"status": "Smart Gemini Bot is fully functional!"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        update = await request.json()
        if "message" in update and "text" in update["message"]:
            chat_id = update["message"]["chat"]["id"]
            user_text = update["message"]["text"]
            
            if user_text.startswith("/"):
                return {"status": "ignored_command"}

            # Inject the real-world timestamp so it isn't lost in time
            current_time_str = datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
            
            system_instruction = (
                "You are an intelligent, articulate AI assistant with direct access to the user's Google Calendar.\n"
                f"The exact current date and time is: {current_time_str}.\n"
                "You have two modes of operation:\n"
                "1. General Chat: If the user asks a normal question, chat naturally, be smart, witty, and helpful.\n"
                "2. Calendar Management: If they ask to schedule or add an event, you MUST run the `Calendar` tool. "
                "After the tool returns the result, read the output and confirm it back to the user in a nice text message."
            )

            # We use `chats.create` with an automatic configuration. 
            # This config tells the SDK: "If Gemini wants to run a tool, execute the python code automatically,
            # feed the results back to Gemini, and give me the final conversational text."
            chat = ai_client.chats.create(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[create_calendar_event],
                )
            )

            # Send the message and get the true text response
            response = chat.send_message(user_text)
            bot_reply = response.text

            # Safety fallback in case text is blank
            if not bot_reply:
                bot_reply = "I processed your request, but I couldn't generate a text response. Please check your calendar!"

            # Send the clean response back to Telegram
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": bot_reply}
                )
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")

    return {"status": "ok"}
