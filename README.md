# Gopf Intel — Discord Bot Messenger

Streamlit app for sending trading signals to Discord via a bot. Supports direct messages and channel delivery with a self-service onboarding flow for users.

## Features

- **Direct Messages**: Send personalized signals to individual users via DM
- **Channel Delivery**: Broadcast signals to a Discord text channel
- **OAuth2 Onboarding**: Users connect their Discord account through a guided flow — no manual ID copying
- **Live Message Feed**: Auto-refreshing chat view for both DMs and channels
- **Admin Dashboard**: Manage recipients, select channels, and send messages

## Setup

### 1. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** and name it (e.g. "Gopf Intel")
3. Go to **Bot** tab → **Reset Token** → copy the token
4. Go to **OAuth2** tab → copy the **Application ID** (Client ID)

### 2. Configure OAuth2 (optional, for user self-service)

1. In the Developer Portal → **OAuth2** → **General** → **Reset Secret** → copy the secret
2. Under **Redirects**, add your app URL (e.g. `http://localhost:8501`)

### 3. Install and Run

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your bot token, client ID, and optionally OAuth2 credentials
streamlit run app.py
```

The app runs at `http://localhost:8501`.

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Yes | Bot token from the Developer Portal |
| `DISCORD_CLIENT_ID` | Yes | Application / Client ID |
| `DISCORD_CLIENT_SECRET` | No | OAuth2 client secret (enables user onboarding) |
| `DISCORD_REDIRECT_URI` | No | OAuth2 redirect URI (must match Developer Portal) |

## Usage

### User Onboarding

Share your app URL with users. They choose between DM or channel delivery and authorize via Discord OAuth2. No manual setup needed.

### Admin Dashboard

Access the admin view by appending `?admin` to the URL (e.g. `http://localhost:8501/?admin`).

From here you can:
- Select a connected user and send them a DM
- Load server channels and broadcast messages
- View live message history

## Project Structure

```
app.py            # Streamlit application (all-in-one)
.env.example      # Template for environment variables
avatar.jpeg       # Bot avatar image
requirements.txt  # Python dependencies
config.json       # Auto-generated runtime config (gitignored)
```
