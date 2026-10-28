"""Cello's voice on this machine: no voice service, no key.

- Ears: the page's microphone (16 kHz, 40 ms at a time) cut into what they say by the pauses (an end is a second of
  quiet, as Gemini Live was set), then written down by Whisper (faster-whisper; large-v3-turbo on a GPU).
- Mind: Claude, on their own Claude Code plan, in one long conversation (`claude -p` with streamed input and output),
  with Cello's instructions and Cello's tools (studio/cello_mcp.py) as its hands.
- Voice: Kokoro-82M (Apache-2.0, through kokoro-onnx; its words become sounds through espeak-ng), sentence by
  sentence as Claude writes, so it starts talking before the whole answer is written.

`Live` speaks the same messages as Gemini Live does to the page (setupComplete, serverContent with the audio, what
was heard and said, interrupted and turnComplete, toolCall / toolResponse), so the page drives both the same way."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

RATE_IN = 16000
RATE_OUT = 24000
MODEL = os.environ.get("VIDO_CELLO_MODEL", "claude-haiku-5-5")  # the conversation: fast; real work goes to `work`
VOICE = os.environ.get("VIDO_CELLO_VOICE", "am_michael")
SILENCE = 1.0  # seconds of quiet that end what they say
PRE_ROLL = 0.3  # seconds kept from before the voice began (its first sound)
LOCAL_NOTE = """

# Speaking
You are heard, not read: what you write is spoken aloud by a voice as you write it. Write plain spoken sentences,
short ones, the way an editor talks: no lists, no markdown, no headings, no emoji, no code, no URLs read out. Numbers
as people say them. Use your tools by calling them; never describe a tool call in words. Messages in brackets or
starting with "(Note" are from Flicello, not the person: act on them and don't read them back."""


class VoiceError(RuntimeError):
    pass


def models_dir() -> Path:
    return Path(os.environ.get("VIDO_MODELS_DIR") or Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
                / "VidoSuper" / "models")


def voice_files() -> tuple[Path, Path]:
    d = models_dir() / "voice"
    return d / "kokoro-v1.0.onnx", d / "voices-v1.0.bin"


def available() -> bool:
    """Cello can speak here without a voice service: Kokoro's files and its package."""
    model, voices = voice_files()
    if not (model.exists() and voices.exists()):
        return False
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------- the voice

@lru_cache(maxsize=2)
def _kokoro(cpu: bool = False):
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    if hasattr(ort, "preload_dlls"):  # CUDA and cuDNN from the pip packages, so the GPU build finds them
        try:
            ort.preload_dlls()
        except Exception:  # noqa: BLE001 - without them it runs on the CPU
            pass

    from ..graphics.device import providers

    model, voices = voice_files()
    if not (model.exists() and voices.exists()):
        raise VoiceError(f"Cello's voice isn't downloaded: put kokoro-v1.0.onnx and voices-v1.0.bin in {model.parent}")
    chosen = ["CPUExecutionProvider"] if cpu else providers(model)
    return Kokoro.from_session(ort.InferenceSession(str(model), providers=chosen), str(voices))


def warm() -> None:
    """Whisper and Kokoro loaded and run once: on a GPU their first call compiles kernels (Kokoro took 36 s, Whisper
    6 s on an RTX PRO 4500), which would otherwise land on the first thing the person says to Cello."""
    if not available():
        return
    hear(np.zeros(RATE_IN, np.float32))
    speak("Ready.")


def warm_in_background() -> None:
    if os.environ.get("VIDO_CELLO_WARM", "1") == "0" or not available():
        return
    threading.Thread(target=lambda: _quietly(warm), daemon=True, name="cello-warm").start()


def _quietly(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - warming is a head start; a real turn says what is wrong
        pass


def speak(text: str) -> np.ndarray:
    """The words spoken in Cello's voice: 24 kHz mono int16. A GPU that can't run the voice hands it to the CPU, and
    that is remembered (graphics/device.py)."""
    from ..graphics.device import _gave_up

    k = _kokoro()
    try:
        audio, rate = k.create(text, voice=VOICE, speed=1.0, lang="en-us")
    except Exception:  # noqa: BLE001 - a GPU that starts but can't run it: the CPU can
        if k.sess.get_providers()[0] == "CPUExecutionProvider":
            raise
        _gave_up(voice_files()[0])
        _kokoro.cache_clear()
        audio, rate = _kokoro(cpu=True).create(text, voice=VOICE, speed=1.0, lang="en-us")
    audio = np.asarray(audio, np.float32)
    if rate != RATE_OUT:  # the page plays 24 kHz
        n = int(round(len(audio) * RATE_OUT / rate))
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)


