# VidoSuper Studio: the web app and its worker in one Linux container.
#   docker compose up -d --build       (see docker-compose.yml and docs/studio.md)
FROM python:3.12-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# FFmpeg 7 (trixie) for -/filter_complex, vidstab and zscale; cairo for the graphics; tini as PID 1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libcairo2 libsndfile1 fonts-dejavu-core tini \
 && rm -rf /var/lib/apt/lists/*

FROM base AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config libcairo2-dev \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# dependencies first (a stub package), so a code change doesn't reinstall them; the editable install then
# points at /app/src, where the real code lands
COPY pyproject.toml README.md PLAN.md ./
RUN mkdir -p src/vidosuper && echo '__version__ = "0.1.0"' > src/vidosuper/__init__.py \
 && python -m venv /venv \
 && /venv/bin/pip install --upgrade pip \
 && /venv/bin/pip install -e ".[ai,studio]"
COPY src ./src
COPY config ./config
COPY assets ./assets

FROM base
RUN useradd --system --uid 10001 --create-home --home-dir /home/vido vido \
 && mkdir -p /data && chown vido:vido /data
COPY --from=build /venv /venv
COPY --from=build --chown=root:root /app /app
WORKDIR /app
ENV PATH=/venv/bin:$PATH \
    VIDO_STUDIO_HOME=/data \
    VIDO_MODELS_DIR=/data/models \
    HF_HOME=/data/models/hf \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLCONFIGDIR=/tmp/mpl \
    HOME=/tmp
USER vido
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["vido", "studio", "serve", "--host", "0.0.0.0", "--port", "8765"]
