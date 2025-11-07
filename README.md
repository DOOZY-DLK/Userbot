# 🚀 Telegram Auto React Userbot

Automatically reacts to **new messages** in **PMs, Groups & Channels** with random emojis.

---

## Features
- Reacts with **40+ Telegram emojis**
- Skips **edited & replied** messages
- Per-chat **ON/OFF** using `/react`
- Works in **Private, Groups, Channels**
- FloodWait & error handling
- Inline button control

---

## Setup

### 1. Get Credentials
- Go to [my.telegram.org](https://my.telegram.org)
- Create app → Get `API_ID` & `API_HASH`
- Generate **Session String** using [this tool](https://t.me/StringFatherBot) or run:
  ```python
  from pyrogram import Client
  app = Client("session", api_id=YOUR_ID, api_hash="YOUR_HASH")
  app.start(); print(app.export_session_string()); app.stop() ```



  #2. Deploy (Free Options)
Option A: Pella.app (Recommended)

Go to pella.app
Click "New Project"
upload app.py + requirements.txt
edit Environment Variables:
textAPI_ID = your_id
API_HASH = your_hash
SESSION_STRING = your_session_string
