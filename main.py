import os
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
import httpx
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler

# --- TARGET CALENDAR ROUTING ---
USER_CALENDAR_EMAIL = "nazzzzeli@gmail.com" 

# --- DATABASE SETUP (MEMORY & TASKS) ---
DB_FILE = "bot_memory.db"

def init_db():
    """Initializes the SQLite tables for storing chat context logs."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
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

# Initialize the official Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)


# --- GOOGLE CALENDAR TOOLS ---

def get_calendar_service():
    scopes = ['https://www.googleapis.com/auth/calendar']
    creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=scopes)
    return build('calendar', 'v3', credentials=creds)

def create_calendar_event(summary: str, start_time: str, end_time: str) -> str:
    """
    Creates a new event or meeting on the user's primary Google Calendar.
    
    Args:
        summary: The title, name, or description of the event.
        start_time: ISO 8601 formatted start string (e.g., '2026-06-01T10:00:00-03:00').
        end_time: ISO 8601 formatted end string (e.g., '2026-06-01T11:00:00-03:00').
    """
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
        }
        service.events().insert(calendarId=USER_CALENDAR_EMAIL, body=event).execute()
        return f"SUCCESS: Added '{summary}' to your calendar."
    except Exception as e:
        return f"ERROR: Failed to write event: {str(e)}"

def list_calendar_events(time_min: str, time_max: str) -> str:
    """
    Retrieves a list of scheduled events from Google Calendar between two strict ISO 8601 timestamps.
    
    Args:
        time_min: Start window formatted as ISO 8601 string (e.g., '2026-05-31T00:00:00Z').
        time_max: End window formatted as ISO 8601 string (e.g., '2026-05-31T23:59:59Z').
    """
    try:
        service = get_calendar_service()
        events_result = service.events().list(
            calendarId=USER_CALENDAR_EMAIL, 
            timeMin=time_min, 
            timeMax=time_max,
            singleEvents=True, 
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        if not events: 
            return f"No events found on your calendar between {time_min} and {time_max}."
            
        lines = []
        for e in events:
            start = e['start'].get('dateTime', e['start'].get('date'))
            lines.append(f"- {e['summary']} ({start})")
            
        return "Events found:\n" + "\n".join(lines)
    except Exception as e:
        return f"ERROR: Failed to read schedule: {str(e)}"

def set_proactive_reminder(chat_id: int, task: str, delay_minutes: int) -> str:
    """
    Schedules an alarm or reminder alert that pings the user after a relative time delay in minutes.
    
    Args:
        chat_id: The specific chat room routing id.
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
        return f"SUCCESS: Alarm registered for '{task}' in {delay_minutes} minutes."
    except Exception as e:
        return f"ERROR: Scheduler initialization failed: {str(e)}"


# --- WEBHOOK INTERCEPTOR ---

@app.get("/")
def home():
    return {"status": "Complete Gemini Agent is active!"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        update = await request.json()
        if "message" in update and "text" in update["message"]:
            chat_id = update["message"]["chat"]["id"]
            user_text = update["message"]["text"]
            
            if user_text.startswith("/"):
                return {"status": "ignored"}

            # Save to SQLite local memory
            save_message(chat_id=chat_id, role="user", content=user_text)
            
            # Fetch contextual history thread
            past_history = get_recent_history(chat_id=chat_id, limit=10)
            
            # Parse localized server time strings to anchoring contexts
            now_local = datetime.now()
            current_time_str = now_local.strftime("%A, %B %d, %Y %I:%M %p")
            iso_now = now_local.isoformat()
            
            system_instruction = (
                f"You are a highly intelligent personal assistant. The user's exact current local time is: {current_time_str}.\n"
                f"The current ISO 8601 reference timestamp is: {iso_now}.\n"
                f"Your active Telegram chat_id is: {chat_id}.\n\n"
                "CRITICAL OPERATION MATRIX:\n"
                "1. If asked about scheduled events, checking availability, or plans for ANY day/date (including today, tomorrow, or any specific date in 2026), you MUST invoke `list_calendar_events`.\n"
                "2. When generating boundaries for `list_calendar_events`, calculate strict ISO strings matching the user's current calendar year and day profiles.\n"
                "3. If asked to remind the user about an activity relative to right now, call `set_proactive_reminder` using the active chat_id integer.\n"
                "4. Always present returned verification output parameters as conversational message updates."
            )

            # Reconstruct thread history components
            formatted_contents = []
            for msg in past_history:
                formatted_contents.append(
                    types.Content(role=msg["role"], parts=[types.Part.from_text(text=msg["text"])])
                )
            
            # Fire structural orchestration engine
            chat = ai_client.chats.create(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[create_calendar_event, list_calendar_events, set_proactive_reminder],
                ),
                history=formatted_contents[:-1]
            )

            response = chat.send_message(user_text)
            bot_reply = response.text

            if not bot_reply:
                bot_reply = "Request processed successfully."

            # Commit model reaction down to local session memory
            save_message(chat_id=chat_id, role="model", content=bot_reply)

            # Route back out to Telegram client UI
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": chat_id, "text": bot_reply}
                )
    except Exception as e:
        print(f"CRITICAL DESYNC EXCEPTION: {e}")

    return {"status": "ok"}
