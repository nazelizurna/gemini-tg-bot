import os
import json
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
import httpx
from google import genai
from google.genai import types
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler
from duckduckgo_search import DDGS

# --- TARGET CALENDAR ROUTING ---
USER_CALENDAR_EMAIL = "nazzzzeli@gmail.com"

# --- DATABASE SETUP (MEMORY & TASKS) ---
DB_FILE = "bot_memory.db"

def init_db():
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

async def save_message_async(chat_id: int, role: str, content: str):
    """Async wrapper for saving messages"""
    await asyncio.to_thread(save_message, chat_id, role, content)

def save_message(chat_id: int, role: str, content: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_history (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
    conn.commit()
    conn.close()

async def get_recent_history_async(chat_id: int, limit=15):
    """Async wrapper"""
    return await asyncio.to_thread(get_recent_history, chat_id, limit)

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

def send_telegram_reminder_sync(chat_id: int, task_text: str):
    """Synchronous version for APScheduler"""
    token = os.environ.get("TELEGRAM_TOKEN")
    alert_msg = f"⏰ **REMINDER:** {task_text}"
    try:
        with httpx.Client() as client:
            client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": alert_msg, "parse_mode": "Markdown"},
                timeout=10
            )
    except Exception as e:
        print(f"Failed to send reminder: {e}")

def trigger_reminder_job(chat_id: int, task_text: str):
    send_telegram_reminder_sync(chat_id, task_text)

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

# --- AUTOMATION TOOLS (with good docstrings for Gemini) ---

def get_calendar_service():
    scopes = ['https://www.googleapis.com/auth/calendar']
    creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=scopes)
    return build('calendar', 'v3', credentials=creds)

def create_calendar_event(summary: str, start_time: str, end_time: str) -> str:
    """Creates a new event or meeting on the user's primary Google Calendar.
    
    Args:
        summary: Short title/description of the event (e.g. "Team meeting with John")
        start_time: Start time in ISO 8601 format with timezone, e.g. '2026-06-04T14:00:00+03:00'
        end_time: End time in ISO 8601 format with timezone, e.g. '2026-06-04T15:00:00+03:00'
    """
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'start': {'dateTime': start_time},
            'end': {'dateTime': end_time},
        }
        service.events().insert(calendarId=USER_CALENDAR_EMAIL, body=event).execute()
        return f"SUCCESS: Added '{summary}' to your calendar from {start_time} to {end_time}."
    except Exception as e:
        return f"ERROR: Failed to create calendar event: {str(e)}"

def list_calendar_events(time_min: str, time_max: str) -> str:
    """Retrieves a list of scheduled events from Google Calendar between two ISO 8601 timestamps.
    
    Args:
        time_min: Start of range in ISO 8601 (e.g. '2026-06-03T00:00:00+03:00')
        time_max: End of range in ISO 8601 (e.g. '2026-06-10T23:59:59+03:00')
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
            summary = e.get('summary', 'No title')
            lines.append(f"- {summary} at {start}")
        
        return "Events found:\n" + "\n".join(lines)
    except Exception as e:
        return f"ERROR: Failed to read calendar: {str(e)}"

def set_proactive_reminder(chat_id: int, task: str, delay_minutes: int) -> str:
    """Schedules a reminder that will be sent to the user after a delay.
    
    Args:
        chat_id: The Telegram chat ID to send the reminder to
        task: Description of what to remind about
        delay_minutes: Minutes from now to send the reminder
    """
    try:
        run_time = datetime.now() + timedelta(minutes=int(delay_minutes))
        scheduler.add_job(
            trigger_reminder_job,
            'date',
            run_date=run_time,
            args=[chat_id, task]
        )
        return f"SUCCESS: Reminder set for '{task}' in {delay_minutes} minutes."
    except Exception as e:
        return f"ERROR: Failed to set reminder: {str(e)}"

# --- NEW FREE SEARCH TOOL ---
def search_the_live_web(query: str) -> str:
    """
    Searches the live internet for up-to-date real-time information using DuckDuckGo.
    
    Args:
        query: Clear search query (e.g. "current weather in Istanbul", "latest news about AI", "Python asyncio best practices")
    """
    try:
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=4)]
            if not results:
                return "No real-time search results found."
            
            snippets = []
            for r in results:
                snippets.append(f"**{r.get('title', 'No title')}**\n{r.get('body', '')}\nSource: {r.get('href', '')}")
            return "\n\n".join(snippets)
    except Exception as e:
        return f"ERROR during web search: {str(e)}"

# --- TOOL EXECUTION HELPER (Manual loop for reliability) ---
async def execute_tool(tool_name: str, args: dict) -> str:
    """Execute one of the available tools"""
    tool_map = {
        "create_calendar_event": create_calendar_event,
        "list_calendar_events": list_calendar_events,
        "set_proactive_reminder": set_proactive_reminder,
        "search_the_live_web": search_the_live_web,
    }
    
    if tool_name not in tool_map:
        return f"ERROR: Unknown tool {tool_name}"
    
    func = tool_map[tool_name]
    
    try:
        # Special handling for chat_id in reminder
        if tool_name == "set_proactive_reminder":
            # chat_id will be passed from context
            return func(**args)
        else:
            return func(**args)
    except Exception as e:
        return f"ERROR executing {tool_name}: {str(e)}"

# --- WEBHOOK INTERCEPTOR ---
@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        update = await request.json()
        
        if "message" not in update or "text" not in update["message"]:
            return {"status": "ignored"}
        
        chat_id = update["message"]["chat"]["id"]
        user_text = update["message"]["text"]
        
        if user_text.startswith("/"):
            return {"status": "ignored"}
        
        # Save user message
        await save_message_async(chat_id=chat_id, role="user", content=user_text)
        
        # Get recent history
        past_history = await get_recent_history_async(chat_id=chat_id, limit=12)
        
        # Current time context
        now_local = datetime.now()
        current_time_str = now_local.strftime("%A, %B %d, %Y %I:%M %p")
        iso_now = now_local.isoformat()
        
        system_instruction = f"""You are a highly capable personal AI assistant with access to tools.

