### Shruti Telegram Bot (FastAPI + Webhooks)

A witty, slightly sarcastic, and friendly Telegram chatbot named "Shruti". Responds in group chats only when mentioned or when replied to. Persists chat history to Firestore and generates humorous replies via an external LLM API.

### Features
- Selective activation in group chats: mention `@YourBot` or reply to the bot
- Personalized name mapping via `users.json` (optional)
- Firestore persistence for chat history per `chat_id`
- LLM-powered witty replies with explicit persona (Grok-style humor)
- FastAPI webhook endpoint for easy Render deployment
- Robust error handling and exponential backoff for LLM calls

### Tech Stack
- Python 3.10+
- python-telegram-bot==20.7
- FastAPI, Uvicorn
- Firebase Admin SDK (Firestore)
- requests, python-dotenv

### Project Structure
```
shruti-bot/
  ├─ main.py
  └─ README.md
```

### Environment Variables
Set these in your local `.env` (for dev) and in Render (for prod):

- `TELEGRAM_TOKEN`: Telegram Bot API token from BotFather
- `GEMINI_API_URL`: Base URL of your LLM endpoint (OpenAI/Gemini/Anthropic-compatible)
- `GEMINI_API_KEY`: API key for your LLM provider
- `__firebase_config`: JSON string of Firebase service account (full JSON)
- `__initial_auth_token`: Optional Firebase ID token to derive a stable `uid` (else anonymous UUID)
- `__app_id`: Logical app ID to namespace Firestore data (e.g., `shruti-prod`)
- `RENDER_EXTERNAL_URL`: Render will inject this (public URL of your service) — used to auto-set webhook

Example `.env` content (do NOT commit secrets):
```
TELEGRAM_TOKEN=123456:ABC...
GEMINI_API_URL=https://api.openai.com/v1/chat/completions
GEMINI_API_KEY=sk-...
__firebase_config={"type":"service_account",...}
__initial_auth_token=
__app_id=shruti-dev
```

### Optional: users.json
Place `users.json` next to `main.py` if you want to map Telegram usernames to preferred names.
```
{
  "mufid_tg": "Mufid",
  "brooke_user": "Brooke",
  "dex_the_great": "Dex"
}
```
If absent or invalid, the bot falls back to `first_name`.

### Installation (Local Dev)
1. Create and activate a virtual environment
```
python -m venv .venv
. .venv/Scripts/activate   # Windows PowerShell
# or
source .venv/bin/activate  # macOS/Linux
```
2. Install dependencies
```
pip install python-telegram-bot==20.7 fastapi uvicorn python-dotenv firebase-admin requests
```
3. Create a `.env` file (see variables above)
4. Run the app
```
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
5. Set Telegram webhook to your local tunnel (e.g., with `ngrok http 8000`):
```
curl -X POST "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://<your-ngrok-domain>/webhook"}'
```

### Render Deployment
1. Create a new Web Service on Render:
   - Runtime: Python 3.10+
   - Build command: `pip install -r requirements.txt` (create this file as below)
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Root directory: `shruti-bot`
2. Environment variables: add all variables listed above. Render sets `RENDER_EXTERNAL_URL` automatically.
3. requirements.txt (create in the service root):
```
python-telegram-bot==20.7
fastapi
uvicorn
python-dotenv
firebase-admin
requests
```
4. After first deploy, the service will auto-set the webhook using `RENDER_EXTERNAL_URL`.
   - You can also manually set/reset via GET: `https://<your-render-url>/set_webhook`

### How It Works
- FastAPI exposes:
  - `POST /webhook`: Telegram delivers updates here
  - `GET /set_webhook`: manually set webhook to Render URL
  - `GET /healthz`: health check
- The bot only talks in groups when:
  - It is mentioned (e.g., `@ShrutiBot`), or
  - A message is a direct reply to one of the bot’s messages
- Conversations are stored in Firestore at:
  - `/artifacts/{__app_id}/public/data/telegram_chat_history/{chat_id}/messages`
- Before replying, the bot loads the last ~10 messages for context, then calls the LLM with a persona prompt.

### Notes
- Commands (messages starting with `/`) are ignored.
- Two special queries are answered exactly:
  - "who are you?" → `I'm Shruti.`
  - Relationship with Mufid → `He's my ex-boyfriend, but I still connect with him.`
- In private chats, the bot always responds. In groups, it waits for a trigger.

### Troubleshooting
- No replies? Ensure webhook is set and `TELEGRAM_TOKEN` is correct.
- Firestore not saving? Confirm `__firebase_config` is a valid service account JSON string and rules allow writes.
- LLM errors? Check `GEMINI_API_URL` and `GEMINI_API_KEY`. The bot will fall back to a witty error message.
