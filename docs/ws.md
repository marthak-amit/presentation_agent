# WebSocket contract

Source of truth: `backend/session/messages.py` (Pydantic models). Everything is JSON text unless noted.

* **`/ws/session`** – one connection per presenter page (one `PresenterSession` on the server).
* **`/ws/debug`** – read-only live feed for the Debug page (see bottom).

Binary frames client → server on `/ws/session` are **mic audio**: PCM16 little-endian, mono, **16 kHz**, ~100 ms chunks.
They are forwarded to Deepgram (or ignored by the mock STT) – also while the agent is speaking.

## State machine

```
IDLE ──start──▶ PRESENTING ──trigger/hand-raise/PTT──▶ PAUSED ──▶ LISTENING ──utterance end──▶ ANSWERING
                   ▲  │                                  ▲            ▲                          │
                   │  └─ deck finished ─▶ END ─▶ OPEN_QA │            └──── "Anything else?" ◀───┤
                   └──────────── "Great, let's continue." (resume at START of interrupted sentence)┘
```
`PAUSED` is also entered by the user's Pause button (`reason:"user"`). `OPEN_QA` answers questions until the page is closed.

## Client → server

| `type` | fields | meaning |
|---|---|---|
| `start_session` | `deck_id`, `barge_in=true`, `slide_n?`, `sentence_i=0` | **must be first.** `slide_n/sentence_i` resume position after a reconnect |
| `control` | `action`: `start\|pause\|resume\|next\|prev\|goto\|restart`, `slide_n?` | transport. `resume` while LISTENING/ANSWERING skips the Q&A |
| `hand_raise` | – | same pause event as a voice trigger. No UI button any more (voice command **\"Okay Agent\"** replaced it); kept for API/tests |
| `set_barge_in` | `enabled` | UI toggle: voice barge-in ON / push-to-talk only |
| `ptt` | `active` | push-to-talk. `true` pauses & listens (no "go ahead"); `false` ends the utterance immediately |
| `audio_ended` | `play_id`, `interrupted=false` | client finished (or was cut off) playing an item. **Server sequencing depends on this**; if it never arrives the server times out and continues |
| `sim_transcript` | `text`, `is_final=true`, `confidence=0.95`, `utterance_end=false` | inject a transcript as if from STT (mock STT, debugging, tests) |
| `ping` | – | → `pong` |

## Server → client

| `type` | fields | notes |
|---|---|---|
| `session_ready` | `session_id, deck_id, slide_count, presenter, barge_in, services{llm,stt,tts}` | |
| `state` | `state, reason, slide_n, sentence_i, barge_in, stt` | on every transition. `stt` = `up\|down\|mock` |
| `play_sentence` | `play_id, slide_n, sentence_i, text, url, next_url?, pointer?` | narration sentence; `url` is a cached MP3 under `/media/…`. Client shows `slide_n` + caption when playback **starts**, preloads `next_url` |
| `play_clip` | `play_id, clip, text, url` | stock clip (`go_ahead, anything_else, continue, filler, followup, open_qa`) under `/stock/…` |
| `play_answer` | `play_id, index, text, audio_b64, final, slide_n?, pointer?` | one streamed answer sentence, base64 MP3. Queue them; play in order |
| `pause` | `fade_ms=150, reason` | fade out current audio over `fade_ms` and **clear the queue** |
| `slide` | `slide_n, temporary` | `temporary=true` = `goto_slide` during an answer; the playhead is unchanged and the origin slide returns on resume |
| `transcript` | `text, is_final, confidence` | live STT output (interim + final) |
| `barge_in_hit` | `trigger, text, score, detect_ms, source` | `source`: `speech\|hand_raise\|ptt` |
| `model_info` | `model, first_token_ms, first_audio_ms, total_ms, fallback_used, question` | after each answer (and with `fallback_used` when the chain switches model). Times are measured from the end of the question (UtteranceEnd) |
| `summary` | `questions[], unanswered[]` | end screen data, refreshed after every question |
| `error` | `message` | |
| `pong` | – | |

### Animated pointer

`play_sentence` and `play_answer` may carry `pointer: {x0,y0,x1,y1,text,lines[]}` – the slide text being talked about,
normalised 0..1 of the slide image. `lines[]` = `{x0,y0,x1,y1,words[{x0,x1,key}]}` read in order (a short wrapped bullet is highlighted whole);
`key` marks words the speaker mentions. The client applies it when that item **starts playing**: a mouse arrow glides along a
curved path to the start of the text, then a marker paints over it in step with the voice (strong fill = already said) while
mentioned words pop out as it passes them.
`pointer: null` = nothing matches (arrow stays where it is). An answer may carry a `slide_n` different from the playhead
slide when its sentence is about a line of another slide it was drawn from; the client shows that slide until the talk resumes.
Boxes come from `data/decks/{id}/layout.json` (`pdftotext -bbox-layout` of the LibreOffice PDF) matched to sentences
(keyword/number/embedding score) in `cursor.json`; answer sentences are matched live.

### Barge-in sequence (what the client sees)

1. `barge_in_hit` → `pause {fade_ms:150}` → `state PAUSED` → `state LISTENING` → `play_clip go_ahead`
   (skipped when more than 4 words already follow the trigger).
2. user speaks → `transcript*` → UtteranceEnd (server side) → `state ANSWERING`
   → optional `play_clip filler` (no first token within 1.2 s) → `play_answer*` → `model_info` → `summary`
3. `state LISTENING` → `play_clip anything_else` → 4 s of silence → `play_clip continue` → `state PRESENTING`
   → `slide` → `play_sentence` for the **start of the interrupted sentence**.
   Speech inside the 4 s window loops back to step 2.

## `/ws/debug`

Server → client: `{"session_id": "...", "ts": 1234.5, "event": {…any /ws/session server message…}}`
(audio payloads are replaced by a size placeholder). On connect the last ~200 events are replayed.
Client → server: `{"type":"sim_transcript","session_id":"…","text":"…","is_final":true,"utterance_end":true}` is routed to that session.
