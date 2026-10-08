"""Pydantic models for every WebSocket message. Contract documented in docs/ws.md."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Msg(BaseModel):
    model_config = ConfigDict(extra="ignore")

    def wire(self) -> dict:
        return self.model_dump(mode="json")


# ============================================================ client -> server
class StartSession(_Msg):
    type: Literal["start_session"] = "start_session"
    deck_id: str
    barge_in: bool = True
    slide_n: int | None = None  # resume position after a reconnect
    sentence_i: int = 0


class Control(_Msg):
    type: Literal["control"] = "control"
    action: Literal["start", "pause", "resume", "next", "prev", "goto", "restart"]
    slide_n: int | None = None


class HandRaise(_Msg):
    type: Literal["hand_raise"] = "hand_raise"


class SetBargeIn(_Msg):
    type: Literal["set_barge_in"] = "set_barge_in"
    enabled: bool


class Ptt(_Msg):
    """Push-to-talk: active=true pauses and listens; active=false ends the utterance."""
    type: Literal["ptt"] = "ptt"
    active: bool


class AudioEnded(_Msg):
    type: Literal["audio_ended"] = "audio_ended"
    play_id: str
    interrupted: bool = False


class SimTranscript(_Msg):
    """Inject a transcript as if it came from STT (mock STT / debugging)."""
    type: Literal["sim_transcript"] = "sim_transcript"
    text: str
    is_final: bool = True
    confidence: float = 0.95
    utterance_end: bool = False
    speech_final: bool = False


class Ask(_Msg):
    """A typed / clicked question (suggestion chip, text box): same flow as a spoken one, no microphone needed."""
    type: Literal["ask"] = "ask"
    text: str


class Ping(_Msg):
    type: Literal["ping"] = "ping"


ClientMessage = Annotated[
    Union[StartSession, Control, HandRaise, SetBargeIn, Ptt, AudioEnded, SimTranscript, Ask, Ping],
    Field(discriminator="type"),
]
_client_adapter = TypeAdapter(ClientMessage)


def parse_client_message(raw: dict) -> ClientMessage:
    return _client_adapter.validate_python(raw)


# ============================================================ server -> client
State = Literal["IDLE", "PRESENTING", "PAUSED", "LISTENING", "ANSWERING", "END", "OPEN_QA"]


class SessionReady(_Msg):
    type: Literal["session_ready"] = "session_ready"
    session_id: str
    deck_id: str
    slide_count: int
    presenter: str
    barge_in: bool
    services: dict[str, str]  # {"llm": "groq|fake", "stt": "deepgram|fake", "tts": "elevenlabs|aura|fake"}


class StateMsg(_Msg):
    type: Literal["state"] = "state"
    state: State
    reason: str = ""
    slide_n: int
    sentence_i: int
    barge_in: bool = True
    stt: str = "unknown"  # up | down | mock
    warnings: list[str] = Field(default_factory=list)  # degraded services (offline safety banner)


class PointerWord(_Msg):
    x0: float
    x1: float
    key: bool = False  # the word is mentioned in the spoken sentence -> emphasised as the highlighter passes it


class PointerLine(_Msg):
    x0: float
    y0: float
    x1: float
    y1: float
    words: list[PointerWord] = Field(default_factory=list)


class Pointer(_Msg):
    """The text being talked about; coordinates are 0..1 of the slide image. `lines` are read in order."""
    x0: float
    y0: float
    x1: float
    y1: float
    text: str = ""
    lines: list[PointerLine] = Field(default_factory=list)


class PlaySentence(_Msg):
    type: Literal["play_sentence"] = "play_sentence"
    play_id: str
    slide_n: int
    sentence_i: int
    text: str
    url: str
    next_url: str | None = None
    pointer: Pointer | None = None
    provider: str = ""  # which TTS made this audio (elevenlabs | aura | fake | cached)


class PlayClip(_Msg):
    type: Literal["play_clip"] = "play_clip"
    play_id: str
    clip: str
    text: str
    url: str


class PlayAnswer(_Msg):
    """One streamed answer sentence; audio is base64 MP3 (low-latency, no extra HTTP round trip)."""
    type: Literal["play_answer"] = "play_answer"
    play_id: str
    index: int
    text: str
    audio_b64: str
    final: bool = False
    slide_n: int | None = None  # slide being shown while this is spoken (pointer coordinates refer to it)
    pointer: Pointer | None = None


class Pause(_Msg):
    """Fade out and clear the client playback queue."""
    type: Literal["pause"] = "pause"
    fade_ms: int = 150
    reason: str = ""


class SlideMsg(_Msg):
    type: Literal["slide"] = "slide"
    slide_n: int
    temporary: bool = False  # True while answering (goto_slide); playhead is unchanged


class Transcript(_Msg):
    type: Literal["transcript"] = "transcript"
    text: str
    is_final: bool
    confidence: float = 1.0


class BargeInHit(_Msg):
    type: Literal["barge_in_hit"] = "barge_in_hit"
    trigger: str
    text: str
    score: float
    detect_ms: float
    source: str = "speech"  # speech | hand_raise | ptt


class ModelInfo(_Msg):
    type: Literal["model_info"] = "model_info"
    model: str
    first_token_ms: float | None = None
    first_audio_ms: float | None = None
    total_ms: float | None = None
    fallback_used: bool = False
    question: str = ""


class Source(_Msg):
    kind: Literal["slide", "doc"]
    slide_n: int | None = None
    name: str = ""


class AnswerStart(_Msg):
    """Sent as soon as retrieval is done: what was asked and which material the answer is grounded in."""
    type: Literal["answer_start"] = "answer_start"
    question: str
    sources: list[Source] = Field(default_factory=list)


class VoiceCommand(_Msg):
    """A spoken navigation command that was understood and executed (shown as a toast)."""
    type: Literal["voice_command"] = "voice_command"
    kind: str
    text: str
    slide_n: int | None = None


class Summary(_Msg):
    type: Literal["summary"] = "summary"
    questions: list[dict]
    unanswered: list[dict]


class ErrorMsg(_Msg):
    type: Literal["error"] = "error"
    message: str


class Pong(_Msg):
    type: Literal["pong"] = "pong"
