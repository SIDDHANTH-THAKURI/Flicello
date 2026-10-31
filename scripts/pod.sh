#!/usr/bin/env bash
# Flicello on a rented GPU (RunPod, Vast.ai...): everything kept on the network volume, so the first run sets it all
# up (about an hour, mostly model downloads) and every later pod on the same volume starts in a few minutes.
#
#   bash scripts/pod.sh            set up (or bring back) and start Flicello on port 8765
#   bash scripts/pod.sh --cartoons also fetch the video models (Wan 2.2 and AniSora: about 130 GB more)
# Sound effects (Stable Audio 3) are gated: accept the licence at huggingface.co/stabilityai/stable-audio-3-small-sfx
# and log in once: HF_HOME=$VOL/hf $VOL/venv/bin/hf auth login
#
# Then, on your own computer: ssh -N -L 8765:127.0.0.1:8765 <the pod's ssh>  and open http://127.0.0.1:8765
# The first time, log in to Claude Code once in the pod's terminal: CLAUDE_CONFIG_DIR=$VOL/claude claude
set -euo pipefail
VOL="${FLICELLO_VOLUME:-/workspace}"
REPO="$VOL/Flicello"
export HF_HOME="$VOL/hf" VIDO_MODELS_DIR="$VOL/models" VIDO_STUDIO_HOME="$VOL/studio" CLAUDE_CONFIG_DIR="$VOL/claude"
export NPM_CONFIG_PREFIX="$VOL/npm" PATH="$VOL/node/bin:$VOL/npm/bin:$PATH" PIP_CACHE_DIR="$VOL/pip-cache"
mkdir -p "$HF_HOME" "$VIDO_MODELS_DIR" "$VIDO_STUDIO_HOME" "$CLAUDE_CONFIG_DIR" "$NPM_CONFIG_PREFIX"
say() { printf '\n== %s\n' "$*"; }

say "System packages (the pod's own disk: every new pod needs them)"
# then what HyperFrames' headless Chrome needs to render motion graphics (without it scenes fail), and the GL loader
# Blender needs to reach the GPU (without libegl1 it renders on the CPU, minutes a frame instead of seconds)
if ! dpkg -s libcairo2-dev libnss3 libgbm1 libegl1 >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq ffmpeg pkg-config libcairo2-dev espeak-ng git curl xz-utils     libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 libxkbcommon0 libxcomposite1     libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 libxshmfence1 libx11-xcb1     fonts-liberation libegl1 libgl1 libglvnd0 libopengl0 >/dev/null
fi

say "Node.js and Claude Code (kept on the volume)"
if [ ! -x "$VOL/node/bin/node" ]; then
  curl -fsSL https://nodejs.org/dist/v22.17.0/node-v22.17.0-linux-x64.tar.xz | tar --no-same-owner -xJ -C "$VOL"
  mv "$VOL/node-v22.17.0-linux-x64" "$VOL/node"
fi
command -v claude >/dev/null || npm install -g --silent @anthropic-ai/claude-code

say "Flicello's code"
if [ -d "$REPO/.git" ]; then git -C "$REPO" pull -q --ff-only; else git clone -q https://github.com/SIDDHANTH-THAKURI/Flicello "$REPO"; fi
cd "$REPO"

say "Python packages (a venv on the volume, using the pod's own PyTorch)"
[ -x "$VOL/venv/bin/python" ] || python3 -m venv --system-site-packages "$VOL/venv"
PY="$VOL/venv/bin/python"
"$PY" -m pip install -q -e ".[ai,studio,voice,paint,sound,video,nvidia]"
"$PY" -m pip install -q --no-deps "kokoro-onnx>=0.4"
"$PY" -m pip install -q --no-deps "$("$PY" -c 'from vidosuper.local.checks import STABLE_AUDIO_3; print(STABLE_AUDIO_3)')"

say "The app's pages and the motion engine"
[ -f src/vidosuper/studio/web/static/dist/manifest.json ] || (cd frontend && npm ci --silent --no-audit --no-fund && npm run build --silent)
[ -e motion/node_modules/.bin/hyperframes ] || (cd motion && npm ci --silent --no-audit --no-fund)
(cd motion && HYPERFRAMES_NO_TELEMETRY=1 npx hyperframes browser ensure >/dev/null)  # its Chrome lives on the pod's own disk

say "Blender, for 3D motion design (kept on the volume)"
apt-get install -y -qq libxi6 libxkbcommon0 libsm6 libxrender1 libxxf86vm1 libgl1 libegl1 >/dev/null 2>&1 || true
if [ ! -x "$VOL/blender/blender" ]; then
  curl -fsSL https://download.blender.org/release/Blender4.5/blender-4.5.15-linux-x64.tar.xz | tar --no-same-owner -xJ -C "$VOL"
  mv "$VOL/blender-4.5.15-linux-x64" "$VOL/blender"
fi
export BLENDER_PATH="$VOL/blender/blender"

say "Cello's voice"
mkdir -p "$VIDO_MODELS_DIR/voice"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  [ -s "$VIDO_MODELS_DIR/voice/$f" ] || curl -fsSL -o "$VIDO_MODELS_DIR/voice/$f" \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f"
done

say "The models and FFmpeg 7 (the first time: about an hour of downloads)"
bash "$REPO/scripts/pod_models.sh" "${1:-}"
export PATH="$VOL/ffmpeg:$PATH"

say "What is ready and what is missing"
"$PY" -m vidosuper connect --json | "$PY" -c 'import json, sys
for i in json.load(sys.stdin).get("items", []):
    print("  %8s  %s" % (i["status"], i["label"]))' || true

if ! ls "$CLAUDE_CONFIG_DIR"/.credentials.json >/dev/null 2>&1; then
  say "Log in to Claude Code once: CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR claude   (then /exit, and run this again)"
fi

say "Starting Flicello on 127.0.0.1:8765 (logs: $VOL/studio.log)"
export LD_LIBRARY_PATH="$(ls -d "$VOL"/venv/lib/python3*/site-packages/nvidia/*/lib /usr/local/lib/python3*/dist-packages/nvidia/*/lib 2>/dev/null | tr '\n' ':')${LD_LIBRARY_PATH:-}"
nohup "$PY" -m vidosuper flicello --port 8765 --no-open > "$VOL/studio.log" 2>&1 &
echo "Flicello is starting. On your computer: ssh -N -L 8765:127.0.0.1:8765 <this pod's ssh>, then open http://127.0.0.1:8765"
