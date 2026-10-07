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
