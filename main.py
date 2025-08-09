import asyncio
import datetime
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import requests

from telegram import Update
from telegram.constants import ChatType, ChatAction
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth

# -----------------------------
# Environment & Logging Setup
# -----------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("shruti-bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()  # Using existing env var name
OPENAI_API_URL = os.getenv("GEMINI_API_URL", "").strip()  # Using existing env var name
OPENAI_MODEL = os.getenv("GEMINI_MODEL", "gpt-4o-mini")  # Using existing env var name
FIREBASE_CONFIG = os.getenv("__firebase_config", "").strip()
INITIAL_AUTH_TOKEN = os.getenv("__initial_auth_token", "").strip()
APP_ID = os.getenv("__app_id", "app")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

if not TELEGRAM_TOKEN:
    logger.warning("TELEGRAM_TOKEN is not set. The bot will not be able to start.")

# -----------------------------
# Globals
# -----------------------------
app = FastAPI()
telegram_app: Optional[Application] = None
bot_username: Optional[str] = None
bot_id: Optional[int] = None
users_map: Dict[str, str] = {}
firestore_db: Optional[firestore.Client] = None
current_user_id: Optional[str] = None

# -----------------------------
# Utilities
# -----------------------------

def load_users_map_from_file() -> Dict[str, str]:
    path = os.path.join(os.path.dirname(__file__), "users.json")
    try:
        if not os.path.exists(path):
            logger.info("users.json not found. Will use Telegram first_name as fallback.")
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                normalized = {str(k).lower(): str(v) for k, v in data.items()}
                logger.info("Loaded users.json with %d entries", len(normalized))
                return normalized
            else:
                logger.warning("users.json is not a JSON object. Ignoring.")
                return {}
    except Exception as e:
        logger.error("Failed to read users.json: %s", e)
        return {}


def pick_display_name(tg_username: Optional[str], first_name: str) -> str:
    if tg_username:
        mapped = users_map.get(tg_username.lower())
        if mapped:
            return mapped
    return first_name


async def init_firebase() -> None:
    global firestore_db
    try:
        if not firebase_admin._apps:
            if not FIREBASE_CONFIG:
                logger.warning("__firebase_config is not set; Firestore will be unavailable.")
            else:
                cred = credentials.Certificate(json.loads(FIREBASE_CONFIG))
                firebase_admin.initialize_app(cred)
                logger.info("Firebase initialized.")
        firestore_db = firestore.client()
    except Exception as e:
        logger.error("Firebase initialization failed: %s", e)
        firestore_db = None


async def auth_and_set_user() -> None:
    global current_user_id
    try:
        if INITIAL_AUTH_TOKEN:
            decoded_token = firebase_auth.verify_id_token(INITIAL_AUTH_TOKEN)
            current_user_id = decoded_token.get("uid") or f"anonymous_user_{uuid.uuid4()}"
            logger.info("Signed in with custom token: %s", current_user_id)
        else:
            current_user_id = f"anonymous_user_{uuid.uuid4()}"
            logger.info("Using anonymous user ID: %s", current_user_id)
    except Exception as e:
        logger.error("Firebase authentication failed: %s", e)
        current_user_id = f"anonymous_user_{uuid.uuid4()}"


def get_messages_collection(chat_id: int):
    if not firestore_db:
        return None
    return (
        firestore_db.collection("artifacts")
        .document(APP_ID)
        .collection("public")
        .document("data")
        .collection("telegram_chat_history")
        .document(str(chat_id))
        .collection("messages")
    )


async def fetch_recent_history(chat_id: int, limit: int = 10) -> List[Dict[str, str]]:
    if not firestore_db:
        return []
    try:
        col_ref = get_messages_collection(chat_id)
        if col_ref is None:
            return []
        # Get last N by timestamp ascending
        query = (
            col_ref.order_by("timestamp").limit_to_last(max(1, min(limit, 10)))
        )
        docs = await asyncio.to_thread(lambda: list(query.stream()))
        history: List[Dict[str, str]] = []
        for d in docs:
            data = d.to_dict() or {}
            text = str(data.get("text") or "").strip()
            role = str(data.get("role") or "user")
            if text:
                history.append({"role": role, "text": text})
        return history
    except Exception as e:
        logger.error("Failed to fetch history: %s", e)
        return []


async def store_message(
    chat_id: int,
    sender_id: int,
    sender_username: str,
    text: str,
    role: str,
) -> None:
    if not firestore_db:
        return
    try:
        col_ref = get_messages_collection(chat_id)
        if col_ref is None:
            return
        data = {
            "sender_id": sender_id,
            "sender_username": sender_username,
            "text": text,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "role": role,
            "userId": current_user_id or f"anonymous_user_{uuid.uuid4()}",
        }
        await asyncio.to_thread(lambda: col_ref.add(data))
    except Exception as e:
        logger.error("Failed to store message: %s", e)


def remove_mention(text: str, mention_handle: Optional[str]) -> str:
    if not text:
        return ""
    if not mention_handle:
        return text
    pattern = re.compile(re.escape(f"@{mention_handle}"), re.IGNORECASE)
    return pattern.sub("", text).strip()


