"""Speech → text with word timings (faster-whisper, local CPU). Lets cuts avoid chopping words and
lets the AI find funny or meaningful lines."""

from __future__ import annotations

import threading
from pathlib import Path

import soundfile as sf
from pydantic import BaseModel, Field

TRANSCRIPT_VERSION = 2
_MODEL_LOCK = threading.Lock()
_MODELS: dict[str, object] = {}


class Word(BaseModel):
    start: float
    end: float
    text: str
    prob: float


class Utterance(BaseModel):
    start: float
    end: float
    text: str
    confident: bool = True  # False when Whisper itself doubts it (likely wind/traffic, not speech)
    words: list[Word] = Field(default_factory=list)


class Transcript(BaseModel):
    clip_id: str
    version: int = TRANSCRIPT_VERSION
    model: str
    language: str | None = None
    language_prob: float | None = None
    utterances: list[Utterance] = Field(default_factory=list)

    @property
    def has_speech(self) -> bool:
        return any(u.confident for u in self.utterances)

    def as_lines(self, only_confident: bool = False) -> list[str]:
        return [f"[{u.start:6.2f}-{u.end:6.2f}] {u.text}" + ("" if u.confident else "  (unsure)")
                for u in self.utterances if u.confident or not only_confident]


def _model(name: str, download_root: Path):
    with _MODEL_LOCK:
        if name not in _MODELS:
            from faster_whisper import WhisperModel

            _MODELS[name] = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(download_root))
        return _MODELS[name]


def transcribe(clip_id: str, wav16k: str | Path, model_name: str, download_root: Path) -> Transcript:
    model = _model(model_name, download_root)
    # Pass decoded samples: faster-whisper's own file decoding goes through PyAV, whose newer
    # releases dropped an argument faster-whisper 1.2 still uses.
    audio, sr = sf.read(str(wav16k), dtype="float32", always_2d=True)
    if sr != 16000:
        raise ValueError(f"{wav16k} must be 16 kHz, got {sr}")
    segments, info = model.transcribe(
        audio.mean(axis=1),
        vad_filter=True,  # skip silence/ambience: faster, and Whisper hallucinates less
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    return build_transcript(clip_id, model_name, segments, info)


def build_transcript(clip_id: str, model_name: str, segments, info) -> Transcript:
    """Whisper segments → Transcript, flagging lines that are probably noise rather than speech."""
    utterances = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        words = [Word(start=round(w.start, 2), end=round(w.end, 2), text=w.word.strip(), prob=round(w.probability, 3))
                 for w in (seg.words or [])]
        word_conf = sum(w.prob for w in words) / len(words) if words else 0.0
        confident = (seg.no_speech_prob < 0.6 and seg.avg_logprob > -1.0 and seg.compression_ratio < 2.4
                     and word_conf >= 0.5)
        utterances.append(Utterance(start=round(seg.start, 2), end=round(seg.end, 2), text=text,
                                    confident=confident, words=words))
    # A few short "words" in a language Whisper isn't sure about is noise (brakes hissing, wind), not speech.
    if info.language_probability < 0.6 and sum(len(u.words) for u in utterances) < 6:
        for u in utterances:
            u.confident = False
    return Transcript(clip_id=clip_id, model=model_name, language=info.language,
                      language_prob=round(info.language_probability, 3), utterances=utterances)
