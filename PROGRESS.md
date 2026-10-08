# PROGRESS

Legend: **REAL** = talks to the real service when its key is present; **MOCK** = offline stand-in used when the key is missing (or when the model can't be downloaded).

## Service status in the build sandbox
| Service | Mode used while building | Notes |
|---|---|---|
| Groq LLM | MOCK (`FakeLLM`) | no `GROQ_API_KEY` in sandbox. Real client (`backend/llm/client.py::GroqLLM`) is unit-tested against stubbed streams only. |
| Deepgram STT | MOCK (`FakeSTT` + `sim_transcript`) | no key. |
| ElevenLabs / Aura TTS | MOCK (silent MP3 sized to the text) | no keys. |
| Embeddings | MOCK (`HashEmbedder`) | HuggingFace is blocked in the sandbox so `all-MiniLM-L6-v2` can't download; on your laptop `sentence-transformers` is used automatically. Chroma itself is REAL. |
| LibreOffice + pdf2image | REAL | slides render to real PNGs. |

---

## Phase 1 — Ingest + narration
**What works**
- `POST /decks` (multipart .pptx) → `deck_id`; background pipeline: parse → render → narrate (sequential, 429 backoff, model fallback, deterministic template as last resort) → index into Chroma.
- `GET /decks/{id}` → `slides [{n, image_url, title, sentences, audio_urls}]` + `status/stage/progress`; `GET /decks` list; `POST /decks/{id}/docs` (pdf/txt/md → same index); `GET /decks/{id}/search` (debug).
- `narration.json` = `[{n, title, sentences, model}]`.
- Frontend Upload page (drag-drop, progress, add docs) and read-only Presenter (slide, captions, prev/next).

**Files**: `backend/{config,services,main}.py`, `backend/ingest/*`, `backend/rag/*`, `backend/llm/*`, `backend/api/decks.py`, `scripts/make_sample_deck.py`, `tests/sample_deck.pptx`, `tests/test_phase1_ingest.py`, `frontend/*`, `Makefile`.

**Known issues**: narration quality can only be judged with a real Groq key (mock output is a template built from speaker notes).

**Verify manually**: `make dev`, open http://localhost:5173, upload `tests/sample_deck.pptx`, wait for "Ready", open presenter.

---

## Phase 2 — Audio + presenting
**What works**
- Per-sentence TTS pre-generation at ingest: `data/decks/{id}/audio/{n}/s{i}.mp3` with a `.meta.json` sidecar (text hash + provider). Existing real audio is never regenerated; only changed text or *mock* audio (when a real provider later appears) is.
- Stock clips in `data/stock/`: "Sure, go ahead.", "Does that answer your question? Anything else?", "Great, let's continue.", "Good question — give me a second.", "Great question — I'll have {PRESENTER_NAME} follow up on that." (+ `open_qa` closing line). Served at `/stock/{key}.mp3`.
- TTS chain ElevenLabs → Aura → mock with a 45 s circuit breaker (offline never stalls the talk). Missing audio is synthesised on demand.
- Server-driven presenter loop (`session/session.py`): playhead `(slide_n, sentence_i)`, auto-advances slides, pause/resume (resume restarts the interrupted sentence), next/prev/goto/restart, end → `END` → `OPEN_QA`.
- WebSocket contract in Pydantic (`session/messages.py`); frontend WebAudio playback queue with 150 ms fade-out, decode cache, next-sentence preload.
- Verified in real headless Chromium: captions advance, slides auto-advance, pause works.

**Files**: `backend/tts/*`, `backend/session/{messages,state,playback,plan,session,ws}.py`, `frontend/src/audio/*`, `frontend/src/session/*`, `frontend/src/pages/Presenter.tsx`, `tests/test_phase2_audio.py`, `tests/helpers.py`.

**Known issues / notes**
- Mock TTS is silent (real-time length). Set `MOCK_TTS_SPEED=6` for faster dry-runs.
- A React StrictMode double mount used to open two sockets; fixed by ignoring stale sockets in `PresenterClient`.

**Verify manually**: upload the sample deck → Open presenter → Start; pause/resume/next/prev.

---

## Phase 3 — Live Q&A + voice barge-in
**What works**
- **Mic → Deepgram live** (`backend/stt/deepgram.py`): `interim_results`, `utterance_end_ms=1000`, `language=multi`, `smart_format`, keyterms = all trigger phrases, `vad_events`, KeepAlive, auto-reconnect with audio buffering. Tested against a local websocket server (connect, stream, parse, drop + reconnect). Browser mic: `getUserMedia({echoCancellation, noiseSuppression, autoGainControl})` → AudioWorklet → 16 kHz PCM16 over the WS, streamed continuously (also while the agent talks).
- **Barge-in** (`session/barge_in.py`): all 14 spec triggers (+ Devanagari variants), rapidfuzz `partial_ratio >= 85` on interim transcripts (single-word triggers match whole words only, so "waiting" ≠ "wait"), confidence ≥ 0.7, 3 s post-resume cooldown, echo guard (similarity > 0.6 to the current/previous narration sentence, scaled by word overlap so a trigger phrase merely *inside* a sentence doesn't over-match). Detection is pure CPU: ~µs–ms; measured `detect_ms` < 400 asserted in tests; 136 ms browser round trip observed.
- On match: `pause` (150 ms fade, queue cleared) → PAUSED → "Sure, go ahead." → LISTENING; >4 words after the trigger ⇒ treated as the question, no "go ahead". Hand-raise button = same event; UI toggle barge-in ON / push-to-talk only (+ hold-to-talk button).
- **Answering**: on UtteranceEnd → `search_kb` top-3 (hybrid vector + keyword rerank, ≤1500 tokens) → Groq streaming with tools `search_kb / goto_slide / resume_presenting` → sentence streamer → per-sentence TTS → base64 MP3 over the WS → client playback queue. Filler after 1.2 s without first token; fallback chain `GROQ_QA_MODEL → GROQ_FALLBACK_MODEL → canned "I'll have Amit follow up" clip` on 429 / error / >2.5 s to first token.
- `goto_slide` shows the slide temporarily and returns to the origin slide on resume. After the answer: "Anything else?" → 4 s window (speech loops, dismissal words or silence → "Great, let's continue." → resume at the START of the interrupted sentence). Deck end → `END` → `OPEN_QA` loop.
- Unanswered questions (model defers or all models fail) → `data/logs/unanswered.json`; each interruption → `data/logs/sessions/{id}.jsonl`.
- WS contract: `docs/ws.md`.

**Files**: `backend/session/{barge_in,session,sentences_stream,logs}.py`, `backend/llm/qa.py`, `backend/stt/*`, `frontend/src/audio/mic.ts`, `frontend/public/pcm-worklet.js`, tests `test_phase3_*.py`.

**Known issues**
- Deepgram, Groq and ElevenLabs/Aura have only been exercised against stubs/local fakes here (no keys in the sandbox); first real run may need minor tuning (e.g. Groq param acceptance — `reasoning_effort`/`include_reasoning` are sent only to the gpt-oss family and retried without on a 400).
- Echo guard cannot distinguish the user repeating a phrase that is literally in the current narration sentence (by design it's ignored).
- The user can't barge in on an *answer* (only while PRESENTING) — spec scope.
- Hash-embedder retrieval is lexical-ish; real MiniLM on your laptop is better.

**Verify manually**: Start presenting, say "I have a question" (or type it in the Simulate box), then ask "how much is the Growth plan?".

---

## Phase 4 — Demo polish
**What works**
- **Debug page** (`/debug`, also linked from the presenter): live interim/final transcript, state badge, trigger hits (accepted *and* ignored with the reason: confidence / cooldown / echo), model used, first-token / first-audio / total latency per answer, services (real vs fake), event log, and a *Simulate speech* box routed to the active session. Backed by `/ws/debug` (`session/hub.py`, replay of the last 200 events).
- **Session log**: every interruption `{ts, trigger, question, answer, model, first_token_ms, first_audio_ms, total_ms, fallback, unanswered}` → `data/logs/sessions/{id}.jsonl`; REST: `GET /sessions`, `GET /sessions/{id}/log`, `GET /logs/unanswered`. Unanswered → `data/logs/unanswered.json`.
- **End screen**: overlay with *Questions asked* and *Unanswered* (live-updated during open Q&A) + "Present again".
- **Offline safety**: presenting uses only cached MP3s (asserted: zero provider calls when the network is down), TTS circuit breaker (one failure → straight to fallback for 45 s), STT auto-reconnect with buffered audio, "Speech recognition offline / TTS degraded" banner, Q&A falls back model → fallback model → canned clip. Browser WS auto-reconnect resumes at the last playhead.
- **README** with setup, keys, run, demo checklist, troubleshooting (echo, 429, mic permission, …). Optional `docker-compose.yml` (untested: no docker in sandbox).

**Files**: `backend/session/hub.py`, `backend/api/sessions.py`, `frontend/src/pages/Debug.tsx`, `README.md`, `docker-compose.yml`, `Dockerfile.backend`, `frontend/Dockerfile`, `tests/test_phase4_polish.py`.

**Verified end to end** in headless Chromium against the running servers (mock services): upload → present → barge-in (136 ms browser round trip) → answer with captions → resume; deck end → end screen with asked/unanswered lists; Debug page shows model + latencies.

**Known issues**: see the final summary below.

---

## Final summary

**Run**: `make install && make dev` → http://localhost:5173 (backend :8000). `make test` runs 82 pytest tests + the frontend type-check/build.

**Real vs mocked in this build (sandbox had no API keys, HuggingFace blocked):**
| piece | status |
|---|---|
| PPTX parse, LibreOffice→PDF→PNG render, Chroma, FastAPI, WebSocket protocol, state machine, barge-in detector, echo guard, QA fallback chain, sentence streaming, caching, logs, React UI, WebAudio playback/fade, mic AudioWorklet | **real**, tested (Chromium for UI/audio) |
| Groq (narration + Q&A) | **mock** (`FakeLLM`) when no key; real `GroqLLM` verified only against a stubbed SDK stream |
| Deepgram STT | **mock** (`sim_transcript`) when no key; real client verified against a local websocket server |
| ElevenLabs / Aura TTS | **mock** (silent MP3 sized to the text) when no keys; request shapes verified with httpx MockTransport |
| Embeddings | MiniLM is used when `sentence-transformers` can load it; otherwise a hashing embedder (used in the sandbox) |

**Open issues / things to check on your laptop first**
1. Run once with real keys and watch the Debug page: tune `GROQ_REASONING_EFFORT`, the 2.5 s first-token timeout, or Deepgram `endpointing` if needed.
2. Echo is the main demo risk: use headphones or a quiet speaker; the guard cannot tell the user apart from the agent when the user repeats a phrase that is literally in the current narration sentence.
3. Only PRESENTING can be barged into (not an in-progress answer) — per spec.
4. `sentence-transformers` is in `requirements.txt` but not installed in the sandbox; the first real run downloads the model.
5. Docker files are unverified.

---

## Follow-up: presenter name + "Hello One" wake phrase
- `PRESENTER_NAME` default is now `Bytes Technolab developer` (`.env.example`); narration prompt tells the model to replace any other personal name in the notes with it.
- **Raise hand button removed** from the UI. Voice wake phrase **"Hello One"** (also "Hello 1", "hello won/wan") pauses the talk like any other trigger; strict fuzzy threshold (96) so "hello all" does not fire. `hand_raise` WS message kept for API/tests. Hint line under the controls.
- Tests: 96 passing.

## Follow-up 2: "Hello One" + visible listening state
- Wake phrase changed to **"Hello One"** (also "Hello 1", "hello won/wan"); strict match with word boundaries, so "hello once", "hello on", "hello 10", "hello all" do not fire. "Hello AI" no longer triggers.
- While a question is asked the talk is stopped (audio fades out in 150 ms, narration loop cancelled, nothing plays until the answer is done) and the slide shows a pulsing **"Listening… ask your question"** banner with the live transcript; **"Answering…"** while the answer plays. Verified in Chromium.
- Known flake: `test_debug_ws_streams_events_and_routes_simulated_speech` failed once in ~5 full runs under load (passes alone); not investigated further.

## Follow-up 3: human-like pointer
- New: an animated mouse arrow follows the line being talked about, during narration **and** answers. Curved ease-in-out path, small tremor, a "click" on arrival, then it glides along the line at speech pace with a highlighter. Component: `frontend/src/components/SlideCursor.tsx`.
- Backend: `ingest/layout.py` (line boxes from the LibreOffice PDF via `pdftotext -bbox-layout`; placeholder geometry otherwise) → `layout.json`; `ingest/cursor.py` (sentence→line: IDF-weighted keyword coverage with number-word normalisation + embedding) → `cursor.json`; pointer rides on `play_sentence` / `play_answer`. Answers can point at a line on another slide they were retrieved from (slide shown temporarily).
- **Decks uploaded before this change have no layout** → re-upload (or they just get no pointer).
- Tests: `tests/test_pointer.py` (15). Verified in Chromium: arrow travels smoothly, highlight on landing, works during answers.
- Known: hidden slides are skipped by LibreOffice's PDF export, which would shift page↔slide numbers (pre-existing limitation).

## Follow-up 4: "Okay Agent" + live reading highlighter
- Wake phrase is now **"Okay Agent"** (also "OK, agent", "o.k. agent"); strict word-bounded match. "Hello One"/"Hello AI" no longer trigger.
- **Live highlighter**: while a sentence is spoken, a marker paints over the slide text it is about, in step with the voice (soft box = what this sentence is about, strong fill = already said); wrapped bullets/titles are read line by line; words the speaker mentions pop out with an orange outline as the marker passes them; the mouse arrow rides the leading edge. Works for narration and answers. Word boxes come from `pdftotext -bbox-layout` (layout.json) – decks need a re-upload to get them.
- Timing is proportional to the clip length (not word-aligned to the audio) – visually in sync, not phoneme-exact.
- Tests: pointer/word-box/keyword tests added; wake-phrase tests updated.

## Fix: backend abort on macOS during answers
`failed assertion _status < MTLCommandBufferStatusCommitted ... IOGPUMetalCommandBuffer` killed the backend while answering: sentence-transformers ran on the Apple GPU (MPS) and was called from several threads (retrieval + pointer matching). `STEmbedder` now uses `device="cpu"` and a lock. Regression test added.

---

## Round 2: core hardening
- Orange word boxes removed from the highlighter (marker + arrow only).
- **Setup check** (`/check` page, `GET /preflight`): real requests verify LibreOffice/poppler, Groq key *and that the configured model ids exist*, Deepgram live-connection with the full parameter set, ElevenLabs key + voice id, embeddings, data folder; mic level meter; "Play voice test". Each failure says how to fix it.
- **Deepgram degrade**: HTTP 400 on optional params (keyterms/multilingual) → automatically retries with simpler settings; 401/403 → explicit "API key rejected" banner on the presenter page.
- **Warm-up at server start**: embedder, stock clips and the Groq TLS connection are prepared before the first question. Prompt: answer from the retrieved context first (fewer tool round trips), answer in the language of the question; sentence splitter understands the Hindi danda.
- **Interrupt an answer**: while the agent answers, "Okay Agent …", "wait", "excuse me", "stop", "enough" fade the audio out in 150 ms and hand the floor back (own-voice guard: ignored if the agent's current sentence contains that word).
- **Voice commands** after the wake phrase ("next slide", "go to slide 3", "pause", "continue", "repeat this slide", "start over"); strict whole-utterance matching so real questions still reach the LLM. The wake phrase also works while paused. Spoken navigation is acknowledged with a "Sure." clip.
- **Presenter UX**: keyboard shortcuts, thumbnail strip, progress bar, talk timer, full-screen stage mode, toasts, voice-command help.
- **Script editor + narration options**: edit/regenerate per slide (tone, length, free-form instruction), saved script re-indexes Q&A, refreshes highlighter targets, voices only changed sentences; orphaned audio removed. Audio URLs are versioned (`?v=mtime`) so browsers never replay stale audio.
- **Q&A report export** (`/sessions/{id}/export.md`), deck delete, ElevenLabs retries (429/5xx/network, honouring Retry-After) before falling back, "retry voice generation" if sentences fell back to silent mock audio, `WAKE_PHRASES` env.
- Tests: 165 passing.
- **Latency**: when Deepgram's endpointer says the speaker stopped (`speech_final`) and the final text is a finished sentence, the answer starts immediately instead of waiting for UtteranceEnd (~0.5 s saved; `endpointing=500`). Ambiguous endings still wait for UtteranceEnd.
- **Voice quality**: pre-generation sends the neighbouring sentences to ElevenLabs (`previous_text` / `next_text`) so intonation is continuous across clips.
- **Bug fixed (found via a flaky test)**: warm-up, session start and pre-generation could synthesise the same file concurrently and collide on the temp file → exception in the Q&A loop. `AudioCache` now serialises per file and uses unique temp names (+ regression test).
- **Setup check** also has a live *Test speech recognition + wake phrase* (mic → Deepgram → shows what it heard and confirms "Okay Agent"). Hold-to-talk button / **T** key are always available (noisy-room fallback).
- `make install` creates `.venv` itself (fixes macOS "externally-managed-environment"); Makefile uses it automatically; `make check` prints the setup check in the terminal. Tests: 172 passing.
- **Voice change**: cached audio now records which voice made it (voice id + model, or Aura voice). Changing `ELEVENLABS_VOICE_ID` re-voices every sentence and stock clip automatically (on the next session start, or `POST /decks/{id}/audio`). Before this fix old audio kept playing in the old voice.
- **Setup check, sharper**: lists the Groq models your key actually has when one is missing; tells an invalid ElevenLabs key from a restricted one (no Voices:read) and proves a restricted key with a real tiny synthesis; warns when `.env` was edited after the server started (the running server still has the old keys).
- **Fix: old (fallback) voice kept playing after ElevenLabs was fixed.** Audio cached while ElevenLabs failed (Aura voice) was treated as fresh forever. Now any clip not made by the preferred real voice is regenerated as soon as that voice is available (unless it is in a failure cool-down). The presenter header shows which voice is actually playing (`playing: elevenlabs`), deck audio records a per-provider count, and the upload card warns about sentences not in the configured voice.
- **Setup check now generates real speech** (not just a voice lookup). Found via "new voice not coming": a Voice-Library voice passes the lookup, but ElevenLabs refuses to speak with it on a free plan (HTTP 402 paid_plan_required) and the app silently used Aura. Now the check reports ElevenLabs' actual reason + fix (library voice / quota / permissions / rate limit), *Play voice test* says which voice really spoke and why it fell back, and the presenter shows a red banner "Not using your elevenlabs voice - aura is speaking instead: <reason>". Tests: 181 passing.

## Round 3: delete bug, tone on existing decks, in-app voice picker
- **Delete bug (ghost blank row)**: reproduced - deleting a deck while its audio/ingest was still running let the background task re-create a half-empty `meta.json`. Now deleted decks are tombstoned (writers skip them, ingest/pre-gen stop), `update_meta` never conjures a deck, and old ghosts are cleaned (only when >2 min old, so a deck being uploaded right now is safe). UI: inline "Delete? Yes/No" (no browser popup that can be blocked) and visible error messages.
- **Tone / length**: shown on every deck card; the Script page has a "Style for the whole deck" panel with **Rewrite ALL slides** (progress + audio regenerates) for decks that already exist (before, tone only applied at upload).
- **Voice picker** on `/check`: lists your ElevenLabs voices, **Test** speaks a sample and explains why a voice cannot be used (library voice on a free plan, quota, permissions), **Use this voice** proves it can speak, saves `ELEVENLABS_VOICE_ID` to `.env`, rebuilds the TTS chain live and re-voices all decks - no restart. Manual voice-id box for keys without Voices:read. CORS now only allows the local frontend (the endpoint writes `.env`).
- Tests: 190+ passing; browser-checked: delete, reload, tone display, rewrite-all, picker error path.
- **Stale-server trap**: `make dev` printed `Address already in use` (an old backend kept port 8000) while Vite still started - the page then talked to OLD code (stuck uploads, old voice behaviour). Now `make dev` refuses to start and says so, `make stop` frees ports 8000/5173, `/health` and the Check page show the backend's git version, the Upload page shows a red banner when the backend does not answer, and uploads fail with a message after 2 minutes instead of hanging.

## Round 4: showtime
- **Dress rehearsal** (`POST /selftest`, `/check` → *Run dress rehearsal*): asks the newest deck one real question through retrieval → Groq → first sentence → voice, shows the split (search / AI / voice) and the time to the first spoken word vs the 1.5 s target, flags fallback model / fallback voice / dead LLM. Do it right before going on stage.
- **Voice orb**: breathes when idle, pulses with the agent's actual voice level (Web Audio analyser) when speaking, with the microphone level when listening, spins while thinking.
- **Question card + sources**: the asked question appears on the stage with "Based on: Slide 5 · faq.pdf" chips (click a slide chip to jump there), then a "⚡ first word 0.9 s · answered in 4.2 s" badge. Backend sends `answer_start {question, sources}`.
- **Type or click to ask** (`ask` message): text box + "Try asking" chips (generated per deck from the notes by the LLM, cached in `suggestions.json`, `GET /decks/{id}/suggestions`). Same flow as a spoken question - a bullet-proof fallback in a noisy room and great for remote audiences.
- **3-2-1 countdown**, brand badge ("<name> · AI presenter"), and an **end-of-talk stats card** (slides, talk time, audience questions, average time to first word).
- Tests: 199 passing (new: typed questions, sources, suggestions, rehearsal, dead-LLM rehearsal).
