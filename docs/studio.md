# Flicello: running the friends trial

The studio is a website on your own PC. Invited friends upload clips and a song, pick one of three story
ideas, check an 8-second sample, and get the finished video in 9:16 and/or 16:9. They rate it, and the
studio learns each person's style. You see everything on the admin page: who did what, every job, every
failure, and what the AI cost.

## How it fits together

```
friend's phone ──Tailscale (private, encrypted)──> your PC: tailscale serve (HTTPS)
                                                        └─> 127.0.0.1:8765  studio (web app + worker)
                                                                ├─ the pipeline: ingest, measure, render, checks (this PC's CPU)
                                                                └─ the brain: Claude API (pay per use, capped)
```

- **Web app + worker** run in one process (`vido studio serve`), or in one Docker container.
- **Data** lives in one folder: `%LOCALAPPDATA%\VidoSuper\studio` when run natively, the `studio-data` volume in
  Docker. It holds `studio.db` (SQLite), `users/<id>/projects/<id>/` (uploads, results), `workspace/` (proxies and
  measurements), `logs/`.
- **Brain**: friends' videos always use the Claude API with your key (`ANTHROPIC_API_KEY`). Your own videos can
  use headless Claude Code on your plan (`owner_brain: claude_code`, native run only), the API, or `local` (plain
  rules, no AI, for testing).

## First start (native, on this PC)

```powershell
# 1. the extra packages (once)
& "$env:LOCALAPPDATA\VidoSuper\venv\Scripts\python.exe" -m pip install -e ".[ai,studio]"
# 2. your own account: prints a one-time link; open it and pick a name and a password
.\vido.cmd studio invite --owner
# 3. run it (web app + worker); keep this window open during the trial
.\vido.cmd studio serve
```

Then open http://127.0.0.1:8765, sign in, and make one video yourself before inviting anyone.

### The API key and the spend cap

1. In the Claude Console, create a key for the studio and set a monthly spend limit there (for example $20).
2. Put it in the `.env` file next to this README as `ANTHROPIC_API_KEY=...`. It's git-ignored. Never paste it in
   chat.
3. The studio has its own caps as well: `$20` a month (`VIDO_STUDIO_MONTHLY_USD`) and `$4` per video. When a cap is
   reached, jobs pause with a message instead of spending more, and you get an alert. Raise the cap and press
   Retry on the admin page to resume.

Measured on 2026-10-12 with your own clips (6 4K clips, 71 s of footage, a 30 s 9:16 video), in API-price terms:
logging a clip costs about $0.03-0.04 (Sonnet, ~12 s each, several at once), and pitching three ideas plus designing
the cut about $0.39 (Opus, ~45 s each). That's **about $0.60 for a 6-clip video and about $1 for 15 clips**, plus
~$0.15-0.25 per revision. The admin page shows the real numbers per step.

## The public link (friends need nothing installed)

Since 2026-10-12 the studio is also published with Tailscale Funnel, so a friend only needs a link and an invite:

```powershell
tailscale funnel --bg --https=10000 http://127.0.0.1:8765     # on:  https://<this-pc>.<tailnet>.ts.net:10000
tailscale funnel --https=10000 off                             # off (the owner's other funnel on 443 is separate)
```

- Anyone can *open* the address, but there is nothing to see without an invite: every page except sign-in and join
  needs a session, uploads and videos are served only to their owner, and an invite works once.
