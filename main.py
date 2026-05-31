import os
import json
import sqlite3
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
import httpx
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler

# --- DATABASE SETUP (MEMORY & TASKS) ---
DB_FILE = "bot_memory.db"

def init_db():
    """Initializes the SQLite tables for storing chat context logs and raw data."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Table for context memory
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_message(chat_id: int, role: str, content: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
    conn.commit()
    conn.close()

def get_recent_history(chat_id: int, limit=15):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role, content FROM chat_history 
        WHERE chat_id = ? 
        ORDER BY timestamp DESC LIMIT ?
    """, (chat_id, limit))
    rows = cursor.fetchall()
    conn.close()
    # Reverse it so it's in chronological order for Gemini
    return [{"role": row[0], "text": row[1]} for row in reversed(rows)]


# --- PROACTIVE REMINDER SERVICE ---
scheduler = BackgroundScheduler()

async def send_async_telegram_reminder(chat_id: int, task_text: str):
    """Fired by scheduler clock to send an alert directly to the user."""
    token = os.environ.get("TELEGRAM_TOKEN")
    alert_msg = f"⏰ **REMINDER:** {task_text}"
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": alert_msg, "parse_mode": "Markdown"}
        )

def trigger_reminder_job(chat_id: int, task_text: str):
    """Bridge sync worker for APScheduler thread execution safety."""
    import asyncio
    asyncio.run(send_async_telegram_reminder(chat_id, task_text))


# --- FASTAPI LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SERVICE_ACCOUNT_INFO = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))

ai_client = genai.Client(api_key=GEMINI_API_KEY)


# --- TOOLS DEFINITIONS ---

def get_calendar_service():
    scopes = ['https://www.googleapis.com/auth/calendar']
    creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=scopes)
    return build('calendar', 'v3', credentials=creds)

def create_calendar_event(summary: str, start_time: str, end_time: str) -> str:
    """Creates a new event or meeting on the user's primary Google Calendar."""
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'start': {'dateTime': start_time, 'timeZone': 'UTC'},
            'end': {'dateTime': end_time, 'timeZone': 'UTC'},
        }
        service.events().insert(calendarId='primary', body=event).execute()
        return f"SUCCESS: Added '{summary}' to your calendar."
    except Exception as e:
        return f"ERROR: Failed to write event: {str(e)}"

def list_calendar_events(time_min: str, time_max: str) -> str:
    """Retrieves a list of scheduled events from Google Calendar between two ISO strings."""
    try:
        service = get_calendar_service()
        events_result = service.events().list(
            calendarId='primary', timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if not events: return "No events found for this time period."
        return "Events:\n" + "\n".join([f"- {e['summary']} ({e['start'].get('dateTime', e['start'].get('date'))})" for e in events])
    except Exception as e:
        return f"ERROR: Failed to read schedule: {str(e)}"

def set_proactive_reminder(chat_id: int, task: str, delay_minutes: int) -> str:
    """
    Schedules an alarm or reminder alert that pings the user after a relative time delay.
    
    Args:
        chat_id: The specific chat room routing id (Must pass the chat_id from user metadata context).
        task: What the user wants to be reminded of.
        delay_minutes: The integer count of minutes to wait before triggering the alarm.
    """
    try:
        run_time = datetime.now() + timedelta(minutes=int(delay_minutes))
        scheduler.add_job(
            trigger_reminder_job,
            'date',
            run_date=run_time,
            args=[chat_id, task]
        )
        return f"SUCCESS: Alarm registered for '{task}' in {delay_minutes} minutes (Target: {run_time.strftime('%I:%M %p')})."
    except Exception as e:
        return f"ERROR: Scheduler initialization failed: {str(e)}"


# --- WEBHOOK INTERCEPTOR ---

@app.get("/")
def home():
    return {"status": "Super Bot with memory and reminders is fully functional!"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        update = await request.json()
        if "message" in update and "text" in update["message"]:
            chat_id = update["message"]["chat"]["id"]
            user_text = update["message"]["text"]
            
            if user_text.startswith("/"):
                return {"status": "ignored"}

            # Save incoming text to SQLite database
            save_message(chat_id=chat_id, role="user", content=user_text)
            
            # Retrieve historical conversation thread to construct working context
            past_history = get_recent_history(chat_id=chat_id, limit=10)
            
            current_time_str = datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
            
            system_instruction = (
                f"You are an optimized personal assistant. Today is: {current_time_str}.\n"
                f"Your active conversational Telegram chat_id context is: {chat_id}.\n\n"
                "When a user asks to be reminded about something in a relative window (e.g. 'in 10 minutes'), "
                f"you MUST call `set_proactive_reminder` and pass the chat_id variable as {chat_id}.\n\n"
                "Available capabilities:\n"
                "- `Calendar` & `list_calendar_events` for Google Calendar management.\n"
                "- `set_proactive_reminder` to drop timed tracking push notifications.\n\n"
                "Maintain clean conversation flow. If the user doesn't mention an automation task, just chat naturally."
            )

            # Build structural Gemini contents payload using history data
            formatted_contents = []
            for msg in past_history:
                formatted_contents.append(
                    types.Content(role=msg["role"], parts=[types.Part.from_text(text=msg["text"])])
                )
            
            # Initialize automated tools orchestrator
            chat = ai_client.chats.create(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[create_calendar_event, list_calendar_events, set_proactive_reminder],
                ),
                history=formatted_contents[:-1] # Seed all except the brand new user input message
            )

            # Send input through context mesh
            response = chat.send_message(user_text)
            bot_reply = response.text

            if not bot_reply:
                bot_reply = "Request executed successfully."

            # Save assistant reply to memory database
            save_message(chat_id=chat_id, role="model", content=bot_reply)

            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": bot_reply}
                )
    except Exception as e:
        print(f"CRITICAL COMPILATION EXCEPTION: {e}")

    return {"status": "ok"}
