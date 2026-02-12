import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
import streamlit as st
from dotenv import load_dotenv
import os

load_dotenv()

# --- Constants ---
DISCORD_API = "https://discord.com/api/v10"
DISCORD_OAUTH2_TOKEN_URL = "https://discord.com/api/oauth2/token"
CONFIG_PATH = Path(__file__).parent / "config.json"
AVATAR_PATH = Path(__file__).parent / "avatar.jpeg"

st.set_page_config(page_title="Discord Bot Messenger", page_icon=":speech_balloon:")


# --- Helper functions ---
def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2))


def discord_request(method: str, endpoint: str, token: str, **kwargs) -> requests.Response:
    url = f"{DISCORD_API}{endpoint}"
    headers = {"Authorization": f"Bot {token}"}
    return requests.request(method, url, headers=headers, timeout=10, **kwargs)


def get_guild_channels(token: str, guild_id: str) -> list[dict]:
    resp = discord_request("GET", f"/guilds/{guild_id}/channels", token)
    resp.raise_for_status()
    channels = resp.json()
    # type 0 = text channels
    return sorted(
        [ch for ch in channels if ch["type"] == 0],
        key=lambda ch: ch.get("position", 0),
    )


def send_message(token: str, channel_id: str, content: str) -> requests.Response:
    return discord_request(
        "POST",
        f"/channels/{channel_id}/messages",
        token,
        json={"content": content},
    )


def open_dm_channel(token: str, user_id: str) -> dict:
    """Create or get an existing DM channel with a user."""
    resp = discord_request(
        "POST",
        "/users/@me/channels",
        token,
        json={"recipient_id": user_id},
    )
    resp.raise_for_status()
    return resp.json()


def get_messages(token: str, channel_id: str, limit: int = 25) -> list[dict]:
    """Fetch recent messages from a channel (works for both text channels and DMs)."""
    resp = discord_request("GET", f"/channels/{channel_id}/messages?limit={limit}", token)
    resp.raise_for_status()
    return resp.json()


def display_messages(messages: list[dict], bot_id: str) -> None:
    """Display messages using st.chat_message, newest at the bottom."""
    if not messages:
        st.caption("No messages yet.")
        return
    # API returns newest first — reverse for chronological order
    for msg in reversed(messages):
        author = msg.get("author", {})
        is_bot = author.get("id") == bot_id
        role = "assistant" if is_bot else "user"
        username = author.get("global_name") or author.get("username", "Unknown")
        timestamp_str = msg.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(timestamp_str).astimezone(tz=None)
            time_display = ts.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            time_display = ""
        with st.chat_message(role):
            st.caption(f"**{username}** · {time_display}")
            st.markdown(msg.get("content", ""))


def send_and_report(token: str, channel_id: str, content: str) -> None:
    """Send a message and show success/error feedback."""
    if not content.strip():
        st.error("Message cannot be empty.")
        return
    try:
        resp = send_message(token, channel_id, content)
        if resp.status_code == 200:
            st.success("Message sent!")
        elif resp.status_code == 429:
            retry_after = resp.json().get("retry_after", "a few")
            st.error(f"Rate limited. Retry after {retry_after} seconds.")
        elif resp.status_code == 403:
            st.error("Bot lacks permission to send messages here. Check bot roles in Discord.")
        elif resp.status_code == 404:
            st.error("Channel not found. It may have been deleted — try reloading.")
        else:
            st.error(f"Discord API error ({resp.status_code}): {resp.text}")
    except requests.RequestException as exc:
        st.error(f"Network error: {exc}")


