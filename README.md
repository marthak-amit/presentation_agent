# PresenterAgent

An AI agent that **presents your PPTX for you, in your voice**, and answers audience questions live — including
voice interruptions like *"I have a question"* in the middle of a slide. Built for demo reliability on one laptop.

```
PPTX ─▶ parse + render (LibreOffice) ─▶ narration (Groq) ─▶ per-sentence TTS (ElevenLabs) ─▶ Presenter page
                          └─▶ Chroma RAG (slides + notes + narration + extra docs)           ▲   │ mic (PCM16) + WS
mic ─▶ Deepgram live STT ─▶ barge-in detector ─▶ pause ─▶ Groq (streaming, tools) ─▶ TTS ────┘   ▼
```

## Quick start

Requirements: Python 3.11+, Node 18+, LibreOffice (`soffice`) and poppler (`pdftoppm`) on PATH.
(macOS: `brew install --cask libreoffice && brew install poppler` · Debian/Ubuntu: `apt install libreoffice-impress poppler-utils`)

```bash
make install        # creates .venv, installs pip + npm deps, creates .env from .env.example
$EDITOR .env        # add your keys (see below) - everything works without them in MOCK mode
make dev            # backend :8000 + frontend :5173
```
Open <http://localhost:5173>, upload a `.pptx` (`make sample` writes `tests/sample_deck.pptx`), wait for **Ready**,
**Open presenter**, **Start presenting**.

### Keys (`.env`)

| var | purpose | if empty |
|---|---|---|
| `GROQ_API_KEY` | narration + Q&A (Groq only) | **mock LLM** (template narration, extractive answers) |
| `GROQ_QA_MODEL` / `GROQ_SCRIPT_MODEL` / `GROQ_FALLBACK_MODEL` | model ids, from env only | defaults in `.env.example` |
| `DEEPGRAM_API_KEY` | live STT (and Aura TTS fallback) | **mock STT**: say-it box: use the *Simulate speech* box |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | your cloned voice | falls back to **Deepgram Aura**; with no keys at all → silent mock audio |
| `PRESENTER_NAME` | the voice the agent speaks as (e.g. `Bytes Technolab developer`) | |

The header of the Presenter page shows which services are real (`STT: deepgram (up) · TTS: elevenlabs · LLM: groq`) and
`GET /health` reports the same.
Embeddings use `sentence-transformers/all-MiniLM-L6-v2` locally (first run downloads ~90 MB; if the download is
blocked it falls back to a built-in hashing embedder so nothing breaks).

## Features at a glance

- **Setup check** (`/check`): real requests verify every key/model/tool, mic level meter, voice test. Run it before every demo.
- **Voice picker** (`/check` → *Choose your voice*): test any voice in your ElevenLabs account and switch with one click (no `.env` editing, no restart).
- **Script editor** (`/script/{deck}`): edit what will be said per slide, or ask the AI to rewrite it ("shorter", "more casual", "mention the pilot"). Only changed sentences are re-voiced; the Q&A index and slide highlighter follow. Tone (conversational / formal / energetic / storytelling) and length are chosen at upload.
- **Live reading highlighter + mouse arrow** follow the line being spoken, in narration and in answers.
- **Voice**: say **"Okay Agent"** then a question, or a command: "next slide", "previous slide", "go to slide 3", "pause", "continue", "repeat this slide", "start over". Cut an answer short with "Okay Agent" / "wait" / "stop". Extra wake spellings: `WAKE_PHRASES` in `.env`.
- **Keyboard**: hold T to talk (push-to-talk) · Space pause/resume · ←/→ slides · F full screen · M mic · B voice commands on/off. Thumbnail strip, progress bar, talk timer, full-screen stage mode with big captions.
- **Q&A report**: the end screen has *Download Q&A report (.md)* (questions, answers, a checklist of follow-ups to send).
- Answers come in the language of the question (English / Hindi / Hinglish).

## How a session works

1. **Ingest** (`POST /decks`): slides → PNGs, speaker notes → first-person narration (45–75 s per slide, sequential Groq calls with 429 backoff),
   narration split into sentences, everything indexed in Chroma, then **every sentence is pre-rendered to MP3** (cached; never regenerated) plus the stock clips.
2. **Present**: the server holds the playhead `(slide, sentence)` and tells the browser what to play; the browser reports when each clip ends.
3. **Interrupt**: the mic streams continuously to Deepgram. Say **"Okay Agent"** (or "I have a question", "excuse me", "wait", "ek sawaal", …),
   or hold **Hold to talk**, to pause with a 150 ms fade → "Sure, go ahead." → the agent listens.
4. **Answer**: top-3 RAG chunks → Groq (streaming, tools `search_kb / goto_slide / resume_presenting`) → sentence-by-sentence TTS → playback.
   Then "Does that answer your question? Anything else?" → 4 s window → "Great, let's continue." → resumes at the **start of the interrupted sentence**.
