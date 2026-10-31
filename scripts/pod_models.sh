#!/bin/bash
# Flicello's models onto the volume, and FFmpeg 7 (scripts/pod.sh runs it). Safe to run again: what is there is
# skipped. --cartoons also fetches the video models (Wan 2.2, AniSora: about 130 GB).
set -uo pipefail
VOL="${FLICELLO_VOLUME:-/workspace}"; PY=$VOL/venv/bin/python
export HF_HOME=$VOL/hf VIDO_MODELS_DIR=$VOL/models HF_HUB_ENABLE_HF_TRANSFER=0
M=$VOL/models; mkdir -p $M/depth $M/inpaint $M/upscale
say() { printf '\n== %s (%s)\n' "$*" "$(date +%H:%M:%S)"; }

say "FFmpeg 7"
if [ ! -x $VOL/ffmpeg/ffmpeg ]; then
  mkdir -p $VOL/ffmpeg && curl -fsSL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    | tar --no-same-owner -xJ -C $VOL/ffmpeg --strip-components=1
fi
$VOL/ffmpeg/ffmpeg -version | head -1

say "Cut-outs, depth, object removal"
get() { [ -s "$2" ] || curl -fsSL -o "$2" "$1"; ls -la "$2" | awk '{print $5, $9}'; }
get https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx $M/u2net_human_seg.onnx
get https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/resolve/main/onnx/model.onnx $M/birefnet_lite.onnx
get https://huggingface.co/Xenova/modnet/resolve/main/onnx/model.onnx $M/modnet.onnx
get https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx $M/depth/depth_anything_v2_small.onnx
get https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx $M/inpaint/lama.onnx

say "The painter (Qwen-Image-Edit-2509, about 58 GB)"
$PY -c "from huggingface_hub import snapshot_download as s; print(s('Qwen/Qwen-Image-Edit-2509'))"
$PY -c "from huggingface_hub import hf_hub_download as d; print(d('lightx2v/Qwen-Image-Lightning', 'Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-8steps-V1.0-bf16.safetensors'))"

say "The music maker (ACE-Step 1.5 XL, about 12 GB)"
$PY -c "from huggingface_hub import snapshot_download as s; print(s('ACE-Step/acestep-v15-xl-sft-diffusers'))"

say "Sound effects (Stable Audio 3: gated, needs the licence accepted and hf auth login)"
$PY -c "from huggingface_hub import snapshot_download as s; print(s('stabilityai/stable-audio-3-small-sfx'))" 2>&1 | tail -1   || echo "not yet: accept the licence at huggingface.co/stabilityai/stable-audio-3-small-sfx, then: HF_HOME=$HF_HOME $VOL/venv/bin/hf auth login"

say "Whisper for Cello and talks"
cd $VOL/Flicello && $PY -c "from vidosuper.local import voice; voice._whisper(); print('whisper ok')" 2>&1 | tail -1

if [ "${1:-}" = "--cartoons" ]; then
say "Cartoons: Wan 2.2 (about 70 GB)"
cd $VOL/Flicello && $PY -m vidosuper.motion.videogen prepare cartoon 2>&1 | tail -3

say "Anime: AniSora V3.2 (about 70 GB)"
cd $VOL/Flicello && $PY -m vidosuper.motion.videogen prepare anime 2>&1 | tail -3
fi

say "Models done"; du -sh $VOL 2>/dev/null
