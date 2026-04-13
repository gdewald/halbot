# ShowMeBot

A Discord bot that manages your server's soundboard using natural language. Mention the bot and tell it what to do — it uses a local LLM (via LM Studio) to interpret your requests and take action.

## Features

### Soundboard Management
- **List sounds** — view all sounds on the server soundboard, or filter by user, date, or keyword
- **Remove sounds** — delete specific sounds or clear the entire soundboard
- **Edit sounds** — rename sounds or change their emoji (supports both unicode and custom server emojis)

### Sound Library
A local SQLite-backed library for saving and organizing sounds beyond what's on the live soundboard.

- **Save from soundboard** — back up sounds from the live soundboard to the library
- **Upload files** — save audio attachments (from the current message or recent chat history) directly to the library
- **Restore to soundboard** — re-upload saved sounds back to the live soundboard
- **Browse & manage** — list, rename, update metadata, or delete saved sounds

### Audio Effects
Apply effects to saved sounds and store the results as new clips. Powered by pydub/ffmpeg.

- **Echo** — configurable delay, decay, and repeat count (presets: light, medium, heavy)
- **Reverb** — simulated room reverb with adjustable room size (presets: small, medium, large)
- **Pitch shift** — adjust pitch up or down by semitones (presets: chipmunk, deep, subtle shifts)
- **Composable** — effects chain together. Applying echo to a pitch-shifted clip creates an "echo+pitch" variant
- **Non-destructive** — modified clips are tracked as children of the original. The full effect chain is always re-applied from the original audio to avoid quality degradation
- **Conversational flow** — if you don't specify parameters, the bot asks you to pick a preset or give custom values

### Custom Emoji Awareness
- Syncs all server custom emojis to a local database on startup
- Uses LM Studio's vision model to auto-generate descriptions of each emoji
- Can list emojis with descriptions, and use them when editing sounds
- Re-syncs automatically when emojis are added or changed on the server

### Persona System
Users can change how the bot talks by setting behavior directives via natural language.

- **Add** — "from now on respond like a grumpy old man"
- **Update** — "tone down the grumpy thing a bit" (modifies the existing directive)
- **Remove** — "stop being grumpy" (drops a specific directive)
- **List** — view all active behavior directives
- Directives are stored in the database and injected into the LLM system prompt
- The LLM flavors all responses (including canned data listings) to match the active persona
- Guard rails: max 200 characters per directive, max 10 active directives

### Conversation Context
The bot reads the last 50 messages in the channel and passes them to the LLM, so it understands follow-up requests and can reference recent audio attachments from chat history.

## Requirements

- Python 3.12+
- [LM Studio](https://lmstudio.ai/) running locally (or any OpenAI-compatible API)
- [ffmpeg](https://ffmpeg.org/) installed and on PATH (for audio effects)
- A Discord bot token with message content and guild intents

## Setup

1. Clone the repo
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your values:
   ```
   DISCORD_TOKEN=your_discord_bot_token_here
   LMSTUDIO_URL=http://localhost:1234/v1/chat/completions
   LOG_LEVEL=INFO
   ```
4. Start LM Studio and load a model
5. Run the bot:
   ```
   python bot.py
   ```

## Usage

Mention the bot in any server channel:

```
@ShowMeBot what sounds are on the soundboard
@ShowMeBot remove the airhorn sound
@ShowMeBot save all sounds to the library
@ShowMeBot add reverb to big-yoshi
@ShowMeBot from now on talk like a pirate
@ShowMeBot what custom emojis do we have
```

You can also attach audio files (MP3, OGG, WAV) to your message to save them to the library.

### Soundboard Limits

- Max file size: 512 KB
- Max duration: 5.2 seconds
- Formats: MP3, OGG, WAV

## CLI Flags

```
python bot.py --list-personas    # Show all active persona directives
python bot.py --clear-personas   # Remove all persona directives and exit
```

## Infrastructure

The `infra/` folder contains Terraform + cloud-init configuration for deploying LM Studio on a GCP VM:

- `main.tf` — GCP Compute Engine instance, VPC, and firewall rules
- `cloud-init-script.sh` — automated LM Studio installation and systemd service setup

All secrets (LM Studio auth keys, VM user) are externalized as Terraform variables marked `sensitive`.

## Architecture

```
Discord message
    |
    v
on_message handler
    |-- parse attachments & validate audio
    |-- fetch soundboard state
    |-- fetch channel history (last 50 messages)
    |-- fetch saved library & emoji DB
    |
    v
parse_intent() --> LM Studio (local LLM)
    |                returns JSON action(s)
    v
action handlers
    |-- list / remove / edit / clear      (live soundboard)
    |-- upload / save / restore / ...     (local library)
    |-- effect_ask / effect_apply         (audio effects)
    |-- persona_set / update / remove     (behavior directives)
    |-- emoji_list                        (custom emojis)
    v
Discord reply (auto-split at 2000 chars)
```