SENTENCE = re.compile(r"(.+?[.!?…:;])(\s+|$)", re.S)


def sentences(buffer: str, final: bool = False) -> tuple[list[str], str]:
    """The whole sentences at the start of `buffer` (and the rest, still being written). `final`: all of it."""
    out, rest = [], buffer
    while True:
        m = SENTENCE.match(rest)
        if not m or (not m.group(2) and not final):
            break
        out.append(m.group(1).strip())
        rest = rest[m.end():]
    if final and rest.strip():
        out.append(rest.strip())
        rest = ""
    return [s for s in out if re.search(r"\w", s)], rest


# ---------------------------------------------------------------- the ears

@lru_cache(maxsize=1)
def _whisper():
    from ..config import load_config
    from ..perceive.transcribe import _device, _model

    cfg = load_config()
    name = "large-v3-turbo" if _device()[0] == "cuda" else cfg.analysis.whisper_model
    return _model(name, cfg.models_root / "whisper")


def hear(audio: np.ndarray) -> str:
    """What was said in a stretch of 16 kHz float audio."""
    segments, _ = _whisper().transcribe(audio, vad_filter=True, beam_size=5, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


class Ears:
    """What they say, cut by its pauses: `feed` the microphone's 16-bit PCM as it comes; `on_start` when they begin
    to speak, `on_speech` with each finished stretch (float32, 16 kHz)."""

    def __init__(self, on_start, on_speech, silence: float = SILENCE):
        self.on_start, self.on_speech = on_start, on_speech
        self.silence = silence
        self.floor = 0.004  # the room's own level, followed while they are quiet
        self.pre: list[np.ndarray] = []
        self.voiced: list[np.ndarray] = []
        self.loud_run = 0
        self.quiet = 0.0
        self.speaking = False

    def feed(self, pcm: bytes) -> None:
        x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
        if not len(x):
            return
        rms = float(np.sqrt(np.mean(x * x)))
        loud = rms > max(0.012, self.floor * 3.5)
        seconds = len(x) / RATE_IN
        if not self.speaking:
            if not loud:
                self.floor = 0.95 * self.floor + 0.05 * rms
            self.pre.append(x)
            while sum(len(p) for p in self.pre) > PRE_ROLL * RATE_IN:
                self.pre.pop(0)
            self.loud_run = self.loud_run + 1 if loud else 0
            if self.loud_run >= 3:  # 120 ms of voice: they are speaking
                self.speaking, self.quiet = True, 0.0
                self.voiced = list(self.pre)
                self.pre = []
                self.on_start()
            return
        self.voiced.append(x)
        self.quiet = 0.0 if loud else self.quiet + seconds
        if self.quiet >= self.silence:
            stretch = np.concatenate(self.voiced)
            self.speaking, self.voiced, self.loud_run = False, [], 0
            if len(stretch) > 0.35 * RATE_IN:
                self.on_speech(stretch)


# ---------------------------------------------------------------- the mind: Claude Code, one long conversation

class Mind:
    """Claude on their plan, as one conversation that stays open: `say` a turn; its words arrive at `on_text` as it
    writes them, `on_done` when the turn ends."""

    def __init__(self, folder: Path, url: str, token: str, on_text, on_done, on_error):
        from ..local import claude_exe, plan_env
        from ..studio.cello import SYSTEM

        self.on_text, self.on_done, self.on_error = on_text, on_done, on_error
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "system.md").write_text(SYSTEM + LOCAL_NOTE, encoding="utf-8")
        (folder / "mcp.json").write_text(json.dumps({"mcpServers": {"cello": {
            "command": sys.executable, "args": ["-m", "vidosuper.studio.cello_mcp"],
            "env": {"FLICELLO_URL": url, "CELLO_LIVE_TOKEN": token}}}}), encoding="utf-8")
        api = os.environ.get("VIDO_SCENES_VIA") == "api"
        env = dict(os.environ) if api else plan_env()
        self.proc = subprocess.Popen(
            [claude_exe(), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
             "--include-partial-messages", "--model", MODEL, "--system-prompt-file", str(folder / "system.md"),
             "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config", "--mcp-config",
             str(folder / "mcp.json"), "--tools", "", "--allowedTools", "mcp__cello"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=str(folder), env=env)
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True, name="cello-mind").start()

    def _write(self, msg: dict) -> None:
        with self.lock:
            if self.proc.poll() is not None:
                raise VoiceError("Cello's mind has stopped")
            self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self.proc.stdin.flush()

    def say(self, text: str) -> None:
        self._write({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    def interrupt(self) -> None:
        """They spoke over it: the turn being written stops."""
        try:
            self._write({"type": "control_request", "request_id": secrets.token_hex(4),
                         "request": {"subtype": "interrupt"}})
        except (VoiceError, OSError):
            pass

    def _read(self) -> None:
        for raw in self.proc.stdout:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            kind = e.get("type")
            if kind == "stream_event":
                ev = e.get("event") or {}
                delta = ev.get("delta") or {}
                if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    self.on_text(delta.get("text", ""))
                elif ev.get("type") == "content_block_start" and (ev.get("content_block") or {}).get("type") == "text":
                    self.on_text(" ")  # a new block of words after a tool: they don't run into the last ones
            elif kind == "result":
                if e.get("is_error") and re.search(r"(session|usage|weekly) limit|hit your limit",
                                                   str(e.get("result") or ""), re.I):
                    self.on_error("the Claude plan's limit is reached")
                self.on_done()
        self.on_error("Cello's mind has stopped")

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
        except OSError:
            pass


# ---------------------------------------------------------------- one voice session with the page

SESSIONS: dict[str, "Live"] = {}
# its name, as Whisper writes it when said on its own or at the start ("Cello", "hey Cello", "chello, ...")
WAKE = re.compile(r"^\W*(?:(?:hey|hi|ok|okay|yo|oh)\W+)?(?:cello|chello|chelo|celo|cellow|sello|chelloh|tello)\b\W*", re.I)


def woken(said: str) -> tuple[bool, str]:
    """Whether what was heard calls Cello by its name, and what they said after it."""
    m = WAKE.search(said.strip())
    if not m:
        return False, ""
    return True, said.strip()[m.end():].strip()


class Live:
    """One page's conversation with Cello on this machine, speaking Gemini Live's messages to that page."""

    def __init__(self, url: str, send, loop: asyncio.AbstractEventLoop):
        self.token = secrets.token_urlsafe(24)
        self.send, self.loop = send, loop
        self.pending: dict[str, asyncio.Future] = {}
        self.turn_text = ""
        self.sent = 0  # turns given to the mind
        self.finished = 0  # turns it has ended
        self.cut_upto = 0  # they spoke over these turns: what is left of them is dropped
        self.talking = 0  # sentences being spoken
        self.done = False
        self.dozing = False  # asleep, listening only for its name
        self.note_next = ""  # said to the mind with the next thing they say
        self.mouth = asyncio.Queue()
        self.folder = Path(tempfile.mkdtemp(prefix="cello-live-"))
        self.ears = Ears(self._started, self._heard)
        self.mind = Mind(self.folder, url, self.token, self._text, self._turn_done, self._failed)
        SESSIONS[self.token] = self
        self.speaker = loop.create_task(self._speak_loop())

    def _emit(self, msg: dict) -> None:
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.send(msg)))

    # what they say
    def audio(self, b64: str) -> None:
        self.ears.feed(base64.b64decode(b64))

    def text(self, text: str) -> None:
        self._say(text)

    def _say(self, text: str) -> None:
        if self.note_next:
            text, self.note_next = f"{self.note_next}\n{text}", ""
        self.sent += 1
        self.mind.say(text)

    @property
    def cut(self) -> bool:
        """The turn being written (or spoken) is one they spoke over."""
        return self.finished + 1 <= self.cut_upto

    def doze(self, on: bool) -> None:
        """Asleep (only its name wakes it) or awake again (woken by a tap)."""
        was, self.dozing = self.dozing, bool(on)
        if on:
            self._started_cut()
        elif was:
            self.note_next = "(Note, not them talking: you're awake again: a tap or their typing woke you.)"

    def _started_cut(self) -> None:
        self.cut_upto = self.sent
        self.turn_text = ""
        self.mind.interrupt()
        self.loop.call_soon_threadsafe(self._drain_mouth)

    def _started(self) -> None:
        if self.dozing:
            return
        if self.talking or self.turn_text or self.sent > self.finished:  # they spoke over it
            self.cut_upto = self.sent
            self.turn_text = ""
            self.mind.interrupt()
            self._emit({"serverContent": {"interrupted": True}})
            self.loop.call_soon_threadsafe(self._drain_mouth)

    def _heard(self, stretch: np.ndarray) -> None:
        def work():
            said = hear(stretch)
            if not said:
                return
            if self.dozing:  # asleep: only its name counts, and what they say after it is the request
                called, rest = woken(said)
                if not called:
                    return
                self.dozing = False
                self._emit({"cello": {"woke": True}})
                self._emit({"serverContent": {"inputTranscription": {"text": said}}})
                if re.search(r"\w{2,}", rest):  # "Cello, cut shot three": awake, and that is the request
                    self.note_next = ("(Note, not them talking: you're awake again: they woke you by saying your "
                                      "name. Do what they ask.)")
                    self._say(rest)
                else:
                    self._say("(Note, not them talking: you're awake again: they woke you by saying your name. "
                              "Answer in two or three words.)")
                return
            self._emit({"serverContent": {"inputTranscription": {"text": said}}})
            self._say(said)
        threading.Thread(target=work, daemon=True, name="cello-ears").start()

    # what it says
    def _text(self, delta: str) -> None:
        if self.cut:
            return
        self.turn_text += delta
        done, self.turn_text = sentences(self.turn_text)
        for s in done:
            self.loop.call_soon_threadsafe(self.mouth.put_nowait, s)

    def _turn_done(self) -> None:
        rest = [] if self.cut else sentences(self.turn_text, final=True)[0]
        self.turn_text = ""
        self.finished += 1
        for s in rest:
            self.loop.call_soon_threadsafe(self.mouth.put_nowait, s)
        self.loop.call_soon_threadsafe(self.mouth.put_nowait, None)  # the turn's end, after its last words

    def _failed(self, why: str) -> None:
        if not self.done:
            self._emit({"serverContent": {"outputTranscription": {"text": f"({why})"}, "turnComplete": True}})

    def _drain_mouth(self) -> None:
        while not self.mouth.empty():
            self.mouth.get_nowait()

    async def _speak_loop(self) -> None:
        while not self.done:
            s = await self.mouth.get()
            if s is None:
                await self.send({"serverContent": {"turnComplete": True}})
                continue
            if self.cut:
                continue
            self.talking += 1
            try:
                pcm = await asyncio.to_thread(speak, s)
            except Exception as exc:  # noqa: BLE001 - said in words when it can't be said aloud
                await self.send({"serverContent": {"outputTranscription": {"text": f"{s} (Cello's voice failed: {exc})"}}})
                self.talking -= 1
                continue
            self.talking -= 1
            if self.cut:
                continue
            await self.send({"serverContent": {
                "modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000",
                                                        "data": base64.b64encode(pcm.tobytes()).decode("ascii")}}]},
                "outputTranscription": {"text": s + " "}}})

    # its hands: a tool runs on the page, and its answer comes back
    async def tool(self, name: str, args: dict) -> dict:
        cid = secrets.token_hex(6)
        fut = self.loop.create_future()
        self.pending[cid] = fut
        await self.send({"toolCall": {"functionCalls": [{"id": cid, "name": name, "args": args}]}})
        try:
            return await asyncio.wait_for(fut, 900)
        finally:
            self.pending.pop(cid, None)

    def answered(self, responses: list[dict]) -> None:
        for r in responses:
            fut = self.pending.get(str(r.get("id")))
            if fut is not None and not fut.done():
                fut.set_result((r.get("response") or {}))

    def close(self) -> None:
        self.done = True
        SESSIONS.pop(self.token, None)
        self.mind.close()
        self.speaker.cancel()
        for fut in self.pending.values():
            if not fut.done():
                fut.set_result({"result": "Not done: the page closed"})
