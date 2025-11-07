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
- Generate **Session String** using [this tool](https://generatesessionstring.pella.app) or run:
  ```python
  from pyrogram import Client
  app = Client("session", api_id=YOUR_ID, api_hash="YOUR_HASH")
  app.start(); print(app.export_session_string()); app.stop()
