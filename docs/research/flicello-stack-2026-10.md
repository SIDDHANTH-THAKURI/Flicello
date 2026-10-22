# What the best are building, and what Flicello should use (research, 22 Oct 2026)

The owner's goal: a "crazy" editing system for creators, ad agencies and editors who pay to get it done and want
reach. It has to:
- understand everything (clips frame by frame, the speech in them, the music);
- make and edit music;
- offer pro motion graphics, animation, VFX and SFX;
- generate AI video (up to about 2 min) and edit films up to 20 min;
- work on phone and laptop;
- have an AI on screen ("Jarvis") that does what you say instantly, with sub-agents behind it;
- show fast previews first and the perfect render after;
- take settings by voice and remember them;
- offer manual tools too;
- export every way, with a virality score from a trained model;
- above all, be accurate.

Money is no object at first; costs come down later.

Each section below gives what exists, the pick, its licence and cost, and how Flicello uses it. Links are at the end of
each section.

## 1. Motion graphics: the biggest gap today

Today our graphics are drawn by our own Python code (Pillow, cairo). That is the ceiling the owner sees ("pretty bad").
The field has moved to **graphics written as web pages by agents and rendered frame-exact in a headless browser**.

| Option | What it is | Licence / cost | Verdict |
|---|---|---|---|
| **HyperFrames** (HeyGen, ~69k stars) | HTML + CSS + JS to MP4; adapters for GSAP, Anime.js, Motion One, Lottie, three.js, CSS, WAAPI; WebGL shader transitions; a ~450-block catalog; deterministic frame capture (Chrome beginFrame) + FFmpeg; agent skills; browser preview with live reload; Lambda rendering | **Apache 2.0, no per-render fee** | **Pick.** The composition is plain HTML, so the browser can play it as a preview and the server renders the same file. Agents write HTML best. |
| Remotion | React to video; Player for in-browser preview; Lambda renders (a 2 h video in ~12 min, ~$0.01/min); official Claude Code agent skills since 20 Jan 2026 | Free for teams of up to 3; automation $0.01/render (minimum $100/month); enterprise from $500/month | Strong second. Its Player is the most mature preview component. |
| Motion Canvas / Revideo | Canvas-based, imperative | MIT | For explainers. Revideo's roadmap now follows a commercial product. |
| After Effects (nexrender, Plainly, Templater) | Real AE templates rendered by automation | nexrender MIT; needs AE on Windows/macOS render machines; marketplace templates' in-app terms are unclear | Later, for agency-grade templates, if licences allow. Lottie brings AE animations in anyway. |

**Animation libraries:**
- **Licence risk:** GSAP is now 100% free including SplitText and MorphSVG, *but* its licence forbids "tools that let users
  build visual animations without code" that compete with Webflow. Webflow's FAQ says AI-generated GSAP code is fine, but
  it's a grey zone for us.
- **Safe defaults:** **Anime.js (MIT), CSS / WAAPI, Lottie, three.js**. GSAP only after written OK from Webflow.

**What makes it look pro (measured, not guessed).** `motionmaxxing` (Apache 2.0) measured 53 professional motion moments:
- **Timing:** 12 named eases; a move takes a median of ~10 frames (0.33 s); entries 11-12 frames, opacity 6, blur 7-8.
- **Direction:** landing decelerates and leaving accelerates. Overshoot is rare (under 9%) and late. Things arrive from
  oversize (logos from ~3.5×); nothing grows from zero.
- **Cuts:** cut on motion, never on rest. Fades are rare: 3,392 transform entries against 584 opacity fades.
- **"Slop" signals to ban:** page chrome (corner labels, counters, timecodes, progress bars), letter-typed headlines,
  flat swatch backgrounds, more than one hero per frame.
- **Quality gates:**
  - no run of more than 3 flat frames;
  - an end hold of 1.4 s at most;
  - the final shot is at most 25% of the film;
  - UI text at least 4% of the frame height.

**Our edge over everyone using these tools:** "Remotion has no idea where your face is." Theirs are blind; ours aren't. Our
analysis knows:
- where the face and subject are (SAM 3 masks);
- the word timings and the beats and drops;
- the platform's safe zones.