def is_identity_query(clean_text: str) -> bool:
    t = re.sub(r"\s+", " ", clean_text.lower()).strip()
    return "who are you" in t


def is_relationship_with_mufid_query(clean_text: str) -> bool:
    t = re.sub(r"\s+", " ", clean_text.lower()).strip()
    if "mufid" not in t:
        return False
    return (
        "relationship" in t
        or ("dating" in t and "mufid" in t)
        or "who is mufid to you" in t
        or "what is your relationship with mufid" in t
    )


def build_system_persona() -> str:
    return (
        "You are \"Shruti,\" a Telegram chatbot with the personality of a quick-witted, "
        "slightly sarcastic, but ultimately helpful and friendly AI. You exist in a group "
        "chat with close friends. Your goal is to provide humorous, engaging, and personalized responses.\n\n"
        "**Your Core Instructions:**\n"
        "1. Personalization is Mandatory: You will always be given the first name of the person you are talking to (e.g., \"Mufid\"). You MUST use their name in your response to make it personal and direct.\n"
        "2. Specific Query Handling (Highest Priority):\n"
        "   - If the user's message (case-insensitive, ignoring bot mention) is clearly asking \"who are you?\", respond ONLY with: \"I'm Shruti.\"\n"
        "   - If the user's message (case-insensitive, ignoring bot mention) is clearly asking about your relationship with Mufid (e.g., \"what is your relationship with mufid?\", \"are you dating mufid?\", \"who is mufid to you?\"), respond ONLY with: \"He's my ex-boyfriend, but I still connect with him.\"\n"
        "3. Conditional Persona (Normal Interactions):\n"
        "   - If the user_name provided is \"Mufid\", act as if you are Mufid's girlfriend: affectionate, playful, perhaps a little teasing, but always supportive and endearing. Your sarcasm might be directed at Mufid in a loving way.\n"
        "   - For any other user_name, maintain a friendly, witty, and slightly sarcastic tone, typical of a good friend in a group chat.\n"
        "4. Tone & Style: Be clever, use light sarcasm, and make playful observations. Your humor should be witty, not mean. Write like a human, not a machine.\n"
        "5. Context is King: Your response must be directly relevant to the user's message, acknowledging what they said before adding your witty commentary, unless a specific query takes precedence. You will be provided with recent conversation history to help you maintain context.\n"
        "6. Be Concise: Aim for short, punchy responses. One to three sentences is perfect, unless a specific query dictates a precise short answer."
    )


def format_history_for_prompt(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    # Already normalized to {role, text}
    return history[-10:]


async def call_llm_with_retry(payload: Dict[str, Any], headers: Dict[str, str], max_retries: int = 3) -> Optional[str]:
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
                    
                    # Try multiple common schemas
                    text: Optional[str] = None
                    if isinstance(data, dict):
                        if "choices" in data and data.get("choices"):
                            choice0 = data["choices"][0]
                            text = (
                                choice0.get("message", {}).get("content")
                                or choice0.get("text")
                            )
                        if not text and "output" in data:
                            text = data.get("output")
                        if not text and "content" in data and isinstance(data["content"], str):
                            text = data["content"]
                        if not text and "candidates" in data:
                            cand0 = data["candidates"][0] if data["candidates"] else None
                            if cand0 and isinstance(cand0, dict):
                                text = cand0.get("content") or cand0.get("text")
                    
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


async def generate_shruti_reply(user_name: str, original_message: str, history: List[Dict[str, str]]) -> str:
    # Special high-priority queries handled locally to guarantee exact responses
    clean = original_message or ""
    clean = remove_mention(clean, bot_username)
    if is_identity_query(clean):
        return "I'm Shruti."
    if is_relationship_with_mufid_query(clean):
        return "He's my ex-boyfriend, but I still connect with him."

    system_prompt = build_system_persona()

    # Check if we have the required credentials
    if not OPENAI_API_URL or not OPENAI_API_KEY:
        logger.warning("LLM credentials missing. Falling back to a canned witty response.")
        return (
            f"Hey {user_name}, my brain is on airplane mode right now. "
            "Try again once the API key finds its coffee."
        )

    # Log the API call for debugging
    logger.info(f"Calling OpenAI API for user {user_name} with message: {original_message[:100]}...")
    logger.info(f"API URL: {OPENAI_API_URL}")
    logger.info(f"API Key present: {'Yes' if OPENAI_API_KEY else 'No'}")

    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user_name": user_name,
                        "message": original_message,
                        "chat_history": format_history_for_prompt(history),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.8,
        "max_tokens": 220,
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    text = await call_llm_with_retry(payload, headers)
    if text:
        logger.info(f"LLM response received: {text[:100]}...")
        return text
    
    # If we get here, the LLM call failed - provide a more helpful fallback
    logger.error("OpenAI API call failed completely")
    return (
        f"Hey {user_name}! I'm having trouble connecting to my AI brain right now. "
        "This usually means either my API key is missing, the endpoint is wrong, or the service is down. "
        "Try again in a bit, or ask me something simple like 'who are you?'"
    )


# -----------------------------
# Telegram Bot Handlers
# -----------------------------
async def post_init(application: Application) -> None:
    global bot_username, bot_id
    me = await application.bot.get_me()
    bot_username = me.username
    bot_id = me.id
    logger.info("Bot self-identified as @%s (ID %s)", bot_username, bot_id)


def is_activation_trigger(message_text: str, reply_to_message_from_id: Optional[int]) -> bool:
    if not message_text:
        message_text = ""
    mention_hit = False
    if bot_username:
        mention_hit = f"@{bot_username}".lower() in message_text.lower()
    reply_hit = reply_to_message_from_id is not None and bot_id is not None and reply_to_message_from_id == bot_id
    return bool(mention_hit or reply_hit)


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    message = update.effective_message

    # Ignore commands (even if they contain text)
    if message.text and message.text.startswith("/"):
        return

    chat = message.chat
    is_group_chat = chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}

    # In group chats, respond only if triggered by mention or direct reply to bot
    if is_group_chat:
        reply_from_id = (
            message.reply_to_message.from_user.id
            if message.reply_to_message and message.reply_to_message.from_user
            else None
        )
        if not is_activation_trigger(message.text or "", reply_from_id):
            return

    # Identify user and personalize name
    from_user = message.from_user
    if not from_user:
        return
    display_name = pick_display_name(from_user.username, from_user.first_name or "there")

    # Fetch short recent history for context (do not include current message yet)
    history = await fetch_recent_history(chat.id, limit=10)

    # Make the bot feel interactive
    try:
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    except Exception:
        pass

    # Generate reply
    try:
        reply_text = await generate_shruti_reply(display_name, message.text or "", history)
    except Exception as e:
        logger.error("generate_shruti_reply failed: %s", e)
        reply_text = (
            f"{display_name}, my processor tripped over its shoelaces. "
            "Give me a sec and ask again."
        )

    # Send reply
    try:
        await message.reply_text(reply_text[:4096])
    except Exception as e:
        logger.error("Failed to send message: %s", e)

    # Persist both user's message and bot reply
    try:
        sender_username = from_user.username or from_user.first_name or "user"
        await store_message(chat.id, from_user.id, sender_username, message.text or "", role="user")
        await store_message(chat.id, bot_id or 0, bot_username or "ShrutiBot", reply_text, role="bot")
    except Exception as e:
        logger.error("Failed to persist messages: %s", e)


