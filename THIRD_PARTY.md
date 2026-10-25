# Third-party software, models and services

Flicello's own code is MIT (see [LICENSE](LICENSE)). It uses the parts below, each under its own terms. None of them
is copied into this repository: packages are installed by `pip` and `npm`, and the AI models are downloaded to your
machine by the setup prompt (the Connect page).

## AI models (downloaded to your machine)

| Model | Used for | Licence |
|---|---|---|
| [BiRefNet lite](https://github.com/ZhengPeng7/BiRefNet) | the clean cut-out of a person | MIT |
| [MODNet](https://github.com/ZHKKKe/MODNet) | the quick cut-out, portrait matting | Apache-2.0 |
| [U²-Net human segmentation](https://github.com/xuebinqin/U-2-Net) (via [rembg](https://github.com/danielgatis/rembg)) | finding people in a frame | Apache-2.0 (rembg: MIT) |
| [Depth Anything V2 Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small) | depth for 3D parallax photos | Apache-2.0 (only the Small model is; Base and Large are not) |
| [LaMa](https://github.com/advimman/lama) | removing an object from a shot | Apache-2.0 |
| [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) | upscaling | BSD-3-Clause |
| [Qwen-Image-Edit-2509](https://huggingface.co/Qwen/Qwen-Image-Edit-2509) (optional) | painting on your own GPU | Apache-2.0 |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (ONNX files from [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx)) | Cello's voice | Apache-2.0 (kokoro-onnx: MIT) |
| [Whisper](https://github.com/openai/whisper) via [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | speech to text | MIT |

## Engines and libraries

| Part | Used for | Licence |
|---|---|---|
| [HyperFrames](https://github.com/heygen-com/hyperframes) | rendering designed scenes and graphics frame by frame | Apache-2.0 |
| [GSAP](https://gsap.com) | the animation in designed scenes (written by Claude) | Webflow's GSAP Standard "No Charge" licence (see below) |
| [FFmpeg](https://ffmpeg.org) | everything video and audio | LGPL/GPL (installed by you) |
| [Blender](https://www.blender.org) (optional) | 3D in cartoons and scenes | GPL (installed by you; Flicello only runs it) |
| [ONNX Runtime](https://onnxruntime.ai) | running the models on your GPU or CPU | MIT |
| [PyTorch](https://pytorch.org), [Diffusers](https://github.com/huggingface/diffusers) (optional) | running the painter | BSD-3-Clause, Apache-2.0 |
| [mediabunny](https://github.com/Vanilagy/mediabunny) | shrinking uploads in the browser | MPL-2.0 |
| [phonemizer](https://github.com/bootphon/phonemizer) and [espeak-ng](https://github.com/espeak-ng/espeak-ng) (the `voice` extra) | turning Cello's words into sounds for Kokoro | GPL-3.0 (installed by you, not part of this repository: if you ship Flicello bundled with them, the GPL applies to that bundle) |
| Fonts: Bricolage Grotesque, Instrument Serif, Geist Mono (and the fonts scenes use) | type | SIL Open Font License 1.1 |

Python and JavaScript dependencies are listed in `pyproject.toml`, `frontend/package.json` and `motion/package.json`.

## GSAP

GSAP is free to use under Webflow's Standard "No Charge" licence. That licence does not allow GSAP to be used in a
tool that lets people build visual animations without code in a way that competes with Webflow's own visual
animation building, unless Webflow agrees in writing.

How Flicello uses it: every animation is code that Claude writes for one scene, rendered into a video; nothing in
Flicello is a visual animation builder, and its output is video, not websites. The editor's timeline can move a
graphic in time, change its words or its look, as a video editor does with any layer. We believe this is not a
prohibited use, but we have not asked Webflow. If you build on Flicello, read the licence yourself:
<https://gsap.com/community/standard-license/>. GSAP is installed from npm when you set Flicello up; it is not
included in this repository.

## Services (your own accounts and keys)

Claude (Claude Code with your plan, or the Anthropic API), Google Gemini, ElevenLabs and, if you use them, Higgsfield.
Their own terms apply to what you send them and what they make. Flicello sends each of them only what the step needs
and never sends your keys anywhere else.
