# Shruti Telegram Bot (FastAPI + Webhooks)

A witty, sarcastic Telegram chatbot with personality that responds to mentions and replies in group chats.

## Features

- 🤖 **Selective Activation**: Only responds when mentioned or replied to
- 👤 **User Personalization**: Recognizes users by username or user ID
- 💬 **Conversation Memory**: Stores chat history in Firestore
- 🧠 **AI-Powered**: Uses Gemini API for intelligent responses
- 🔥 **Firebase Integration**: Secure data storage and authentication
- 🚀 **Render Ready**: Easy deployment with webhooks

## Quick Start

### 1. Environment Variables

Create a `.env` file with:

```bash
TELEGRAM_TOKEN=your_telegram_bot_token
GEMINI_API_KEY=your_gemini_api_key
GEMINI_API_URL=https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent
GEMINI_MODEL=gemini-1.5-flash
__firebase_config={"type":"service_account","project_id":"your_project","private_key_id":"...","private_key":"...","client_email":"...","client_id":"...","auth_uri":"...","token_uri":"...","auth_provider_x509_cert_url":"...","client_x509_cert_url":"..."}
__initial_auth_token=your_firebase_auth_token
__app_id=your_firebase_app_id
WEBHOOK_URL=https://your-render-app.onrender.com/webhook
```

### 2. User Identification Setup

The bot needs to know who's who! Update `users.json` with your group members:

```json
{
  "usernames": {
    "calmheartache": "Sakshi",
    "Orewa_kami_desu": "Mufid",
    "On_my_way_buddy": "Anchal",
    "Hemsworth_cris": "Infinity"
  },
  "user_ids": {
    "123456789": "Mufid",
    "987654321": "Sakshi",
    "456789123": "Anchal",
    "789123456": "Infinity"
  }
}
```

#### How to Get User IDs:

1. **Add the bot to your group**
2. **Send `/whoami` in the group** - the bot will show your user ID
3. **Update `users.json`** with the real user IDs you get

**Example output from `/whoami`:**
```
👤 User Info:
🆔 ID: 123456789
👤 Username: @Orewa_kami_desu
📝 First Name: Mufid
🔍 Real Name: Mufid
```

### 3. Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python main.py

# Or with uvicorn
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Render Deployment

1. **Push to GitHub**
2. **Connect to Render** (Web Service)
3. **Set environment variables** in Render dashboard
4. **Deploy!**

## Bot Behavior

### Activation Triggers
- **Mention**: `@YourBotUsername hello!`
- **Reply**: Reply to any bot message
- **Command**: `/whoami` (shows user info)

### Personality Modes
- **Mufid**: Gets girlfriend treatment (affectionate, playful)
- **Others**: Get friendly friend treatment (witty, sarcastic)

### Special Responses
- **"Who are you?"** → "I'm Shruti."
- **"What's your relationship with Mufid?"** → "He's my ex-boyfriend, but I still connect with him."

## Troubleshooting

### Bot Not Responding?
1. Check if bot is mentioned: `@YourBotUsername`
2. Check bot permissions in group
3. Verify webhook is set correctly

### User Not Recognized?
1. Send `/whoami` to get your user ID
2. Update `users.json` with correct user ID
3. Check bot logs for identification errors

### API Errors?
1. Verify all environment variables are set
2. Check `/debug` endpoint for configuration status
3. Ensure Firebase credentials are valid

## API Endpoints

- `GET /` - Bot status
- `GET /debug` - Environment and configuration info
- `GET /health` - Health check
- `POST /webhook` - Telegram webhook (internal)

## File Structure

```
shruti-bot/
├── main.py              # Main bot logic
├── users.json           # User mapping (username + user ID)
├── requirements.txt     # Python dependencies
├── render.yaml         # Render deployment config
├── get_user_ids.py     # Helper script for user identification
└── README.md           # This file
```

## Support

If you're having issues:
1. Check the `/debug` endpoint
2. Send `/whoami` in your group
3. Check Render deployment logs
4. Verify all environment variables are set correctly
