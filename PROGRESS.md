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
