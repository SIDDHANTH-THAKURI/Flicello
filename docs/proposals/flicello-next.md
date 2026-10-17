# Flicello: what's next (running notes from talks with the owner)

Started 2026-10-17. Each item: what was decided, then what's open.

## 1. One Flicello, in the cloud

- flicello.com (Sydney) is the only Flicello from now on.
- The old studio on the PC (friends' trial over Tailscale) is to be shut down.
- Friends' and the owner's old projects are not migrated. The owner sends fresh invites to friends' real emails.

## 2. Render offload to Modal

- **Why:** the Sydney server (2 GB) can't render. Without this, nobody can get a video on flicello.com.
- **What:**
  - The server plans the edit, Modal machines render it and shut down, and the finished file comes back.
  - Caps per person and per month.
- **Cost (Modal list prices as remembered; check modal.com/pricing before building):**
  - CPU about $0.05 per core-hour, memory about $0.01 per GB-hour, a T4 GPU about $0.60 an hour, billed per
    second. The free plan includes about $30 of usage a month.
  - A typical 30-60 s video is roughly 2-10 cents of render. The AI planning (Anthropic) is separate, and usually
    costs more per video than the render.
- **Status 2026-10-17:**
  - The Modal account exists and is logged in on the PC; no Modal code yet.
  - The owner put it on the list for later: "we can build it later".
  - Until then flicello.com can't reliably make videos.

## 4. Plans and payments (asked 2026-10-17, recommendation given, not decided)

- **Model:** credits.
  - A free trial (3 films) and monthly plans that grant credits each month, plus top-up packs.
  - 1 credit = 1 finished film up to 60 s, with 2 revisions included.
  - Longer films, or talking videos with cut-outs, cost more credits.
- **Unit cost to cover:**
  - AI planning about $0.10-0.50 and render about $0.02-0.10, so about $0.30-0.60 per film with revisions.
  - Price at 3x cost or more.
- **Draft prices (USD, tax included):**
  - Free: 3 films once.
  - Creator $12/mo: 20 films.
  - Pro $29/mo: 60 films and priority.
  - Top-up: 10 films for $8.
- **Payment provider:** a Merchant of Record (Paddle, or Lemon Squeezy), because users are worldwide.
  - It is the legal seller, so it collects and pays GST/VAT/US sales tax everywhere, and handles invoices,
    refunds and chargebacks.
  - Fee about 5% + 50c. Stripe is cheaper (about 2-3%) but leaves the owner with global tax.
- **Build:**
  - Credits ledger, plan limits, checkout, webhooks (verified signatures), a billing page and a customer portal.
  - Admin revenue charts.
  - Terms: billing, renewals and a refund policy that respects the Australian Consumer Law guarantees.
- **The owner must:** open the payment account (identity, bank, ABN); I can't. I integrate with its keys.

**Decided 2026-10-17:** the owner goes with the recommendation as written (credits + plans, the draft prices,
Paddle).

## 5. Test bench: is the output good, and does it improve?

- A fixed set of 10-15 real test cases: footage + song + request, covering travel, talking, sports, music video
  and a vague request.
- Every engine change re-runs the bench. The owner rates out of 20, and automatic checks (sync, loudness,
  glue, text) score too.
- A chart shows whether the scores rise, so a change that makes things worse is caught before users see it.

## 6. Public films, likes and learning (later, after paying users)

- Opt-in "make public" with a gallery and likes. Every person in the film must have agreed: a checkbox and a
  report button.
- Moderation: auto-check plus reports, because public = NCII/copyright risk. Licensed or own music only on
  public films.
- **Learn from signals, not footage.** Use likes, watch-through, ratings, chosen pitches and revision requests to
  tune the planning rules and per-person taste.
- Never train models on users' footage: the privacy policy promises this. Any change needs fresh, explicit
  opt-in.

## 7. "Do we understand each other" step before rendering

- After upload, the AI writes an **understanding card** in plain words:
  - the song: mood, tempo, where the drop is, sung words;
  - the footage: best moments, people, problems;
  - what it thinks the person wants.
- The person corrects it in plain words; at most 2 rounds, then go.
- Then a **cheap preview** before the full render: a low-res animatic (thumbnails cut on the real beats with the
  real song).
- Approve, then the full render, which spends the credit. This saves money (no wasted full renders) and builds
  trust.
- Builds on the existing pitch + "three questions" steps.

## 8. Abuse and cost protection

- **Uploads:**
  - caps per project (clips, total minutes, GB) and per day per account;
  - one song per film;
  - file-type sniffing (not the extension);
  - a duplicate hash means no re-analysis;
  - the 14-day auto delete already exists.
- **Money:**
  - credits gate every expensive step;
  - AI spend caps per person per day and per month, plus a global daily cap (the existing caps stay);
  - revision limits per film;
  - a kill switch in admin;
  - alerts when spend jumps.
- **Accounts:**
  - one free trial per verified identity (Google sub / verified email, plus device and IP signals);
  - disposable emails blocked (done);
  - a sign-up rate limit (done);
  - a bot check on sign-up (Cloudflare Turnstile) if bots appear.
- **Prompt injection:**
  - user text, transcripts and song lyrics are data, never instructions;
  - the AI only returns structured plans that code validates;
  - it has no tools that touch accounts, money or other people's data;
  - output token caps.
- **Content:** NCII/CSAM rules (done in the policy). Later: automated checks on public films, plus the
  abuse@ inbox.
- **Admin:** a flag for unusual accounts (volume, repeated failures, spend), with one-click suspend.

## 3. The PC renders for flicello.com (owner's idea, 2026-10-17)

- **Design: the PC pulls.**
  - A worker on the PC asks flicello.com over HTTPS for work, downloads the job's clips, renders and uploads the
    video.
  - The server never connects to the PC: no open ports, no Tailscale needed on the server.
  - A per-worker secret token, and the server keeps the queue.
- **The same job protocol later serves Modal:** a Modal worker is just another puller. This is a first half of
  item 2, not throwaway work.
- **Costs and limits:**
  - Renders happen only while the PC is on and online; the site shows "waiting for a renderer" otherwise.
  - Clips go over the home upload link.
- **Privacy:** the policy says rendering runs "on our own servers". It must also name the operator's own
  computer in Australia while this is in use (Australian Consumer Law: claims must be true).
- **Built 2026-10-17:**
  - `remote.py`, `web/workerapi.py` and `renderer.cmd`, with tests (`tests/test_remote.py`).
  - Verified end to end on the PC with the real pipeline.
  - The privacy and AI pages name the NSW computer (`FACTS.render_location`).

## 9. Storyboard (owner's idea, 2026-10-17)

- Before the full render, show the plan as a storyboard: one frame per shot (the real still from the chosen
  moment), its timing on the song's beats, the text and effect on it, and why it's there.
- The person can swap a shot, move it, drop it, or say what's wrong with it in words. Only the changed shots are
  re-planned.
- Joins item 7: understanding card, then storyboard, then the cheap animatic preview, then the full render.
  Each step costs little and catches a wrong idea before the expensive render.

## 10. Growing new abilities when a request needs one (owner's idea, 2026-10-17)

- **Detect:** every request is mapped to the abilities it needs (say, "lyrics on screen", "split screen",
  "map route animation"). One we don't have is logged as a gap, with the request and how often it is asked
  for.
- **Build:** for a gap asked often enough (or flagged by the owner), the improvement agent (`improve.py`
  routine) writes the new tool or skill in a branch. It adds tests and a sample video made with it, and runs
  the whole suite and the test bench (item 5).
- **Ship, not blindly:**
  - the owner gets the sample and a one-line summary and approves;
  - then it deploys (the guarded auto-deploy already exists for safe changes);
  - no AI-written code goes live without tests passing and, for new abilities, the owner's OK.
- **Meanwhile, for the person:**
  - say honestly what isn't possible yet;
  - offer the closest thing that is;
  - optionally "notify me when it's ready".
- **Why not fully automatic:** code written and shipped by an agent with no review can break the site, cost
  money or open security holes. A human approval step is cheap and keeps quality up. The improvement
  routine's guards stay.

Agreed order (2026-10-17): 1 PC renderer (done), 2 abuse limits + payments, 3 understanding card + preview,
4 test bench alongside, 5 Modal, 6 public gallery.

## Done 2026-10-17 (not on the list, asked for in between)

- The 16 Oct voice video fixed: no empty scenes, animations at their drawing's size, names respelled, film rules
  kept out of explainers.
- Looks: three painted looks per voice/talking video, refined live ("Repaint it", your own idea), learned per
  person (profile taste); painted backdrops with camera moves; painted props in the look; a frosted text backing.
  Needs GEMINI_API_KEY on the renderer (Tier 1, prepaid).
- An allow-list for sign-in (three emails); the owner account linked to Google.
