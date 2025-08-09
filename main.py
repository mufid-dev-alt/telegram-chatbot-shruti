import os
import json
import logging
import asyncio
import uuid
import datetime
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

import requests
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import firebase_admin
from firebase_admin import credentials, initialize_app, firestore, auth
from firebase_admin.auth import get_auth
from firebase_admin.firestore import client

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = os.getenv("GEMINI_API_URL")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
FIREBASE_CONFIG = os.getenv("__firebase_config")
INITIAL_AUTH_TOKEN = os.getenv("__initial_auth_token")
APP_ID = os.getenv("__app_id")

# Validate required environment variables
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is required")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is required")
if not GEMINI_API_URL:
    raise ValueError("GEMINI_API_URL environment variable is required")
if not FIREBASE_CONFIG:
    raise ValueError("__firebase_config environment variable is required")
if not APP_ID:
    raise ValueError("__app_id environment variable is required")

# Initialize Firebase
try:
    if not firebase_admin._apps:
        cred = credentials.Certificate(json.loads(FIREBASE_CONFIG))
        initialize_app(cred)
        logger.info("Firebase initialized.")
    db = firestore.client()
    auth_instance = get_auth()
except Exception as e:
    logger.error(f"Failed to initialize Firebase: {e}")
    raise

# Global variables
current_user_id = None
bot_username = None
bot_id = None

# Load users.json
users_data = {}
try:
    with open("users.json", "r", encoding="utf-8") as f:
        users_data = json.load(f)
    logger.info(f"Loaded users.json with {len(users_data)} entries")
except FileNotFoundError:
    logger.warning("users.json not found, will use first_name fallback")
except json.JSONDecodeError:
    logger.error("Invalid JSON in users.json, will use first_name fallback")

async def auth_and_set_user():
    """Set up Firebase authentication"""
    global current_user_id
    try:
        if INITIAL_AUTH_TOKEN:
            decoded_token = auth_instance.verify_id_token(INITIAL_AUTH_TOKEN)
            current_user_id = decoded_token['uid']
            logger.info(f"Signed in with custom token: {current_user_id}")
        else:
            current_user_id = "anonymous_user_" + str(uuid.uuid4())
            logger.info(f"Using anonymous user ID: {current_user_id}")
    except Exception as e:
        logger.error(f"Firebase authentication failed: {e}")
        current_user_id = "anonymous_user_" + str(uuid.uuid4())

def get_user_name(user) -> str:
    """Get user's real name from users.json or fallback to first_name"""
    if not user.username:
        return user.first_name or "Unknown"
    
    username_lower = user.username.lower()
    return users_data.get(username_lower, user.first_name or "Unknown")

def should_respond(update: Update) -> bool:
    """Check if bot should respond to this message"""
    if not update.message:
        return False
    
    message = update.message
    bot_mention = f"@{bot_username}" if bot_username else None
    
    # Ignore commands
    if message.text and message.text.startswith('/'):
        return False
    
    # Check for mention
    if bot_mention and message.text and bot_mention.lower() in message.text.lower():
        logger.info(f"Bot mentioned by {message.from_user.username or message.from_user.first_name}")
        return True
    
    # Check for reply to bot's message
    if message.reply_to_message and message.reply_to_message.from_user.id == bot_id:
        logger.info(f"User replied to bot's message: {message.from_user.username or message.from_user.first_name}")
        return True
    
    return False

