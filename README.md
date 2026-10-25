# EditByChat — Alpha 1.0

EditByChat is a chat-first web video editor. Upload a video, describe the change in natural language, refine it with follow-up messages, preview the result, and export MP4.

The assistant decides **what operation to perform**. FFmpeg performs the actual edit deterministically.

## Alpha 1.0 highlights

### First-run onboarding + polished export

The first visit now explains the core workflow:

1. Upload a video.
2. Tell the editor what to change.
3. Refine by chatting, preview, and export.

The Export dialog supports:

- Original / 720p / 1080p
- Source / 30 / 60 fps
- Draft / Balanced / High quality
- Background export job with real progress

The exporter does not upscale a smaller source merely to hit the selected preset.

### True clip crossfades

`Insert clip ... with fade` and `Replace ... with clip ... with fade` now use a real overlapping video `xfade` plus audio `acrossfade`. Earlier MVP builds faded the inserted clip locally; Alpha 1.0 blends the two clips at the transition boundary.

Example:

```text
Insert clip at 5 seconds with fade
Replace from 8 to 12 seconds with clip and fade
```

### Caption transcription cache

Automatic captions are cached per normalized audio fingerprint + transcription model.

When a project is re-rendered after visual-only changes such as moving a logo, the same audio reuses its saved timestamped transcription rather than calling the transcription API again. Audio-changing edits naturally produce a different fingerprint and are transcribed again.

### Project workflow

- Persistent projects on disk
- Resume last project in the same browser
- Browser-scoped recent projects
- Rename projects
- Persistent chat history
- Logical project layers (`Text 1`, `Logo 1`, `Music 1`, `Zoom 1`, etc.)
- Remove / restore old layers by name
- Visual timeline + playhead
- Background render jobs with real progress
- Undo

### Editing features

- Trim the beginning
- Remove a time range
- 9:16 / 1:1 / 16:9 framing
- Video volume / mute
- Speed changes
- Timed text overlays
- Automatic captions
- Logo/image overlays
- Background music
- Time-ranged zoom with focus position
- Fade in/out
- Insert another video clip
- Replace a range with another clip
- True clip crossfade
- Multi-step commands
- Conversational follow-ups
- Named layer references
- Export presets

## English-first, multilingual product

English is the default and canonical experience. Unsupported browser languages fall back to English.

Localized UI/SEO routes are included for 14 languages:

- English `/l/en`
- Spanish `/l/es`
- Portuguese `/l/pt`
- French `/l/fr`
- Arabic `/l/ar` (RTL)
- German `/l/de`
- Hindi `/l/hi`
- Indonesian `/l/id`
- Turkish `/l/tr`
- Japanese `/l/ja`
- Korean `/l/ko`
- Chinese `/l/zh`
- Italian `/l/it`
- Russian `/l/ru`

Localized routes return the language, title, description, canonical URL, and `Content-Language` from the server before JavaScript runs. `hreflang` alternates are included.

For serious international SEO, each locale should eventually get useful unique landing-page copy instead of relying only on translated product UI.

## Example commands

```text
Remove first 4 seconds
Make it 9:16
Add text “Order Now” at the end for 3 seconds
Add captions
Add logo top right
Add music at 20%
Zoom 1.3x from 4 to 7 seconds
Insert clip at 5 seconds with fade
Remove Text 1
Move Logo 1 left
Restore Zoom 1
Undo that
```

## Architecture

```text
User command
  -> background edit job
  -> named-layer resolver / conversational parser
  -> validated logical layer state
  -> rebuild active stack from clean base
  -> FFmpeg render
  -> persisted project + preview

Export settings
  -> background export job
  -> H.264/AAC output preset
  -> MP4 download
```

The LLM does not render video. It only converts free-form language into validated edit operations.

## Run locally

Requirements: Python 3.11+ and FFmpeg/ffprobe on PATH.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

Open `http://127.0.0.1:8000` for the landing page and `http://127.0.0.1:8000/editor` for the editor.

Health check:

```text
GET /healthz
```

## Optional AI + automatic captions

Copy `.env.example` to `.env` and configure:

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-6-luna
OPENAI_CAPTION_MODEL=whisper-1
OPENAI_BASE_URL=https://api.openai.com/v1
```

Without an API key, deterministic editing, project layers, persistence, multilingual UI, many common multilingual commands, and non-caption edits continue to work.

`OPENAI_CAPTION_MODEL` is isolated as an environment variable so the timestamp-capable transcription model can be changed without altering the editor.

## Docker

```bash
cp .env.example .env
# fill in OPENAI_API_KEY if needed
docker compose up --build
```

Projects are mounted from `./projects` to `/app/projects` so they survive container replacement.

Useful deployment variables:

```bash
PROJECTS_DIR=/app/projects
MAX_UPLOAD_MB=500
PUBLIC_BASE_URL=https://example.com
PORT=8000
```

## Tests

```bash
python smoke_test.py
node --check <extracted-ui-js-file>
```

The smoke test covers command parsing, contextual revisions, zoom/fade, true crossfade insertion, replacement, audio/video preservation, export encoding, and health checks.

## Current production gaps

Alpha 1.0 is suitable for controlled user testing, not an open paid launch yet.

Before public production use, add:

- Authentication and per-user project authorization
- Durable job queue + isolated render workers
- Object storage / database instead of local-only project files
- Billing and usage quotas
- Rate limits and abuse protection
- Upload malware/content validation and cleanup policies
- Job and export retention/garbage collection
- Analytics, error monitoring, and worker metrics
- Signed/private media URLs
- Richer unique SEO landing content per language

The current in-process background thread queue is intentionally MVP-level.


## Product site structure

- `/` — English marketing landing page (canonical)
- `/l/{locale}` — localized marketing landing pages
- `/editor` — English editor
- `/editor/l/{locale}` — localized editor UI
- `/sitemap.xml` — marketing pages only
- `/robots.txt` — allows landing pages and keeps editor/API out of search

The current working brand is **EditByChat**. A normal web search on 2026-10-25 did not surface an obvious existing product using that exact name, but domain and trademark availability should still be checked before a paid public launch.