- It's HTTPS (Tailscale's certificate), cookies are Secure/HttpOnly/SameSite=Strict, the site tells search engines
  to stay away (header, meta tag, robots.txt), and failed sign-ins are slowed per account, per address and
  overall (a person's own browser, once signed in, is exempt).
- The computer must be on and awake, with Tailscale running and `studio.cmd` open, or the link says unreachable.
- To go back to private-only, turn the funnel off; the tailnet-only address (`:8765`) keeps working for your devices.

## Letting friends in: Tailscale

Tailscale makes a private, encrypted network between your devices and the people you share with. Nothing is
opened to the internet.

1. Install Tailscale on this PC and sign in.
2. Serve the studio over HTTPS on your tailnet only:
   ```powershell
   tailscale serve --bg 8765
   ```
   It prints an address like `https://your-pc.tailnet-name.ts.net`. Run the studio with `VIDO_STUDIO_HTTPS=1` so
   its cookies are marked Secure.
3. Share **only this machine** with each friend (admin console → Machines → this PC → Share). They install
   Tailscale, accept, and open the https address.
4. Restrict what shared users can reach: in the access-control policy, allow them **only port 443 of this PC**
   (what `tailscale serve` answers on), nothing else. Check the current Tailscale docs for the exact syntax of
   rules for shared-in users before you rely on it, then test it from a friend's device: only the studio may
   answer.
5. Keep Windows Firewall on. Don't leave Remote Desktop or file sharing open to the Tailscale network.
6. Make each friend an invite link on the admin page (it works once, for 7 days) and send it to them.

## Running it in Docker (more isolation)

The container only sees its own data volume, runs as a non-root user with a read-only filesystem and no extra
privileges, and its port is bound to 127.0.0.1.

**Native or Docker?** Measured on 2026-10-12 with the same 30 s 9:16 video from six 4K clips:

| | analyse | sample | final render | whole video |
|---|---|---|---|---|
| Native (Intel QuickSync decode) | 42 s | 10 s | 3.4 min | ~4.7 min |
| Docker (software decode, no GPU) | 2.9 min | 65 s | 10 min | ~14 min |

Native is ~3x faster with 4K phone footage. Docker keeps uploaded files and FFmpeg inside a sandbox. For the first
friends you trust, native is fine; switch to Docker (or a cloud server) when people you don't know join.

```powershell
# put the key in .env.studio (git-ignored):  ANTHROPIC_API_KEY=...
docker compose up -d --build
docker compose exec studio vido studio invite --owner
```

Inside Docker the owner's brain is the API too (there's no Claude Code in the container).

## Keeping an eye on it

- **Admin page** (`/admin`, owner only): the worker heartbeat, the queue, disk space, the month's spend, alerts,
  every job (retry or cancel), each person's spend, and per-video event logs with error details.
- **Phone alerts (optional)**: set `VIDO_STUDIO_ALERT_URL` to an ntfy.sh topic with a long random name, and
  subscribe to it in the ntfy app. The studio sends one line for a failed job, a stopped worker, low disk or
  a spend warning. It never sends footage, file names or what a friend wrote. Alerts of the same kind are
  sent at most once every 30 minutes.
- **Command line**: `vido studio status` prints the same health line and unread alerts.
- **Logs**: `logs\studio.log` in the data folder (rotated).
- `/healthz` answers 200 while the worker beats (for an uptime checker).

## Limits for the trial (change them in `<data>\studio.yaml`)

```yaml
limits:
  videos_per_user: 3
  revisions_per_video: 2
  clips_per_project: 20
  clip_bytes: 2147483648        # 2 GB per clip
  user_bytes: 6442450944        # 6 GB per friend
  retention_days: 14            # friends' projects untouched this long are deleted, footage and all
  monthly_usd: 20
  video_usd: 4
  min_free_gb: 15               # below this, uploads and jobs wait
```

## Privacy, in short

- Uploads are stored under server-chosen names, checked with ffprobe, and only ever served back to their owner.
- The brain gets contact-sheet frames, measurements and transcripts. It never gets the files or anyone's GPS
  position.
- Nothing learns from footage. A person's style profile keeps only their ratings and short rules, and they can
  read and delete it on "My style".
- "Delete" removes a video's files at once. "Delete everything" removes all of a person's videos and their
  style profile.

## Measuring a video without the web app

```powershell
.\vido.cmd studio run "C:\path\to\clips" --song "C:\path\to\song.mp3" --shape 9:16 --length 30 --brain claude_code
```

It runs every step and prints the time and the AI cost of each (`--brain api` once the key is set, `local` for no
AI). The clips folder is only read.

## One shot per beat, music optional, time estimates (2026-10-12)

- **Cutting style "One shot per beat"** (`pace=every`, also chosen when the note says "cut on every beat" or "one shot per
  beat"): `brain/strikes.py` makes the plan without an AI call. The song's hits (`MusicAnalysis.strikes`: kick, snare and
  stabs on the sixteenth-note grid) plus the beats between them are the cuts. Real impacts in the footage (`find_impacts`)
  land on a beat with a wind-up and follow-through, and their own sound is mixed in, levelled by its peak. Every other
  shot is the most active stretch of the footage (motion signal) in step with how hard the music hits, never the same
  stretch twice until the footage runs out; a reuse gets a new framing (`reuse_framing`). Hits never show inside plain shots.