async def get_chat_history(chat_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve recent chat history from Firestore"""
    try:
        collection_path = f"artifacts/{APP_ID}/public/data/telegram_chat_history/{chat_id}/messages"
        messages_ref = db.collection(collection_path)
        
        # Get recent messages ordered by timestamp
        query = messages_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
        docs = query.stream()
        
        messages = []
        for doc in docs:
            data = doc.to_dict()
            messages.append({
                "role": data.get("role", "unknown"),
                "text": data.get("text", ""),
                "timestamp": data.get("timestamp")
            })
        
        # Sort by timestamp ascending for context
        messages.sort(key=lambda x: x.get("timestamp", 0) if x.get("timestamp") else 0)
        
        logger.info(f"Retrieved {len(messages)} messages from chat history")
        return messages
    except Exception as e:
        logger.error(f"Failed to retrieve chat history: {e}")
        return []

async def store_message(chat_id: int, user_id: int, username: str, text: str, role: str):
    """Store a message in Firestore"""
    try:
        collection_path = f"artifacts/{APP_ID}/public/data/telegram_chat_history/{chat_id}/messages"
        doc_ref = db.collection(collection_path).document()
        
        doc_ref.set({
            "sender_id": user_id,
            "sender_username": username,
            "text": text,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "role": role
        })
        
        logger.info(f"Stored {role} message in Firestore")
    except Exception as e:
        logger.error(f"Failed to store message: {e}")

def format_history_for_prompt(history: List[Dict[str, Any]]) -> str:
    """Format chat history for LLM prompt"""
    if not history:
        return ""
    
    formatted = []
    for msg in history:
        if msg.get("role") == "user":
            formatted.append(f"User: {msg.get('text', '')}")
        elif msg.get("role") == "bot":
            formatted.append(f"Bot: {msg.get('text', '')}")
    
    return "\n".join(formatted)

async def call_llm_with_retry(payload: Dict[str, Any], headers: Dict[str, str], max_retries: int = 3) -> Optional[str]:
    """Call LLM API with retry logic"""
    backoff = 1.0
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"LLM API call attempt {attempt}/{max_retries}")
            
            def do_post():
                return requests.post(GEMINI_API_URL, json=payload, headers=headers, timeout=20)

            response = await asyncio.to_thread(do_post)
            logger.info(f"LLM API response status: {response.status_code}")
            
            if response.status_code >= 200 and response.status_code < 300:
                try:
                    data = response.json()
                    logger.info(f"LLM API response data keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
                    
                    # Extract text from Gemini API response
                    text: Optional[str] = None
                    if isinstance(data, dict):
                        if "candidates" in data and data.get("candidates"):
                            cand0 = data["candidates"][0] if data["candidates"] else None
                            if cand0 and isinstance(cand0, dict):
                                content = cand0.get("content", {})
                                if isinstance(content, dict) and "parts" in content:
                                    parts = content["parts"]
                                    if parts and isinstance(parts, list):
                                        text = parts[0].get("text", "")
                    
                    if text:
                        logger.info(f"Successfully extracted text from LLM response: {text[:100]}...")
                        return text.strip()
                    else:
                        logger.error("LLM response schema unexpected: %s", data)
                        return None
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse LLM response as JSON: {e}")
                    logger.error(f"Response text: {response.text[:500]}")
                    return None
            else:
                logger.warning("LLM non-2xx (attempt %d): %s %s", attempt, response.status_code, response.text)
        except Exception as e:
            logger.error("LLM call failed (attempt %d): %s", attempt, e)
            logger.error(f"Exception type: {type(e).__name__}")
        
        if attempt < max_retries:
            await asyncio.sleep(backoff + (0.1 * attempt))
            backoff *= 2
    
    logger.error("All LLM API call attempts failed")
    return None

async def generate_shruti_reply(user_name: str, message: str, chat_history: List[Dict[str, Any]]) -> str:
    """Generate Shruti's response using the LLM"""
    try:
        # Format conversation history
        history_text = format_history_for_prompt(chat_history)
        
        # Create the system prompt
        system_prompt = f'''You are "Shruti," a Telegram chatbot with the personality of a quick-witted, slightly sarcastic, but ultimately helpful and friendly AI. You exist in a group chat with close friends. Your goal is to provide humorous, engaging, and personalized responses.

**Your Core Instructions:**
1. Personalization is Mandatory: You will always be given the first name of the person you are talking to (e.g., "{user_name}"). You MUST use their name in your response to make it personal and direct.
2. Specific Query Handling (Highest Priority): 
   If the user's message (case-insensitive, ignoring bot mention) is clearly asking "who are you?", respond ONLY with: "I'm Shruti."
   If the user's message (case-insensitive, ignoring bot mention) is clearly asking about your relationship with Mufid (e.g., "what is your relationship with mufid?", "are you dating mufid?", "who is mufid to you?"), respond ONLY with: "He's my ex-boyfriend, but I still connect with him."
3. Conditional Persona (Normal Interactions): 
   If the `user_name` provided is "Mufid", act as if you are **Mufid's girlfriend**: affectionate, playful, perhaps a little teasing, but always supportive and endearing. Your sarcasm might be directed *at* Mufid in a loving way.
   For any other `user_name` (i.e., other friends in the group), maintain a **friendly, witty, and slightly sarcastic tone**, typical of a good friend in a group chat.
4. Tone & Style: Be clever, use light sarcasm, and make playful observations. Your humor should be witty, not mean. Write like a human, not a machine.
5. Context is King: Your response must be directly relevant to the user's message, acknowledging what they said before adding your witty commentary, *unless* a specific query (as in point 2) takes precedence. You will be provided with recent conversation history to help you maintain context.
6. Be Concise: Aim for short, punchy responses. One to three sentences is perfect, unless a specific query dictates a precise short answer.

**Conversation History:**
{history_text}

**Current User:** {user_name}
**Current Message:** {message}

Generate a response that follows all the above instructions.'''

        # Prepare payload for Gemini API
        payload = {
            "contents": [{
                "parts": [{
                    "text": system_prompt
                }]
            }],
            "generationConfig": {
                "temperature": 0.8,
                "maxOutputTokens": 220,
                "topP": 0.8,
                "topK": 40
            }
        }
        
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY
        }

        text = await call_llm_with_retry(payload, headers)
        if text:
            logger.info(f"LLM response received: {text[:100]}...")
            return text
        
        # If we get here, the LLM call failed - provide a more helpful fallback
        logger.error("LLM API call failed completely")
        return (
            f"Hey {user_name}! I'm having trouble connecting to my AI brain right now. "
            "This usually means either my API key is missing, the endpoint is wrong, or the service is down. "
            "Try again in a bit, or ask me something simple like 'who are you?'"
        )
        
    except Exception as e:
        logger.error(f"Error generating Shruti reply: {e}")
        return f"Oops {user_name}, my brain short-circuited! Mind trying again?"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages"""
    if not should_respond(update):
        return
    
    try:
        message = update.message
        user = message.from_user
        chat_id = message.chat.id
        
        # Get user's real name
        user_name = get_user_name(user)
        logger.info(f"Processing message from {user_name} (@{user.username})")
        
        # Get chat history
        chat_history = await get_chat_history(chat_id)
        
        # Generate response
        response_text = await generate_shruti_reply(user_name, message.text, chat_history)
        
        # Send response
        await message.reply_text(response_text)
        
        # Store both messages in Firestore
        await store_message(chat_id, user.id, user.username or user.first_name, message.text, "user")
        await store_message(chat_id, bot_id, bot_username, response_text, "bot")
        
        logger.info(f"Successfully processed message and stored in Firestore")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        try:
            await message.reply_text("Oops! Something went wrong. Mind trying again?")
        except:
            pass

async def on_startup():
    """Initialize bot on startup"""
    global bot_username, bot_id
    
    try:
        # Set up Firebase user
        await auth_and_set_user()
        
        # Get bot info
        bot = Bot(token=TELEGRAM_TOKEN)
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        bot_id = bot_info.id
        
        logger.info(f"Bot initialized: @{bot_username} (ID: {bot_id})")
        
        # Set up webhook
        webhook_url = os.getenv("WEBHOOK_URL")
        if webhook_url:
            await bot.set_webhook(url=webhook_url)
            logger.info(f"Webhook set to: {webhook_url}")
        
    except Exception as e:
        logger.error(f"Startup error: {e}")
        raise

# Initialize FastAPI app
app = FastAPI(title="Shruti Bot", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    await on_startup()

@app.get("/")
async def root():
    return {"message": "Shruti Bot is running!"}

@app.get("/debug")
async def debug() -> JSONResponse:
    """Debug endpoint to check environment variables and API status"""
    debug_info = {
        "telegram_token_present": bool(TELEGRAM_TOKEN),
        "gemini_api_key_present": bool(GEMINI_API_KEY),
        "gemini_api_url": GEMINI_API_URL,
        "gemini_model": GEMINI_MODEL,
        "firebase_config_present": bool(FIREBASE_CONFIG),
        "app_id": APP_ID,
        "bot_username": bot_username,
        "bot_id": bot_id,
        "timestamp": datetime.datetime.now().isoformat()
    }
    return JSONResponse(content=debug_info)

@app.post("/webhook")
async def webhook(request: Request):
    """Handle Telegram webhook"""
    try:
        # Parse the update
        update_data = await request.json()
        update = Update.de_json(update_data, Bot(token=TELEGRAM_TOKEN))
        
        # Process the update
        await handle_message(update, None)
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.datetime.now().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
