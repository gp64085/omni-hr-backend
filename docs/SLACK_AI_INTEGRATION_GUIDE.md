# OmniHR Slack & Groq AI Conversational Assistant Setup Guide

This guide walks you through setting up the **Slack & Groq AI integration** with OmniHR. With this feature, employees can interact with OmniHR in natural language to query leave balances, draft and submit leave applications (with interactive review and confirmation), view upcoming company holidays, and log project timesheets.

---

## 1. Overview & Architecture

- **Direct Messages (DMs):** Designed for 1-on-1 private interactions, balance inquiries, company holiday lookups, and personal timesheet queries.
- **@Mentions in Channels:** Designed for quick action triggers (e.g. `@OmniBot apply casual leave tomorrow` or `@OmniBot log 4 hours on Mobile App`).
- **2-Phase Confirmation Pattern:** When an employee drafts a leave request, OmniBot validates the dates against weekends and company holidays, verifies quota sufficiency, and presents an interactive **Slack Block Kit card** with `[✅ Confirm & Submit]` and `[❌ Cancel]` buttons. The database record is only inserted once the user explicitly clicks confirm.
- **Encrypted In-App Credential Management:** All credentials (Slack Bot Token, Signing Secret, Groq API Key) are configured via the OmniHR UI at `/integrations`, encrypted using AES/Fernet in PostgreSQL, and never leaked in logs or API responses.

---

## 2. Slack App Creation & Configuration

### Step 1: Create a Slack App
1. Go to the [Slack API Portal](https://api.slack.com/apps) and click **Create New App** &rarr; **From scratch**.
2. Name your app (e.g., `OmniHR Bot`) and select your target workspace.

### Step 2: Configure Bot Permissions (OAuth & Permissions)
1. In the sidebar under **Features**, select **OAuth & Permissions**.
2. Scroll down to **Scopes** &rarr; **Bot Token Scopes** and add the following 6 scopes:
   - `chat:write` — Post messages and interactive Block Kit cards.
   - `im:history` — Read messages in Direct Message threads.
   - `im:read` — View basic information about Direct Messages.
   - `app_mentions:read` — Receive `@mentions` in channels.
   - `users:read` — Look up workspace members.
   - `users:read.email` — Resolve Slack user ID to corporate email for OmniHR RBAC mapping.
3. Scroll up and click **Install to Workspace** (or **Reinstall to Workspace**).
4. Copy the generated **Bot User OAuth Token** (starts with `xoxb-...`).

### Step 3: Get the Signing Secret
1. In the sidebar under **Settings**, select **Basic Information**.
2. Under **App Credentials**, click **Show** on **Signing Secret** and copy it.

### Step 4: Configure Event Subscriptions
1. In the sidebar under **Features**, select **Event Subscriptions** and toggle it **On**.
2. In the **Request URL** field, enter your public backend endpoint:
   ```text
   https://<your-backend-domain>/api/v1/integrations/slack/events
   ```
   *(Slack will immediately send a `url_verification` challenge; OmniHR automatically responds with HTTP 200 and the challenge token).*
3. Under **Subscribe to bot events**, add:
   - `message.im` — Enables direct 1-on-1 messages with the bot.
   - `app_mention` — Enables channel-level `@mentions`.
4. Click **Save Changes**.

### Step 5: Enable Interactivity
1. In the sidebar under **Features**, select **Interactivity & Shortcuts** and toggle it **On**.
2. In the **Request URL** field, enter:
   ```text
   https://<your-backend-domain>/api/v1/integrations/slack/interactions
   ```
3. Click **Save Changes**.

---

## 3. Groq AI Configuration

1. Visit [Groq Console](https://console.groq.com/keys) and generate an API key (starts with `gsk_...`).
2. Recommended model: `llama-3.3-70b-versatile` for fast, accurate structured function calling.

---

## 4. Configuring Credentials in OmniHR

1. Log in to the OmniHR web application as a **Super Admin**.
2. In the navigation sidebar, click **Integrations & AI** (`/integrations`).
3. Under **Slack App Integration**:
   - Paste your **Bot User OAuth Token** (`xoxb-...`).
   - Paste your **Signing Secret**.
   - Click **Test Slack Connection** to verify workspace authentication.
   - Click **Save Configuration**.
4. Under **Groq AI Reasoning Engine**:
   - Paste your **Groq API Key** (`gsk_...`).
   - Select `Llama 3.3 70B Versatile`.
   - Click **Test Groq API Connection** to verify access.
   - Click **Save Configuration**.

---

## 5. Usage Examples

### In Direct Messages:
- `"What is my current leave balance?"` &rarr; Returns formatted balance card for Casual, Sick, and Earned leaves.
- `"List upcoming company holidays"` &rarr; Returns official holidays for the year.
- `"Apply 2 days of casual leave from 2026-12-10 to 2026-12-11 for family vacation"` &rarr; Returns review card with `[Confirm & Submit]` button.

### In Public or Team Channels:
- `@OmniHR Bot apply sick leave for today because of fever`
- `@OmniHR Bot log 4 hours on Mobile App for UI refactoring`
