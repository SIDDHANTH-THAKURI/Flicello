# Flicello

An AI video studio that runs on your own computer, with your own Claude. You talk to **Cello**, the editor on your
screen, and it makes the film: edits from your clips in any style, talking videos cut on your words, designed motion
graphics, music made for your film, cartoons written as code, AI-generated shots. Nothing is a template: Claude
designs each piece for what you asked. It runs only on your computer: no accounts, no cloud, and your footage never
leaves your machine except as the parts a step sends to the AI services you connect.

> Status: early and moving fast. Some parts are solid, some are experimental. See [what it can do](#what-it-can-do).

## Built for powerful machines

Flicello is made for the best result, not for every computer. It never lowers its quality to fit a smaller machine;
it tells you what yours has instead.

| | Built for |
|---|---|
| Memory | 32 GB or more |
| Graphics | NVIDIA GPU with 12 GB+ VRAM, or Apple Silicon M2 Pro or better |
| Disk | 100 GB free |
| CPU | 8+ threads |
| Claude | Claude Code, logged in with a Claude plan (Max recommended for heavy design work) |

It still runs below that, slowly, and the Connect page says so.

**Painting on your own GPU.** The designers paint the real things in a scene (people, creatures, objects, worlds).
With an NVIDIA GPU or Apple Silicon, Flicello paints them itself with an open model
([Qwen-Image-Edit-2509](https://huggingface.co/Qwen/Qwen-Image-Edit-2509), Apache-2.0, about 58 GB): install
`pip install -e ".[paint]"` (with the PyTorch build for your GPU) and the setup prompt downloads the model. No image
service, no per-picture cost. Without it, a Gemini key paints instead. On an NVIDIA GPU, install the CUDA build of the
model runtime once (`pip install -e ".[nvidia]"` after `pip uninstall onnxruntime onnxruntime-directml`): every
cut-out frame then runs on the GPU.

**Music and sound effects on your own GPU.** Your film's music is made by
[ACE-Step 1.5 XL](https://github.com/ace-step/ACE-Step-1.5) (MIT, about 12 GB) to the film's length and tempo, and
each sound effect by [Stable Audio 3 Small-SFX](https://huggingface.co/stabilityai/stable-audio-3-small-sfx) (about
4 GB; Powered by Stability AI, under the Stability AI Community License: free for commercial use under $1M a year in
revenue once you register at [stability.ai/community-license](https://stability.ai/community-license); trained on
licensed and Creative Commons audio).
Install `pip install -e ".[sound]"` and Stable Audio 3 itself (the setup prompt does both). Stable Audio's model is
gated: accept its licence on Hugging Face and log in there once (`huggingface-cli login`). No music service, no cost
per track. Without them, an ElevenLabs key makes the music and sounds instead.

## Quick start

You need [Python 3.11+](https://www.python.org/downloads/), [Node.js 22+](https://nodejs.org),
[FFmpeg 7+](https://ffmpeg.org/download.html) and [Claude Code](https://docs.anthropic.com/claude-code) (logged in).

```bash
git clone <this repo> flicello
cd flicello
./flicello          # macOS / Linux / Git Bash
flicello.cmd        # Windows
```

The first run sets up its Python environment, builds the app and the motion engine, then opens
**http://127.0.0.1:8765/connect** in your browser.

On the **Connect** page:

1. Copy the setup prompt into Claude Code: it checks your machine and installs whatever is missing (models, tools),
   explaining each step. It never touches your keys.
2. Add your keys. They stay in your computer's keychain, never in the repo, never sent anywhere but the service:
   - **Gemini** (optional): watches your footage with its sound, and is Cello's voice or the painter when this
     machine can't be. Without it, Claude watches from frames, words and sounds measured on your machine
     ([get one](https://aistudio.google.com/apikey))
   - **ElevenLabs** (optional): music and sound effects when this machine can't make them itself
     ([get one](https://elevenlabs.io/app/settings/api-keys))
   - optional: **Anthropic API** (instead of your Claude Code plan), **Higgsfield** (AI video generation),
     **Blender** (3D).

Everything shows green or red, live. If a service runs out of credit or refuses a key while you work, Connect and
Cello tell you plainly, with what to do.

## Who pays for what

You do, directly, and only for what you use: your Claude plan (or Anthropic API), your Gemini and ElevenLabs
accounts. Flicello itself is free and open source; it never sends your footage or keys anywhere else.

## What it can do

- **Edits from clips**: it watches your footage (with sound), understands it, pitches stories, cuts on the music's
  beats and hits, in styles (cinematic, hype, travel, documentary...) and formats (9:16, 16:9, 4:5, square).
- **Talking videos** cut on your words: pauses and "um"s out, never mid-word.
- **Designed motion graphics** (experimental): Claude designs each scene as code (HyperFrames + GSAP), paints real
  assets, tracks you through the shot, cuts you out cleanly to put things behind you, looks at its own frames, fixes
  them and passes a strict review.
- **Cartoons as code**: tell it a story (or give it your narration) and a director writes the board (the look, the
  cast painted as model sheets, every shot's size, angle and action), each shot is animated at its own desk as code
  with painted cut-out puppets and passes a strict animation director, then it is cut on music made for it, with a
  sound for every action. The result is a film like any other: editable on the timeline.
- **Blender** (optional): when it is installed, the scene and cartoon designers can write Blender scripts for real 3D,
  rendered with a transparent background and laid into the shot.
- **Music** made for your film (ElevenLabs), timed to it; sound on every graphic.
- **Changes in plain words** ("shot 3 out", "make the second half faster", "captions in karaoke style"), as instant
  drafts before anything is rendered; every version kept.
- **Effects**: 3D parallax photos, remove an object, upscale; AI-generated shots (Higgsfield).
- **Checks**: a ready-to-post score and an honest AI viewer's review.
- **Exports**: captions (SRT), 4:5, square, ProRes, GIF, thumbnail, MP3, and project files for Final Cut (FCPXML),
  Premiere and Resolve (EDL).
- **Cello**: talk to it (or type). It sees your screen, does things itself, speaks up when something finishes or
  fails, and asks when something is unclear. It can be turned off. Its voice runs on your machine: Whisper hears
  you, Claude (on your plan) thinks with Cello's tools in hand, and Kokoro speaks, sentence by sentence as it
  writes; speak over it and it stops. With a Gemini key and no local voice, Gemini Live is its voice instead.

## Under the hood

Python (FastAPI) + a Vite front end; FFmpeg for video; HyperFrames + GSAP for motion design; ONNX models for cut-outs
(BiRefNet, u2net, MODNet), depth (Depth Anything V2), object removal (LaMa) and upscaling (Real-ESRGAN);
faster-whisper for speech; Claude (Claude Code or the API) as the brain; Gemini Live for Cello's voice.

The command line and the older tools: [docs/vidosuper-cli.md](docs/vidosuper-cli.md).

## Tests

```bash
pip install -e ".[ai,studio,dev]"
pytest
```

## Licence

[MIT](LICENSE). Third-party parts keep their own licences: see [THIRD_PARTY.md](THIRD_PARTY.md) (models, engines,
GSAP's terms, services).