Current local time for the user: {current_time_str}
Current ISO timestamp: {iso_now}
Active Telegram chat_id: {chat_id}

AVAILABLE TOOLS (use them via function calls when needed):
1. create_calendar_event — for adding events/meetings to Google Calendar
2. list_calendar_events — for checking the user's schedule
3. set_proactive_reminder — for setting timed reminders/alarms
4. search_the_live_web — for real-time info, news, weather, facts, sports, etc.

CRITICAL RULES:
- Always use the correct current time context when creating events or reminders.
- For calendar events, use full ISO 8601 datetime strings with timezone offset.
- When the user asks about their schedule, upcoming events, or "what do I have today", use list_calendar_events.
- For reminders like "remind me in 30 minutes to...", use set_proactive_reminder.
- For anything requiring up-to-date information, use search_the_live_web.
- Be helpful, concise, and confirm actions clearly to the user.
"""
        
        # Build history for Gemini
        formatted_contents = []
        for msg in past_history:
            role = "model" if msg["role"] == "model" else "user"
            formatted_contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=msg["text"])])
            )
        
        # Create chat session
        chat = ai_client.chats.create(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=[
                    create_calendar_event,
                    list_calendar_events,
                    set_proactive_reminder,
                    search_the_live_web,
                ],
                # Enable automatic function calling if supported by your SDK version
                # automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False)
            ),
            history=formatted_contents
        )
        
        # Send message and handle potential tool calls
        response = chat.send_message(user_text)
        
        # Simple loop to handle tool calls (works even without automatic mode)
        max_tool_rounds = 5
        rounds = 0
        
        while rounds < max_tool_rounds:
            # Check if model wants to call tools
            if response.candidates and response.candidates[0].content.parts:
                function_calls = []
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        function_calls.append(part.function_call)
                
                if function_calls:
                    # Execute tools and send results back
                    tool_responses = []
                    for fc in function_calls:
                        tool_name = fc.name
                        args = dict(fc.args) if fc.args else {}
                        
                        # Inject chat_id for reminder tool
                        if tool_name == "set_proactive_reminder":
                            args["chat_id"] = chat_id
                        
                        print(f"[TOOL CALL] {tool_name} with args: {args}")
                        result = await asyncio.to_thread(execute_tool, tool_name, args)
                        
                        tool_responses.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": result}
                            )
                        )
                    
                    # Send tool results back to model
                    response = chat.send_message(tool_responses)
                    rounds += 1
                    continue
            
            # No more tool calls — we have final text
            break
        
        # Get final reply
        bot_reply = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text') and part.text:
                    bot_reply += part.text
        
        if not bot_reply.strip():
            bot_reply = "I've processed your request using the available tools."
        
        # Save bot response
        await save_message_async(chat_id=chat_id, role="model", content=bot_reply)
        
        # Send to Telegram
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": bot_reply},
                timeout=10
            )
    
    except Exception as e:
        print(f"CRITICAL ERROR in webhook: {e}")
        import traceback
        traceback.print_exc()
    
    return {"status": "ok"}

# Optional: Health check
@app.get("/")
async def health():
    return {"status": "running", "service": "Telegram Gemini Bot"}
