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
