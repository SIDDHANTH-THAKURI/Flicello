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
- `vido studio run ... --pace every` (and no `--song`) for a run from the command line.
