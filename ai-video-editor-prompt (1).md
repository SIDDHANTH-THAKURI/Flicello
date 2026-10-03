# Claude Code Project Prompt: AI Video Editor for Trip Footage

Copy everything below the line into Claude Code to kick off the project.

---

## Project Goal

Build a tool that takes a folder of raw video clips (e.g. footage from a trip) and produces a single polished, shareable output video for YouTube/Instagram — not a mechanically stitched-together reel, but something edited with real creative judgment: good clip selection, good cut timing, good pacing, optionally music/effects.

I have no traditional video editing skill. I just want to supply raw footage and intent, and have the tool handle all editing judgment and execution.

## Why this is hard (and why it matters)

A naive tool would just concatenate clips in file order. That's not the goal. The goal is output that could genuinely do well if posted — meaning the tool needs to reason about *which* moments are worth keeping, *when* to cut, how to build a hook/pacing arc, and whether the result "feels" edited rather than assembled. Optimize for that bar throughout, not just for "does it run."

## Core Architecture

### 1. Analysis stage
Analyze every clip in the input folder and extract, at minimum:
- Technical quality: stability/shake, lighting, focus, exposure
- Content: what's actually happening in the clip (scene type, subject, action)
- Notable moments: timestamps within a clip worth highlighting (action peaks, good expressions, scenic beats, etc.)

### 2. Agentic, iterative generation loop — do NOT one-shot this
Build a loop, not a single pass:
1. Generate a draft edit (clip selection + ordering + cut points + any effects/music sync)
2. Evaluate the draft against quality criteria
3. Identify specific weaknesses
4. Refine and regenerate
5. Repeat until the draft clears a genuinely high bar, or a max-iteration/time/cost budget is hit (define a sane default and make it configurable)

Each iteration should produce an actual diff/explanation of what changed and why, not just a new render from scratch with no reasoning trail — so I can see the tool's "thinking" if I want to.

### 3. Specialized sub-agents
Use separate sub-agents for distinct evaluation dimensions, each scoring independently, for example:
- **Technical quality agent** — stability, lighting, focus
- **Content/action agent** — how interesting/engaging the content itself is
- **Pacing/rhythm agent** — cut timing, flow, whether the edit drags or rushes
- **Viral/trend-fit agent** — hook strength in the first few seconds, overall shareability patterns

A main orchestrating process aggregates these verdicts, decides what to change, and drives the next refinement iteration.

### 4. Beyond basic sub-agents — include these if feasible
- **Generator/critic pair**: one agent proposes an edit, a separate adversarial critic agent actively hunts for flaws in it (rather than agents purely scoring in isolation) — this tends to produce sharper results than independent scoring alone.
- **Reference-video grounding**: let me optionally supply 1-3 example videos I think look good, and use them as a concrete style/pacing target rather than abstract criteria.
- **Human-in-the-loop checkpoint**: show me a rough cut partway through (e.g. after the first full iteration) for a thumbs up/down or quick note before continuing to refine, so it's not fully blind to my taste.
- **Persistent taste memory across projects/runs**: track what I keep vs. discard over time (which clips, which pacing choices, which styles) and let that inform future runs, so the tool improves at matching my taste the more I use it. Store this locally in a simple structured format (not cloud-dependent) unless I say otherwise.

## Technical Constraints

- Use free/local tools for mechanical video processing wherever possible (e.g. FFmpeg) rather than defaulting to paid cloud video-processing services.
- Use actual reasoning (via Claude) for the creative decisions — clip selection, cut points, pacing judgments — not purely rule-based heuristics bolted on top of FFmpeg.
- I'm fine with API/token costs beyond my existing Claude Code subscription for the reasoning/analysis parts. I'm also open to paid services if something is genuinely worth it for quality — but don't default to paid tools without flagging the tradeoff first.
- Input: a folder path containing raw clips (assume common formats like .mp4/.mov). Output: a single rendered video file.

## What I want from you (Claude Code)

- You have creative latitude on architecture, specific libraries, file structure, and exact workflow — don't ask me to pre-specify implementation details I'm not qualified to specify.
- Propose a concrete plan and project structure before writing a large amount of code, so I can sanity check direction early.
- Flag any point where a paid API/service would meaningfully improve quality, with a rough sense of cost, so I can decide.
- Make the number of refinement iterations, and any cost/time budget, configurable rather than hardcoded.