5. **End**: "Questions asked" + "Unanswered" screen, then open Q&A. Unanswered questions are saved in `backend/data/logs/unanswered.json`;
   every interruption in `backend/data/logs/sessions/{session}.jsonl`.

Protocol details: [`docs/ws.md`](docs/ws.md). Debug page: `/debug` (live transcript, state, trigger hits, model, first-token / first-audio / total latency).

## Demo checklist

- [ ] `make dev` is running; `curl localhost:8000/health` shows the services you expect (not `fake`).
- [ ] Deck uploaded **the day before**; status *Ready* and `audio X/X` complete on the Upload page (audio is cached on disk, so the talk survives a network drop).
- [ ] Run **Check setup** (`/check`): everything green, mic meter moves, *Play voice test* sounds like you.
- [ ] Read the generated script in **Edit script** and fix anything that does not sound like you.
- [ ] Add reference docs (FAQ/pricing PDF) with *Add reference docs* so Q&A has answers beyond the slides.
- [ ] Use **headphones** or keep speakers low and use Chrome (echo cancellation is on, but a loud speaker next to the mic will still bleed).
- [ ] Click **Enable microphone** and allow the permission prompt; the Debug page (second window/monitor) should show live interim text when you speak.
- [ ] Say "I have a question" once during a dry run: the pause should land well under half a second.
- [ ] Ask one question the deck can answer and one it cannot (should defer: "I'll have Bytes Technolab developer follow up" and appear under *Unanswered*).
- [ ] Know the fallbacks: **Voice barge-in OFF + Hold to talk**, **Simulate speech** box.
- [ ] Groq free tier: don't re-upload decks during the demo (narration is sequential and rate limited); Q&A falls back to `GROQ_FALLBACK_MODEL` then a canned follow-up clip on its own.

## Troubleshooting

| symptom | fix |
|---|---|
| **The agent interrupts itself / false barge-ins** | its own voice is reaching the mic. Use headphones, lower speaker volume, or switch **Voice barge-in** off and use hold-to-talk. The echo guard drops transcripts that look like the current narration sentence (similarity > 0.6) and there is a 3 s cooldown after every resume. |
| **Barge-in never fires** | check the Debug page: *Trigger hits* lists ignored matches with the reason (`confidence`, `cooldown`, `echo`). Confirm `STT: deepgram (up)`; mic permission granted; the mic icon/permission in the browser address bar. |
| **429 / rate limited** | Narration retries with exponential backoff (honours `Retry-After`) and then switches to `GROQ_FALLBACK_MODEL`; Q&A switches immediately (model → fallback model → canned clip). Debug page shows `(fallback)`. Wait a minute or use a paid key. |
| **Mic permission denied / no mic** | Chrome → site settings → Microphone → Allow, then click *Enable microphone*. `getUserMedia` needs `localhost` or HTTPS. The rest of the app works without a mic. |
| **Slides look like grey placeholders** | LibreOffice is missing or failed; install it (see above) and re-upload. |
| **Sentence audio is silent** | no TTS keys → mock silent audio (by design). Set `ELEVENLABS_*` or `DEEPGRAM_API_KEY`, then `POST /decks/{id}/audio` to upgrade mock files. |
| **ElevenLabs works but voice is wrong** | check `ELEVENLABS_VOICE_ID` is your cloned voice's id (not its name). |
| **Banner "Speech recognition offline"** | Deepgram connection dropped; it reconnects automatically (audio is buffered). Use Hold to talk meanwhile (it also needs STT) or the Simulate box. |
| **Browser blocks audio** | click *Start presenting* (a user gesture is required to unlock WebAudio). |
| **First run is slow / `sentence-transformers` download blocked** | the app falls back to a hashing embedder automatically (lower retrieval quality); fix network and delete `backend/data/chroma` to re-index with MiniLM. |

## Mock mode

Without keys every service has an offline stand-in so the whole flow is runnable (and testable):
`FakeLLM` (template narration + extractive answers, supports `goto_slide`), `FakeSTT` (`sim_transcript` messages / Simulate box),
`FakeTTS` (valid silent MP3 whose length tracks the text; `MOCK_TTS_SPEED=6` speeds dry runs up). Force mocks with `FORCE_MOCK=llm,stt,tts`.

## Tests

```bash
make test        # pytest (backend, ~1 min) + frontend type-check/build
```

## Layout

```
backend/  ingest/ (parse, render, narration, pipeline)   rag/ (chunk, embed, Chroma)   llm/ (client, prompts, tools, qa engine)
          stt/ (deepgram, fake)   tts/ (elevenlabs, aura, chain, cache, clips, pregen)
          session/ (messages, state, barge_in, session, ws, hub, logs)   api/   data/ (decks, chroma, stock, logs)
frontend/src/  pages/ (Upload, Presenter, Debug)   audio/ (mic, playbackQueue, streamingPlayer)   session/ (ws client)
docs/ws.md   PROGRESS.md   docker-compose.yml (optional)
```