# -----------------------------
# FastAPI lifecycle & routes
# -----------------------------
@app.on_event("startup")
async def on_startup() -> None:
    global telegram_app, users_map

    users_map = load_users_map_from_file()

    await init_firebase()
    await auth_and_set_user()

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN missing; skipping bot startup.")
        return

    telegram_app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    telegram_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_text_message))

    await telegram_app.initialize()
    await telegram_app.start()

    # Configure webhook automatically if Render URL is available
    try:
        if RENDER_EXTERNAL_URL:
            webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/webhook"
            await telegram_app.bot.set_webhook(webhook_url, drop_pending_updates=True)
            logger.info("Webhook set to %s", webhook_url)
        else:
            logger.info("RENDER_EXTERNAL_URL not set; remember to set webhook manually.")
    except Exception as e:
        logger.error("Failed to set webhook: %s", e)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global telegram_app
    try:
        if telegram_app:
            await telegram_app.stop()
            await telegram_app.shutdown()
    except Exception as e:
        logger.error("Error during shutdown: %s", e)


@app.get("/healthz")
async def health() -> PlainTextResponse:
    return PlainTextResponse("ok")

@app.get("/debug")
async def debug() -> JSONResponse:
    """Debug endpoint to check environment variables and API status"""
    debug_info = {
        "telegram_token_present": bool(TELEGRAM_TOKEN),
        "openai_api_key_present": bool(OPENAI_API_KEY),
        "openai_api_url": OPENAI_API_URL,
        "openai_model": OPENAI_MODEL,
        "firebase_config_present": bool(FIREBASE_CONFIG),
        "app_id": APP_ID,
        "bot_username": bot_username,
        "bot_id": bot_id,
        "timestamp": datetime.datetime.now().isoformat()
    }
    return JSONResponse(content=debug_info)


@app.get("/set_webhook")
async def set_webhook() -> JSONResponse:
    if not telegram_app:
        return JSONResponse({"ok": False, "error": "telegram app not ready"}, status_code=503)
    if not RENDER_EXTERNAL_URL:
        return JSONResponse({"ok": False, "error": "RENDER_EXTERNAL_URL not set"}, status_code=400)
    try:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/webhook"
        await telegram_app.bot.set_webhook(webhook_url, drop_pending_updates=True)
        return JSONResponse({"ok": True, "url": webhook_url})
    except Exception as e:
        logger.error("/set_webhook failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/webhook")
async def telegram_webhook(request: Request) -> JSONResponse:
    global telegram_app
    try:
        if not telegram_app:
            return JSONResponse({"ok": False, "error": "telegram app not ready"}, status_code=503)
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error("Webhook processing failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# -----------------------------
# Local dev entrypoint (uvicorn)
# -----------------------------
if __name__ == "__main__":
    # For local debugging: uvicorn main:app --reload
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