So graphics are **placed and timed automatically around the footage** and checked from rendered frames (the agent
looks at its own output). HeyGen (Motion Designer) and Higgsfield (Vibe Motion, built with Anthropic on Remotion) do
prompt-to-graphics; none of them composes them onto *your* footage with that knowledge.

Sources:
- [HyperFrames](https://github.com/heygen-com/hyperframes)
- [awesome-claude-video-skills](https://github.com/zhuyansen/awesome-claude-video-skills)
- [Remotion pricing](https://www.remotion.dev/docs/pricing), [Remotion Lambda](https://remotion.dev/lambda)
- [Remotion vs Motion Canvas](https://www.remotion.dev/docs/compare/motion-canvas)
- [motionmaxxing](https://github.com/Tejashmakwana/motionmaxxing)
- [Claude Code + Remotion lessons](https://www.rescroll.ai/blog/claude-code-remotion-motion-graphics-2026)
- [GSAP free](https://webflow.com/blog/gsap-becomes-free), [GSAP licence text](https://scancode-licensedb.aboutcode.org/gsap-standard-no-charge-2025.html)
- [nexrender](https://github.com/mkariti/nexrender)
- [HeyGen Motion Designer](https://www.heygen.com/en-in/tool/ai-motion-graphics)

## 2. Instant edits: "cut where my face shows" → done in a second

**How the fast ones do it:** the heavy understanding happens **once, when clips land**; later asks are **lookups on an
index**. The edit is **data, not a video**: a list of cuts that a browser player plays from small proxies at once. The
full-quality render runs later in the background.

| Layer | Pick | Licence / cost | Why |
|---|---|---|---|
| Watching a clip (what, who, said, moments) | **Gemini 3.x Flash video understanding** (native video + audio; the agentic mode from 1 Sep 2026 picks frames, audio or transcript itself; Google reports up to 88% fewer tokens) | ~$0.01-0.03 per minute of video; timestamps at about 1 s | Hears and sees together, so it covers "understand the audio inside the video". Claude reads stills only. Claude stays the editor. |
| Find any thing by words ("my face", "the red car", "that guy") and track it | **SAM 3** (Meta, Nov 2025): text prompt, then detect, segment and track every instance in video | SAM License: commercial use allowed (bans military uses etc.) | Masks for cuts, blurs, cut-outs, reframing, rotoscoping and graphics placement |
| Who is who (faces) | **OpenCV YuNet + SFace** (commercial-friendly) | Apache / MIT | InsightFace's best models (buffalo_l) are **non-commercial**: avoid |
| Search by meaning ("the sunset shot") | **SigLIP 2** frame embeddings + a vector index; or **Twelve Labs Marengo** managed | SigLIP 2 Apache 2.0, self-hosted on Modal; Twelve Labs ~$0.033-0.042/min to index, $4 per 1k searches | Start managed for speed of building; self-host later to cut cost |
| Speech, word by word, with who speaks | faster-whisper (have) + WhisperX alignment + pyannote diarization | MIT / BSD | Edit by deleting words, and captions |
| Shots and moments | PySceneDetect / TransNetV2 + our measured signals (have) | BSD / MIT | |

**The preview player:** the edit list plays in the browser on proxies (we already make 640-px proxies), so a change
shows in under a second. Options:
- the **Remotion Player**;
- a **HyperFrames composition** in an iframe (it plays as-is);
- a WebCodecs/WebGPU engine.

**OpenReel** (MIT, React + Zustand + Mediabunny + WebCodecs + WebGPU + three.js) is a full browser editor: multitrack,
keyframes, colour wheels and curves, LUTs, masks, chroma key, ducking, an agent package. It's the base to study, or
fork, for the manual editing screen. **OpenChatCut** (AGPL, ideas only) shows the right agent pattern:
- immutable timeline state, changed only through editor commands (traceable, undoable);
- AI edits as **draft proposals** applied in one undo step.

**Phones:** Safari 26 turned WebGPU on by default on iOS and iPadOS, so GPU preview and compositing in mobile browsers
is viable. WebCodecs video *encoding* on iOS is not confirmed, so final renders stay in the cloud.

Sources:
- [Gemini agentic video understanding](https://www.fonearena.com/blog/491156/google-gemini-agentic-video-understanding.html/amp)
- [Gemini video costs](https://vorplabs.com/models/video-understanding-costs)
- [Gemini timestamp precision](https://discuss.ai.google.dev/t/improve-timestamp-accuracy-on-video-understanding/95356)
- [SAM 3](https://ai.meta.com/sam3/), [SAM licence](https://huggingface.co/facebook/sam3/blob/main/LICENSE)
- [Twelve Labs pricing](https://www.twelvelabs.io/pricing-2)
- [OpenReel](https://github.com/Augani/openreel-video), [OpenChatCut](https://github.com/0xsline/OpenChatCut)
- [OpenCut](https://themenonlab.blog/blog/opencut-open-source-capcut-alternative-video-editor)
- [Diffusion Studio core](https://docs.diffusion.studio)
- [WebGPU in iOS 26](https://appdevelopermagazine.com/webgpu-in-ios-26/)

## 3. Jarvis: one main agent, a team behind it, and speed

- **Pattern:** an orchestrator plus parallel specialists. Anthropic's own research system, an Opus lead with Sonnet
  workers, beat a single Opus agent by 90% and used about 15× the tokens.
- **The Claude Agent SDK** runs subagents concurrently, so they finish in the time of the slowest one. Limits are
  configurable: 20 running per session by default; dynamic workflows run 16 at a time, 1,000 per run.
- **Our split:**
  - **Jarvis's own turns:** the Messages API with our tools, for low latency. Small asks are routed by a fast model:
    **Haiku 5.5 at low effort** (~6 s at 10k tokens on low effort; effort dominates latency) or a code fast path.
  - **Creative subtasks** run as **Agent SDK workers in a sandbox**: write the HyperFrames graphic, lint, render stills,
    look, fix. A coding agent with the official skills (HyperFrames, Remotion, motion-design) and a closed visual loop.
- **Speed rules:**
  1. Index on upload.
  2. Code before AI: "remove shot 3", "brighter", "for Instagram" never wait for a model.
  3. A fast model for routing, the creative model only for creative work.
  4. Work ahead while they talk.
  5. Preview from the edit list, render later.
  6. Cache everything, and use prompt caching.
- **Voice:** streaming speech-to-text: ElevenLabs Scribe v2 Realtime (~150 ms, most accurate), or Deepgram Nova-3 / Flux
  (best turn detection, ~$0.0092/min). Text-to-speech for Jarvis's voice: ElevenLabs.
- **Patterns from OpenMontage** (AGPL, ideas only):
  - approval gates at creative decisions;
  - scored choice between providers, with the reasoning logged;
  - "slideshow risk" scoring;
  - post-render self-review (ffprobe, frames, audio);
  - budget estimate, reserve, reconcile;
  - analysing a reference video into a plan.

Sources:
- [Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Agent SDK subagents](https://platform.claude.com/docs/en/agent-sdk/subagents)
- [Dynamic workflows](https://platform.claude.com/cookbook/claude-agent-sdk-08-dynamic-workflows)
- [Haiku 5.5 latency](https://artificialanalysis.ai/zh/models/claude-haiku-5-5/providers)
- [Speech-to-text 2026](https://futureagi.com/blog/best-voice-ai-july-2026/)
- [OpenMontage](https://github.com/calesthio/OpenMontage), [VideoDB Director](https://github.com/video-db/Director)
- [EditDuet (Adobe)](https://research.adobe.com/publication/editduet-a-multi-agent-system-for-video-non-linear-editing)

## 4. Music, beats, stems, SFX

| Need | Pick | Licence / cost |
|---|---|---|
| Beats and downbeats | **Beat This!** (CPJKU, ISMIR 2024, SOTA without DBN; ONNX and Rust ports) alongside our 808-phase logic | MIT code and weights (some training audio is restricted: check before redistribution) |
| Song structure (intro, chorus, drop) | All-In-One (allin1) + our drop detection | MIT |
| Make music | **ElevenLabs Music v2**: section editing (inpainting), extend or trim, loops, change lyrics, stems, timestamps | Self-serve API: broad commercial use; inpainting may be Enterprise-only (to verify) |
| Stems from any song | **BS-RoFormer / Mel-RoFormer** (top of the MVSEP leaderboard), self-hosted | Open weights |
| Sound effects matched to the picture | **HunyuanVideo-Foley** (Aug 2025, 48 kHz, beats MMAudio and ThinkSound on its authors' tests) plus ElevenLabs SFX | Open weights (check the licence) / API |
| Known-sounding licensed music | Soundraw API ($30-$300/month), Loudly; Epidemic and Artlist consumer plans do **not** cover in-app use | Decision for the owner |

Sources:
- [Beat This!](https://arxiv.org/pdf/2407.21658)
- [Eleven Music v2](https://elevenlabs.io/blog/eleven-music-new-tools-for-exploring-editing-and-producing-music-with-ai)
- [Mel-RoFormer](https://arxiv.org/html/2310.01809v1)
- [HunyuanVideo-Foley](https://github.com/Tencent-Hunyuan/HunyuanVideo-Foley)
- [Music APIs](https://soundraw.io/blog/post/music-api-developers-guide)

## 5. Generated video (≤ 2 min) and VFX

- **Generation (pay per second, via Higgsfield's developer API from 17 Sep 2026, or fal / BytePlus):**
  - Kling 3.0 ~$0.042/s;
  - Wan 3.0 from $0.020/s;
  - Seedance 2.5 from $0.144/s;
  - LTX 2.5 Fast $0.09/s;
  - Veo 3.1 tiers $0.03-0.75/s.

  Sora 2's API shut down in Sep 2026.

  **Draft then final applies here too:** a cheap, fast model (Wan or LTX Fast) for the idea, then Kling, Seedance or Veo
  for the keeper. LTX-2.5 is open weights (free under $10M ARR) and renders a 10 s 720p clip in 6.8 s on data-centre GPUs.
- **VFX:**

  | Need | Pick | Licence / note |
  |---|---|---|
  | Remove an object | MiniMax-Remover / DiffuEraser, with masks from SAM 3 | Slow: ~7 min for a 2K clip, so cloud GPU |
  | Upscale (AI clips, low-light footage) | SeedVR2 | Apache 2.0, 14 GB VRAM |
  | 3D parallax and camera moves from footage | Video Depth Anything | Apache 2.0 for Small, Base and Large |
  | Slow motion from 30 fps | RIFE / GIMM-VFI | To verify |
  | Colour matching between clips | No good open model yet: our measured grade plus LUTs | Commercial references: Colourlab, fylm.ai |

Sources:
- [Higgsfield API models and prices](https://open.higgsfield.ai)
- [Video API pricing 2026](https://devtk.ai/en/blog/ai-video-generation-pricing-2026/)
- [LTX-2.5](https://www.opensourceforu.com/2026/08/ltx-2-5-targets-real-time-video-generation/)
- [MiniMax-Remover](https://arxiv.org/pdf/2505.24873)
- [SeedVR2](https://upsampler.com/blog/seedvr2-diffusion-upscaler)
- [Video Depth Anything](https://huggingface.co/AEmotionStudio/Video-Depth-Anything-Small/blob/main/README.md)
- [Shot matching (SMPTE 2026)](https://journal.smpte.org/periodicals/SMPTE%20Motion%20Imaging%20Journal/135/1/10/)

## 6. Virality: a trained model, not a guess

- **Higgsfield Virality Predictor** (May 2026):
  - inputs clips of up to 15 s;
  - returns viral potential, hook score, hold rate, retention risk, an attention / brain heatmap and an interactive
    dashboard;
  - is available on their platform and via **MCP/CLI** (create from an uploaded video, then preview the dashboard);
  - is **not in their public developer API** (open.higgsfield.ai is generation-only today).

  Next: ask Higgsfield for API access, or run it through their CLI on our server.
- **Our own trained model:** the ICCV 2025 VQualA **SnapUGC** engagement challenge (real Snapchat engagement labels)
  has a baseline repo. The winner ensembled VideoLLaMA2 (with audio) and Qwen2.5-VL; audio matters.
  - Path: fine-tune on SnapUGC.
  - Calibrate on our users' real post numbers (numbers only, never their footage).
  - Pair it with the rule checks and the Opus critic panel already planned.
- **Platforms rank on** watch time, sends per reach and likes per reach (Instagram), and 2026 favours original content.

Sources:
- [Higgsfield virality predictor](https://pasqualepillitteri.it/en/news/2273/higgsfield-virality-predictor-hook-score-hold-rate-2026)
- [VQualA 2025 challenge](https://openaccess.thecvf.com/content/ICCV2025W/VQualA/html/Li_VQualA_2025_Challenge_on_Engagement_Prediction_for_Short_Videos_Methods_ICCVW_2025_paper.html)
- [The winning method](https://arxiv.org/abs/2508.02516v1)
- [SnapUGC baseline](https://arxiv.org/html/2410.00289v1)
- [OpusClip score](https://help.opus.pro/docs/article/virality-score)

## 7. Captions, reframing, platforms, export

- **Captions:**
  - Word-by-word animated styles ("Hormozi" pop, karaoke, highlight, bounce) are table stakes. Open tools burn ASS
    subtitles with FFmpeg (AI Video Captions MIT, Capite MIT, 27 styles).
  - For pro looks, captions become HyperFrames graphics that know the face and the safe zones.
- **Reframing 16:9 → 9:16:** AutoFlip (2020, MediaPipe) is the only open framework. Ours will be SAM 3 subject tracks
  plus our framing code (exists), with speaker switching from diarization.
- **Safe zones (1080×1920):**

  | Platform | Top | Bottom | Sides |
  |---|---|---|---|
  | TikTok | 240 px | 660 px | 120 px each |
  | Reels | ≥14% | ≥35% | ≥6% each |
  | Shorts | 288 px | 672 px | 48 px left, 192 px right |

  The Reels feed also crops to the **4:5 centre**. Platforms change their UI 3-5 times a year, so keep these as data.
- **Specs:** 1080×1920 H.264 MP4, AAC, 30 or 60 fps; Instagram downscales to 1080p. Export presets and formats as in
  the plan (ProRes, HEVC, OTIO/FCPXML…).

Sources:
- [Safe zones](https://reap.video/blog/short-form-video-safe-zones)
- [AutoFlip](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/)
- [AI Video Captions](https://github.com/nicolaigaina/ai-video-captions)
- [Capite](https://alternativeto.net/software/capite/about/)

## 8. What it means for Flicello (the stack)

| Layer | Today | Next |
|---|---|---|
| Understanding | Contact sheets + Claude (Sonnet), whisper, our signals | **Gemini Flash watches each clip (video + audio)**; SAM 3 tracks; faces (SFace); search embeddings; word-level speech with speakers. All in one **index** made on upload. |
| Edit model | EDL JSON per version (exists), code ops (versions.py) | The **timeline as the single source**, changed only by commands (undo / redo, drafts) |
| Preview | Full ffmpeg renders (minutes) | **Instant browser preview from proxies**; full render in the background |
| Graphics | Python drawing | **HyperFrames** (HTML, Apache 2.0), placed with face and subject masks, word timings and beats; Lottie; the pro motion rules and gates |
| Music | Analysis (good), ElevenLabs v1 | + Beat This!, stems (RoFormer), ElevenLabs v2 section edits, Foley |
| Jarvis | Editor panel (slice 1, live) | Instant actions (a code fast path + Haiku router), voice (Scribe/Deepgram + ElevenLabs TTS), Agent SDK workers for graphics and generation |
| Generation | none | Higgsfield API (Kling, Wan, Seedance), draft then final |
| Virality | none | Higgsfield predictor (CLI/MCP, ask for API) + our SnapUGC-trained model + rules + critics |
| Manual editing | strip buttons | A timeline editor (study OpenReel, MIT) |