# --- OAuth2 helpers ---
def generate_auth_url(client_id: str, redirect_uri: str, scope: str, permissions: int | None = None) -> str:
    """Build a Discord OAuth2 authorization URL."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
    }
    if permissions is not None:
        params["permissions"] = str(permissions)
    return f"https://discord.com/oauth2/authorize?{urlencode(params)}"


def exchange_code_for_token(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Exchange an OAuth2 authorization code for an access token."""
    resp = requests.post(
        DISCORD_OAUTH2_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_oauth_user(access_token: str) -> dict:
    """Get the current user via OAuth2 Bearer token (user's own identity)."""
    resp = requests.get(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def add_authorized_user(config: dict, user_record: dict) -> dict:
    """Add or update a user in the authorized_users list. Returns updated config."""
    users = config.get("authorized_users", [])
    # Update existing or append new
    for i, u in enumerate(users):
        if u["id"] == user_record["id"]:
            users[i] = user_record
            config["authorized_users"] = users
            return config
    users.append(user_record)
    config["authorized_users"] = users
    return config


def migrate_config(config: dict) -> dict:
    """Migrate old single-user DM fields to authorized_users list (one-time)."""
    old_user_id = config.get("dm_user_id")
    if old_user_id and "authorized_users" not in config:
        config["authorized_users"] = [{
            "id": old_user_id,
            "username": config.get("dm_username", ""),
            "global_name": config.get("dm_username", ""),
            "dm_channel_id": config.get("dm_channel_id", ""),
            "authorized_at": datetime.now(timezone.utc).isoformat(),
        }]
        # Clean up old fields
        config.pop("dm_user_id", None)
        config.pop("dm_channel_id", None)
        config.pop("dm_username", None)
        save_config(config)
    return config


# --- Onboarding wizard ---
def _onboarding_dm(config: dict) -> None:
    """DM setup step of the onboarding wizard."""
    st.subheader("Step 1 of 2: Connect your Discord account")
    st.write(
        "Click the button below to authorize. The bot will be able to send "
        "you private messages with your personalized signals."
    )

    if not OAUTH2_AVAILABLE:
        st.error("OAuth2 is not configured — DM connections require it.")
        with st.expander("Setup instructions for administrators"):
            st.markdown(
                f"""\
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application → **OAuth2** → **General**
3. Click **Reset Secret** → copy the new secret
4. Under **Redirects**, click **Add Redirect** and enter your app URL
   (e.g. `http://localhost:8501`)
5. Add both values to the `.env` file next to `app.py`:
   ```
   DISCORD_CLIENT_SECRET=your_secret_here
   DISCORD_REDIRECT_URI=http://localhost:8501
   ```
6. Restart the Streamlit app

The redirect URI must match **exactly** — including trailing slashes and `http` vs `https`.
"""
            )
        if st.button("← Back"):
            st.session_state.onboarding_step = "choose"
            st.rerun()
        return

    has_guild = bool(config.get("guild_id"))
    if has_guild:
        auth_url = generate_auth_url(CLIENT_ID, REDIRECT_URI, scope="identify")
        st.link_button("Connect with Discord", auth_url, type="primary", use_container_width=True)
    else:
        auth_url = generate_auth_url(
            CLIENT_ID, REDIRECT_URI, scope="bot identify", permissions=68608
        )
        st.link_button("Add Bot & Connect", auth_url, type="primary", use_container_width=True)
        st.caption("This will also add the bot to your server.")

    if st.button("← Back"):
        st.session_state.onboarding_step = "choose"
        st.rerun()


def _onboarding_channel(config: dict) -> None:
    """Channel setup step of the onboarding wizard."""
    # Step 1: Add bot
    st.subheader("Step 1 of 3: Add the bot to your server")
    invite_url = (
        f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
        f"&scope=bot&permissions=68608"
    )
    st.link_button("Add Bot to Server", invite_url, use_container_width=True)

    st.divider()

    # Step 2: Enter Server ID
    st.subheader("Step 2 of 3: Enter your Server ID")
    saved_guild = config.get("guild_id", "")
    guild_id = st.text_input(
        "Server (Guild) ID",
        value=saved_guild,
        placeholder="e.g. 123456789012345678",
        key="onboarding_guild_id",
    )
    guild_valid = (
        guild_id.strip().isdigit() and len(guild_id.strip()) >= 17
        if guild_id.strip()
        else False
    )

    if guild_id.strip() and not guild_valid:
        st.warning("Server ID should be a numeric snowflake (17-20 digits).")

    if "onboarding_channels" not in st.session_state:
        st.session_state.onboarding_channels = []

    if st.button("Load Channels", disabled=not guild_valid, key="onboarding_load_ch"):
        try:
            channels = get_guild_channels(BOT_TOKEN, guild_id.strip())
            st.session_state.onboarding_channels = channels
            if not channels:
                st.warning("No text channels found. Is the bot in this server?")
        except requests.HTTPError as exc:
            st.session_state.onboarding_channels = []
            status = exc.response.status_code
            if status == 403:
                st.error("Bot doesn't have access to this server. Add it using the button above.")
            elif status == 404:
                st.error("Server not found. Check the Server ID.")
            else:
                st.error(f"Discord API error ({status}): {exc.response.text}")
        except requests.RequestException as exc:
            st.session_state.onboarding_channels = []
            st.error(f"Network error: {exc}")

    st.divider()

    # Step 3: Select channel
    st.subheader("Step 3 of 3: Select a channel")
    channel_options = {
        f"#{ch['name']}  ({ch['id']})": ch
        for ch in st.session_state.onboarding_channels
    }

    if channel_options:
        saved_channel_id = config.get("channel_id", "")
        default_idx = 0
        for i, (label, ch) in enumerate(channel_options.items()):
            if ch["id"] == saved_channel_id:
                default_idx = i
                break
        selected_label = st.selectbox(
            "Channel",
            list(channel_options.keys()),
            key="onboarding_channel_select",
            index=default_idx,
        )

        if st.button("Save", key="onboarding_save_channel", type="primary"):
            ch = channel_options[selected_label]
            config["guild_id"] = guild_id.strip()
            config["channel_id"] = ch["id"]
            config["channel_name"] = ch["name"]
            save_config(config)
            st.success(f"Saved! Signals will be delivered to **#{ch['name']}**.")
            st.balloons()
    else:
        st.caption("Load channels from your server first.")

    if st.button("← Back"):
        st.session_state.onboarding_step = "choose"
        st.rerun()


def show_onboarding(config: dict, bot_info: dict) -> None:
    """Render the end-user onboarding wizard."""
    authorized_users = config.get("authorized_users", [])
    channel_configured = bool(config.get("channel_id"))

    # If setup is already complete, show status page
    if authorized_users or channel_configured:
        st.title("Gopf Intel")

        if authorized_users:
            names = [u.get("global_name") or u.get("username", "?") for u in authorized_users]
            st.success(f"Connected: **{', '.join(names)}**")
        if channel_configured:
            st.success(f"Channel: **#{config.get('channel_name', '?')}**")

        st.markdown("[Open Admin Dashboard](?admin)")

        # Allow connecting additional users
        if OAUTH2_AVAILABLE:
            st.divider()
            st.caption("Connect another account:")
            has_guild = bool(config.get("guild_id"))
            if has_guild:
                auth_url = generate_auth_url(CLIENT_ID, REDIRECT_URI, scope="identify")
                st.link_button("Connect with Discord", auth_url)
            else:
                auth_url = generate_auth_url(
                    CLIENT_ID, REDIRECT_URI, scope="bot identify", permissions=68608
                )
                st.link_button("Add Bot & Connect", auth_url)

        st.divider()
        st.caption("[Admin Dashboard](?admin)")
        return

    # --- Onboarding wizard for new setup ---
    if "onboarding_step" not in st.session_state:
        st.session_state.onboarding_step = "choose"

    step = st.session_state.onboarding_step

    st.title("Gopf Intel")

    if step == "choose":
        st.subheader("Trading Signals on Discord")
        st.write("How would you like to receive your signals?")

        col_dm, col_ch = st.columns(2)
        with col_dm:
            st.markdown("**Direct Message** (Recommended)")
            st.caption("Private, personalized to you.")
            if st.button("Direct Message", type="primary", use_container_width=True):
                st.session_state.onboarding_step = "dm"
                st.rerun()
        with col_ch:
            st.markdown("**Channel**")
            st.caption("Shared with everyone in the channel.")
            if st.button("Channel", use_container_width=True):
                st.session_state.onboarding_step = "channel"
                st.rerun()

    elif step == "dm":
        _onboarding_dm(config)

    elif step == "channel":
        _onboarding_channel(config)

    # Admin link
    st.divider()
    st.caption("[Admin Dashboard](?admin)")


# --- Load env vars ---
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "")
OAUTH2_AVAILABLE = bool(CLIENT_SECRET and REDIRECT_URI)

if not BOT_TOKEN or not CLIENT_ID:
    st.error("Missing `DISCORD_BOT_TOKEN` or `DISCORD_CLIENT_ID` in `.env` file.")
    with st.expander("Setup instructions"):
        st.markdown(
            """\
1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it **Gopf Intel**
3. Go to **Bot** tab → click **Reset Token** → copy the token
4. Go to **OAuth2** tab → copy the **Application ID** (Client ID)
5. Create a `.env` file next to `app.py`:
   ```
   DISCORD_BOT_TOKEN=your_token_here
   DISCORD_CLIENT_ID=your_client_id_here
   ```
6. Restart the app
"""
        )
    st.stop()

# --- Verify bot token ---
try:
    me_resp = discord_request("GET", "/users/@me", BOT_TOKEN)
except requests.RequestException as exc:
    st.error(f"Could not connect to Discord API: {exc}")
    st.stop()

if me_resp.status_code == 401:
    st.error("Invalid bot token. Please check `DISCORD_BOT_TOKEN` in your `.env` file.")
    st.stop()
elif me_resp.status_code != 200:
    st.error(f"Discord API error ({me_resp.status_code}): {me_resp.text}")
    st.stop()

bot_info = me_resp.json()
BOT_ID = bot_info["id"]

# --- Load & migrate config ---
config = load_config()
config = migrate_config(config)

# --- OAuth2 callback handler (must run before UI renders) ---
if OAUTH2_AVAILABLE and "code" in st.query_params:
    oauth_code = st.query_params["code"]
    oauth_guild_id = st.query_params.get("guild_id", "")
    try:
        token_data = exchange_code_for_token(oauth_code, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
        access_token = token_data["access_token"]
        user_data = get_oauth_user(access_token)

        # Open a DM channel with the user so the bot can message them
        dm_channel = open_dm_channel(BOT_TOKEN, user_data["id"])

        user_record = {
            "id": user_data["id"],
            "username": user_data.get("username", ""),
            "global_name": user_data.get("global_name") or user_data.get("username", ""),
            "dm_channel_id": dm_channel["id"],
            "authorized_at": datetime.now(timezone.utc).isoformat(),
        }
        config = add_authorized_user(config, user_record)

        # Capture guild_id if provided (from bot install flow)
        if oauth_guild_id:
            config["guild_id"] = oauth_guild_id

        save_config(config)
        st.query_params.clear()
        st.title("Gopf Intel")
        st.success(f"Connected: **{user_record['global_name']}** ({user_record['username']})")
        st.info("You're all set! The bot can now send you direct messages.\n\nYou can close this page.")
        st.divider()
        st.caption("[Admin Dashboard](?admin)")
        st.stop()
    except requests.HTTPError as exc:
        st.query_params.clear()
        st.error(f"OAuth2 error: {exc.response.status_code} — {exc.response.text}")
        st.stop()
    except requests.RequestException as exc:
        st.query_params.clear()
        st.error(f"Network error during OAuth2: {exc}")
        st.stop()

# --- Mode detection ---
is_admin = "admin" in st.query_params

if not is_admin:
    show_onboarding(config, bot_info)
    st.stop()

# --- Admin UI ---
st.title("Gopf Intel — Admin")

guild_id = config.get("guild_id", "")

# --- Tabs: DM first, Channel second ---
tab_dm, tab_channel = st.tabs(["Direct Messages", "Channel"])

# ========================
# Tab: Direct Messages
# ========================
with tab_dm:
    authorized_users = config.get("authorized_users", [])

    if not authorized_users:
        st.info("No users connected yet. Share the onboarding link with users to get started.")
        st.code(REDIRECT_URI or "http://localhost:8501")
    else:
        # --- Recipient selection ---
        user_options = {
            f"{u.get('global_name') or u.get('username', '?')}  ({u['id']})": u
            for u in authorized_users
        }
        selected_label = st.selectbox(
            f"Recipient ({len(authorized_users)} connected)",
            list(user_options.keys()),
            key="dm_user_select",
        )
        selected_user = user_options[selected_label]

        # Resolve DM channel
        dm_channel_id = selected_user.get("dm_channel_id", "")

        if not dm_channel_id:
            st.warning("No DM channel for this user yet.")
            if st.button("Open DM Channel", key="open_dm"):
                try:
                    dm_channel = open_dm_channel(BOT_TOKEN, selected_user["id"])
                    selected_user["dm_channel_id"] = dm_channel["id"]
                    config = add_authorized_user(config, selected_user)
                    save_config(config)
                    st.rerun()
                except requests.HTTPError as exc:
                    status = exc.response.status_code
                    if status == 403:
                        st.error("Cannot open DM — the user may have DMs disabled.")
                    elif status == 404:
                        st.error("User not found.")
                    else:
                        st.error(f"Discord API error ({status}): {exc.response.text}")
                except requests.RequestException as exc:
                    st.error(f"Network error: {exc}")
        else:
            target_name = selected_user.get("global_name") or selected_user.get("username", "?")

            # --- Send DM ---
            dm_message = st.text_area(
                "Message",
                max_chars=2000,
                height=150,
                placeholder=f"Type a message to {target_name}...",
                key="dm_message",
            )
            st.caption(f"{len(dm_message)} / 2000 characters")

            if st.button("Send Message", type="primary", key="send_dm"):
                send_and_report(BOT_TOKEN, dm_channel_id, dm_message)

            # --- Auto-refreshing message feed ---
            st.divider()
            st.subheader("Messages")

            @st.fragment(run_every=timedelta(seconds=2))
            def _dm_message_feed():
                try:
                    msgs = get_messages(BOT_TOKEN, dm_channel_id)
                    display_messages(msgs, BOT_ID)
                except requests.HTTPError as exc:
                    status = exc.response.status_code
                    if status == 403:
                        st.error("Bot lacks permission to read this DM channel.")
                    else:
                        st.error(f"Discord API error ({status}): {exc.response.text}")
                except requests.RequestException as exc:
                    st.error(f"Network error: {exc}")

            _dm_message_feed()

        # --- Connected users list ---
        with st.expander(f"All connected users ({len(authorized_users)})"):
            for u in authorized_users:
                name = u.get("global_name") or u.get("username", "?")
                auth_time = u.get("authorized_at", "")
                try:
                    ts = datetime.fromisoformat(auth_time).astimezone(tz=None)
                    auth_display = ts.strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    auth_display = "unknown"
                st.markdown(f"- **{name}** (`{u['id']}`) — connected {auth_display}")

# ========================
# Tab: Channel
# ========================
with tab_channel:
    if not guild_id:
        st.info(
            "No server connected yet. The server ID is captured automatically "
            "when a user completes onboarding with **Add Bot & Connect**."
        )
    else:
        st.caption(f"Server `{guild_id}`")

        # Channel loading
        if "channels" not in st.session_state:
            st.session_state.channels = []

        if st.button("Load Channels", key="load_channels"):
            try:
                channels = get_guild_channels(BOT_TOKEN, guild_id)
                st.session_state.channels = channels
                if not channels:
                    st.warning("No text channels found. Is the bot in this server?")
            except requests.HTTPError as exc:
                st.session_state.channels = []
                status = exc.response.status_code
                if status == 403:
                    st.error("Bot doesn't have access to this server.")
                elif status == 404:
                    st.error("Server not found.")
                else:
                    st.error(f"Discord API error ({status}): {exc.response.text}")
            except requests.RequestException as exc:
                st.session_state.channels = []
                st.error(f"Network error: {exc}")

        # Channel selection
        channel_options = {f"#{ch['name']}  ({ch['id']})": ch for ch in st.session_state.channels}

        if channel_options:
            saved_channel_id = config.get("channel_id", "")
            default_idx = 0
            for i, (label, ch) in enumerate(channel_options.items()):
                if ch["id"] == saved_channel_id:
                    default_idx = i
                    break
            selected_label = st.selectbox("Channel", list(channel_options.keys()), index=default_idx, key="admin_channel_select")

            if st.button("Save", key="save_channel"):
                ch = channel_options[selected_label]
                config["guild_id"] = guild_id
                config["channel_id"] = ch["id"]
                config["channel_name"] = ch["name"]
                save_config(config)
                st.success("Configuration saved!")
                st.rerun()

        if config.get("channel_id"):
            st.info(f"Current target: **#{config.get('channel_name', '?')}**")

            # --- Send Message ---
            st.subheader("Send Message")

            ch_message = st.text_area(
                "Message",
                max_chars=2000,
                height=150,
                placeholder="Type your message here...",
                key="ch_message",
            )
            st.caption(f"{len(ch_message)} / 2000 characters")

            if st.button("Send Message", type="primary", key="send_ch"):
                send_and_report(BOT_TOKEN, config["channel_id"], ch_message)

            # --- Auto-refreshing message feed ---
            st.divider()
            st.subheader("Messages")
            _ch_channel_id = config["channel_id"]

            @st.fragment(run_every=timedelta(seconds=2))
            def _ch_message_feed():
                try:
                    msgs = get_messages(BOT_TOKEN, _ch_channel_id)
                    display_messages(msgs, BOT_ID)
                except requests.HTTPError as exc:
                    status = exc.response.status_code
                    if status == 403:
                        st.error("Bot lacks permission to read message history.")
                    else:
                        st.error(f"Discord API error ({status}): {exc.response.text}")
                except requests.RequestException as exc:
                    st.error(f"Network error: {exc}")

            _ch_message_feed()