- **Music is optional.** With no song the person chooses (project page): a beat made for the video (default), or no music at all (`projects.music`: made | none; none leaves the EDL without a music track, the cut still follows the unheard grid). A beat is made (the pipeline makes one (`music/house.py`: synthesised, original, tempo from the
  pace, 120 BPM at most because the beat tracker halves faster tempos).
- **The note is read back** ("How I read your note" on the setup page): `read_note`, `pace_from_note`, `look_from_note`
  ("bright" gives the `bright` look).
- **Time estimates**: `Studio.eta` / `total_estimate` use the median of the last finished jobs of that kind (first guesses
  until there are some), corrected by the running job's progress; shown on the setup page and the progress card.
  Drawing a voice or talking video is expected to take its length times the drawing rate of that kind and style here
  (`Studio.draw_rate`: seconds of drawing per second of video, from `renders.render_seconds / seconds` with
  `renders.style`; first guesses 20 for designed, 3 for labels), since a 2-minute designed talk takes far longer than
  a 30-second montage. A parallel designed render reports the frames its parts have drawn (`<part>.drawn` files).
- `vido studio run ... --pace every` (and no `--song`) for a run from the command line.

## Voice-only and talking videos (2026-10-15)

`projects.kind`: `montage` (clips + music, the original), `voice` (a recording only, animation drawn to it) and `talk`
(the person's own video kept as is, motion graphics over it). Code in `src/vidosuper/explainer/`:

- `script.py`: `VoiceScript` (scenes with a layout, callouts), `fit_script` (repairs any script against the real audio so
  it always renders), `local_script` (no-AI fallback; keywords to emoji).
- `draw.py` / `compose.py`: frames drawn with Pillow (colour emoji font, Nunito), piped to FFmpeg. Voice loudness pulses
  the hero picture; captions are word-by-word from Whisper's word times. Talk mode copies untouched frames and the
  original audio stream (checked: correlation 0.9999 with the source).
- `make.py`: transcribes once (cached in `<project>/speech.json`), renders the 9 s sample and the final.
- `Brain.script_voice` + `brain/prompts/voice.md`: the AI reads the transcript with word times and returns the script
  (about $0.05). Revisions pass the previous script and the person's words.
- Stages are the same as for a montage (setup, preparing, review of a sample, rendering, done); the prepare job also makes
  the sample. Uploads: the recording goes in as upload kind `song` (audio), the talking video as one `clip`.

## Designed talking videos (2026-10-16)

A talking video (or a voice) can now have **designer animations**: `projects.style` is `designed` (default) or `labels`
(the clean labels from before); the form also lists AI-made scenes, explainer drawings and 3D as "coming soon".

- **The cut-out** (`explainer/cutout.py`): MODNet portrait matting (Apache-2.0, `%LOCALAPPDATA%/VidoSuper/models/
  modnet.onnx`, from huggingface.co/Xenova/modnet `onnx/model.onnx`, sha256 07c308cf…). A guided filter snaps the edge to
  the picture, pieces not attached to the person are dropped (ceilings, chairs), edge colours are cleaned of the wall
  behind, and the matte is smoothed over time. No fixed head area: the zones for graphics come from the person's outline.
  Without the model file the same graphics are drawn over the video on a dark band.
- **The animation library** (`explainer/lottie_lib.py`, `%LOCALAPPDATA%/VidoSuper/lottie`): free LottieFiles animations
  (Lottie Simple License: commercial use and changes allowed, no credit needed), pulled per topic on demand, each tested
  in a child process first (one real file crashes the native renderer), measured (size, length, own background, faint,
  empty lead-in). Picks are remembered per need; needs that nothing fitted are remembered too (no paying to ask again).
- **Picking** (`explainer/visuals.py`): the script names a `visual` and search `topics` for every moment; the brain sees
  numbered preview sheets and picks (`Brain.pick_visuals`, Sonnet, ~6 needs per request); needs nothing fitted get one
  more try with simpler ideas (`Brain.rethink_visuals`, Haiku). What still has nothing shows the words, animated.
- **The script** (`prompts/voice.md` + `DESIGNED_TEXT`): `scene` callouts every 3-6 s on the word, `title`, `stack`
  (10 audio clips fly out of the email), `stat`, an `ambient` loop behind the head, `punch` once. `check_voice_script`
  sends it back when a stretch over 10 s of speech has no graphics or a number that is said is not shown.
- **The renderer** (`explainer/designed.py`): stage (palette gradient, fog, particles), ambient loop, the main animation in
  the free zone above/beside the person (may sink behind the head), labels in front above the head, stacks, counting
  stats, kinetic titles, full-screen cutaways, captions (split numbers rejoined), a slow push-in and one punch. Their audio
  is copied untouched. Final videos are drawn by `render_processes` (3) processes in parallel and joined.
- Speed on this laptop: ~11 s of work per second of video with 3 processes (the 124 s Undertone talk: 23 minutes).
  Fewer processes when memory is short (`safe_workers`: ~1.3 GB per part at its peak, drawing + encoder + decoder,
  2.5 GB kept spare); the encoder and decoder threads are capped, since uncapped x264 took ~0.9 GB per part.
- The transcript for talk/voice keeps every sentence (Whisper's "unsure" flag dropped real lines, like the rating).

## The public site (2026-10-17)

Flicello now runs on its own server at **https://flicello.com**: a DigitalOcean droplet in Sydney (`ssh flicello`;
it moved from Bangalore on 2026-10-17), with Docker Compose (`deploy/docker-compose.server.yml`) running the
studio behind Caddy. Caddy handles HTTPS by itself. Secrets live in `/srv/flicello/.env.studio` on the server,
never in git and never in chat.

**Server notes.**
- **Setup:** user `deploy` with only the PC's key, root login off, `AllowUsers deploy`. ufw allows 22, 80 and
  443 (tcp and udp), with fail2ban, unattended upgrades and 4 GB of swap. Docker logs rotate (10 MB × 5).
- **Deploy:**
  - Ship `git ls-files -co --exclude-standard`, without `tests/` and `docs/`, as a tarball into `/srv/flicello`.
  - Then run `docker compose -f deploy/docker-compose.server.yml build && ... up -d`.
- **Caddyfile trap:** Caddy mounts the Caddyfile as a single file, which pins the file's inode. Replacing the
  file (rm and extract, tar, an editor that renames) leaves the running Caddy on the old copy. After changing it,
  run `docker compose ... up -d --no-deps --force-recreate caddy`.
- **Moving servers:**
  1. Copy the `caddy-data` volume, so the certificates come along and HTTPS works from the first second.
  2. Stop the old studio and copy `studio-data`. SQLite is consistent once the app is stopped.
  3. Point the old Caddy at the new server with `reverse_proxy https://<new ip>`, plus
     `header_up Host {host}` and `tls_server_name flicello.com`. Without the Host line, Caddy sends the IP and
     the new Caddy answers an empty 200.
  4. Change DNS, and delete the old droplet a day later.
- **Remote rendering** (`remote.py`, `web/workerapi.py`; `VIDO_STUDIO_JOBS=remote` on the server):
  - The server keeps the queue but runs no jobs. The 2 GB droplet can't render.
  - `renderer.cmd`, i.e. `vido studio remote-worker`, on the owner's PC claims jobs over HTTPS. It mirrors the
    project's files, runs `Studio.run_job` with every DB call going to `/api/worker/db`, and uploads what
    changed. The stage change lands only after the upload.
  - Both sides hold the same `VIDO_STUDIO_WORKER_TOKEN`. That token is as powerful as the database.
  - The PC keeps the pipeline's working files between a project's jobs, and deletes its copies of projects the
    site no longer has.
  - With no renderer checking in for 90 s, queued videos say so.
  - Modal later is just another remote worker on the same API.
- **Packages:** the image installs only `.[ai,studio]`. `tests/test_packaging.py` fails when the code imports a
  package that isn't declared there. httpx once arrived only through dev and anthropic, and the server
  crashed on sign-in emails.

**Front end.** `frontend/` is a Vite project built into `src/vidosuper/studio/web/static/dist`. Templates load
it with `{{ vite("app") }}`, `vite("landing")` or `vite("admin")`, which reads the manifest. The CSP stays
`'self'` only: no inline scripts or styles, and fonts are self-hosted.
- `npm run build` for one build, or `npm run dev` to rebuild on save.
- The Docker image builds the front end in its own stage.
- Check pages by looking, not by trusting the code:
  - `node frontend/tools/shoot.mjs <url> <dir>`: the landing page, with frame stats;
  - `pages.mjs`: walks through sign-up;
  - `studio_pages.mjs`: the app screens;
  - `admin_shot.mjs`: the dashboard;
  - `section_shot.mjs <url> <selector> <dir> 1440x900 390x844 ...`: one section at several sizes, flagging
    anything wider than its box and a page that scrolls sideways.

**Sign-in** (`auth.py`, `web/authweb.py`).
- **Methods:** Google (OpenID Connect with PKCE, plus state and nonce), a one-time email code (stored as an
  HMAC, 10 minutes, 5 tries, rate-limited), and passkeys (WebAuthn).
- **New accounts:** confirm 18+ and accept the Terms and the Privacy Policy. Every consent is stored with its
  version, the time and a keyed hash of the IP address.
- **Sessions:** renew whenever the person uses the site (60 days after last use, a year at most). Cookies are
  `SameSite=Lax`; anonymous forms must come from our own Origin.
- **Invites:** still work while public sign-up is off, and the name-and-password route remains as a fallback.
- **To switch features on:**
  - Put `RESEND_API_KEY` (email codes), and `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` (redirect URI
    `https://flicello.com/auth/google/callback`), in the server's `.env.studio`.
  - Then set `VIDO_STUDIO_PUBLIC_SIGNUP=1` to open sign-up to everyone.
- **Search and Google's brand check:** the public face (`/`, `/tour`, `/signin`, `/legal/*`) is always
  indexable, even while sign-up is closed. Google's OAuth brand verification reads the home page, and it must
  say what Flicello is, what Google sign-in is for, and that intimate fakes are banned (tested). Signed-in
  pages, `/signup` while closed, and the private paths in `robots.txt` never are.

**Legal** (`legal/*.md`, served at `/legal/...`). The documents are rendered with the facts in
`web/legalweb.py` (`FACTS`: operator, state, hosting location, contact emails); change a fact there and every
document follows. Keep `auth.LEGAL_VERSION` in step with material changes.

**People's rights.**
- `/account/export`: everything kept about the person, as JSON.
- `/account/delete`: erases every row and file for the person; spend rows stay for the books, anonymised.

**Analytics** (`analytics.py`) are cookieless. Each visitor is a keyed hash of (IP, browser) with a salt that
changes daily and is deleted the next day. No IP address is stored, bots are skipped, and project ids and
invite tokens never reach the log.

**The owner's dashboard** at `/admin` runs on `/admin/api/metrics` and `/admin/api/live`. It shows counts and
settings, never anyone's footage, titles, requests or written feedback.
- **Charts:** follow the dataviz method. The palette was validated against the panel surface `#101016`.
- **Live data:** refreshes every 5 seconds.

## The cloud renderer: Modal (2026-10-18)

flicello.com renders on the owner's PC (renderer.cmd) and, when the PC can't, on Modal: containers running the same
remote worker (`src/vidosuper/studio/remote.py`, `cloud=True`), started by the site and stopped when the queue is empty.

- **When the cloud works** (`VIDO_STUDIO_MODAL` on the server): `auto` (default) - the PC goes first; the cloud takes
  everything while the PC is offline, and a step that has waited 45 s while the PC is busy. `always` - every step the
  cloud can run goes there. `off`. The owner's own videos always stay on the PC (they use the owner's Claude plan).
- **Size and cost:** one renderer = 8 cores (16 vCPUs) and 16 GiB, about $0.50 an hour while it works, up to 3 at
  once; each stops after 90 s with nothing to do. Its time goes on the books (spend, backend `modal`) and counts
  towards the monthly cap. Set a spending limit in the Modal dashboard too.
- **Set up** (from the repo, with the venv's `modal`, logged in to the owner's workspace):
  1. the secret `flicello-worker` with VIDO_STUDIO_WORKER_TOKEN, ANTHROPIC_API_KEY, GEMINI_API_KEY and
     ELEVENLABS_API_KEY (`modal secret create flicello-worker --from-json <file> --force`; never paste keys in chat);
  2. the cut-out model into the volume: `modal volume put flicello-models <modnet.onnx> /modnet.onnx`
     (in Git Bash set `MSYS_NO_PATHCONV=1`, or the remote path turns into a Windows one);
  3. `modal deploy deploy/modal_app.py` (prints the `wake` address), then `modal run deploy/modal_app.py::prepare`
     (fetches the speech models into the volume) and `modal run deploy/modal_app.py::check` (a self-test);
  4. on the server, `VIDO_STUDIO_MODAL_URL=<the wake address>` in /srv/flicello/.env.studio, then restart the
     studio container.
- **Check it:** admin page, "Cloud renderer" -> "Test the cloud renderer": a container draws a short designed talk
  with no one's footage and reports its speed in Alerts.
- **After a code change:** `modal deploy deploy/modal_app.py` again (the code is mounted, so it's quick), as well
  as the server deploy.
