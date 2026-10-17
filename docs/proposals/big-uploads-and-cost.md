# Big uploads and what they cost: options (item 5 of the 18 Oct list)

The question: "what if someone uploads 20 ten-hour videos? Charge by compute, or render on their own PC for free and
in the cloud for money?" Measured on the live site and the owner's laptop, 18 Oct 2026.

## What a film costs us today

| Part | Measured | Per film |
|---|---|---|
| AI: logging each clip (Sonnet) | $0.027 a clip | $0.10-0.60 (4-20 clips) |
| AI: story ideas (Opus) | $0.146 a round | $0.15-0.45 |
| AI: the cut's plan (Opus) | $0.132 a plan | $0.26-0.53 (sample, film, a revision) |
| AI: talking/voice script + looks | $0.09 + $0.05 | $0.14 |
| AI: painted looks, backdrops, props (Gemini) | $0.034-0.05 an image | $0.30-0.80 |
| Rendering on Modal (estimate) | a 2-min designed talk: ~1,450 CPU-s + ~5 GPU-min | **$0.08-0.25** |
| Storage | 14-day retention, 2 GB a video cap | cents |

**AI is 75-90% of the cost; rendering is the small part.** Rendering on the person's own PC would save only that small
part, while the AI cost stays the same wherever the frames are drawn.

## What already stops "20 ten-hour videos"

These limits are live (settings.Limits; per access level in settings.tiers):

- a clip is at most 10 minutes; a video uses at most 20 clips and 20 minutes of footage in total, at most 2 GB;
- 6 GB per person; 60 GB for the whole site; uploads stop when the disk is low;
- beta access: 3 videos, films up to 60 s, recordings up to 3 min; special: 10 videos, up to 10 min;
- a monthly AI budget cap ($20 now) that pauses jobs and tells the owner when reached;
- projects are deleted after 14 days.

A ten-hour file is refused at upload. The worst case today is about 20 minutes of footage per video, three videos a
person, which costs roughly $1-3 of AI each.

## Options for when the beta opens up

1. **Credits per film (recommended).** A film costs credits by its length and kind (e.g. 1 credit up to 60 s, +1 per
   started minute after that; painted looks +1). Plans give monthly credits; top-ups for more. People understand it,
   it tracks what really costs money (AI + painting), and long films pay their own way.
2. **Minutes in and out.** Charge per minute of footage analysed plus per minute of film made. Fairer for
   very long footage, harder to explain.
3. **Bring your own AI key** (power users). They paste their own Anthropic/Gemini keys; their films use their keys and
   cost us only rendering. Good for heavy creators; needs careful key handling (stored encrypted, never shown).
4. **Render on your own PC.** A small app like our renderer that people run at home. It saves only the rendering slice
   (~$0.10 a film), adds an install, support and security work, and is worse on phones. Worth it later as a "keep my
   footage at home" privacy feature, not to save money.

## Recommendation

- Keep the hard caps (they already make the abuse case impossible) and the monthly AI budget alarm.
- Price with **credits per film** (option 1), sized so a plan's price covers its AI at the measured rates with margin;
  special access = more credits and longer films.
- Move rendering to **Modal** (GPU for the cut-out, 2 drawing processes, per-second billing) when the PC's queue waits
  regularly pass ~10 minutes; at ~$0.1-0.25 a film it doesn't change pricing much.
- Revisit option 3 for heavy creators once paid plans exist.

Decisions needed from the owner: the credit sizes and plan prices, and when to open sign-ups beyond approvals.
