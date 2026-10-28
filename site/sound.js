// flicello.com's sound: the doorway, the score and every little sound on the page, all made here with Web Audio.
// No audio files. Nothing plays until someone chooses sound at the door.
(() => {
  const $ = (s) => document.querySelector(s);
  const KEY = "flicello:sound";
  const D = (n) => 440 * Math.pow(2, (n - 69) / 12); // midi to Hz
  let ctx, master, wet, padBus, on = false, padTimer, sparkTimer, lastUi = 0, lastImpact = 0;

  // ---------------------------------------------------------------- the engine
  function init() {
    if (ctx) return;
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain(); master.gain.value = 0;
    const comp = ctx.createDynamicsCompressor(); comp.threshold.value = -16; comp.ratio.value = 3;
    master.connect(comp).connect(ctx.destination);
    const n = ctx.sampleRate * 3.4, ir = ctx.createBuffer(2, n, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) { const d = ir.getChannelData(ch); for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 2.8); }
    const verb = ctx.createConvolver(); verb.buffer = ir;
    wet = ctx.createGain(); wet.gain.value = .5; wet.connect(verb).connect(master);
    padBus = ctx.createGain(); padBus.gain.value = 1; padBus.connect(master);
    document.addEventListener("visibilitychange", () => { if (!ctx) return; document.hidden ? ctx.suspend() : on && ctx.resume(); });
  }
  const now = () => ctx.currentTime;
  function out(node, dry = 1, send = .25, bus = master) {
    const g = ctx.createGain(); g.gain.value = dry; node.connect(g).connect(bus);
    if (send) { const s = ctx.createGain(); s.gain.value = send; node.connect(s).connect(wet); }
  }
  function env(g, at, a, d, v) { g.gain.setValueAtTime(0.0001, at); g.gain.exponentialRampToValueAtTime(v, at + a); g.gain.exponentialRampToValueAtTime(0.0001, at + a + d); }
  function tone(f, { type = "sine", at = now(), a = .005, d = .3, v = .2, send = .25, to } = {}) {
    const o = ctx.createOscillator(), g = ctx.createGain(); o.type = type; o.frequency.setValueAtTime(f, at);
    if (to) o.frequency.exponentialRampToValueAtTime(to, at + a + d);
    env(g, at, a, d, v); o.connect(g); out(g, 1, send); o.start(at); o.stop(at + a + d + .05);
  }
  let noiseBuf;
  function noise({ at = now(), d = .2, v = .2, type = "bandpass", f = 1200, to, q = 1, a = .005, send = .2 } = {}) {
    if (!noiseBuf) { noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate); const x = noiseBuf.getChannelData(0); for (let i = 0; i < x.length; i++) x[i] = Math.random() * 2 - 1; }
    const s = ctx.createBufferSource(), fl = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = noiseBuf; s.loop = true; fl.type = type; fl.Q.value = q; fl.frequency.setValueAtTime(f, at);
    if (to) fl.frequency.exponentialRampToValueAtTime(to, at + a + d);
    env(g, at, a, d, v); s.connect(fl).connect(g); out(g, 1, send); s.start(at); s.stop(at + a + d + .05);
  }
  function bell(f, v = .06, at = now()) { for (const [m, k] of [[1, 1], [2.76, .4], [5.4, .18], [8.9, .08]]) tone(f * m, { at, a: .002, d: 2.4 / m + .3, v: v * k, send: .6 }); }
  function kick(at = now(), v = .7) { tone(150, { at, a: .002, d: .42, v, to: 40, send: .05 }); noise({ at, d: .02, v: v * .3, type: "highpass", f: 3000, send: 0 }); }
  function hat(at = now(), v = .05) { noise({ at, d: .045, v, type: "highpass", f: 8000, send: .05 }); }
  function clap(at = now(), v = .2) { for (const k of [0, .012, .024]) noise({ at: at + k, d: .14, v: v * (k ? .6 : 1), f: 1400, q: 1.4, send: .4 }); }
  function bass(f, at, d = .4, v = .16) {
    const o = ctx.createOscillator(), fl = ctx.createBiquadFilter(), g = ctx.createGain(); o.type = "sawtooth"; o.frequency.value = f;
    fl.type = "lowpass"; fl.frequency.setValueAtTime(900, at); fl.frequency.exponentialRampToValueAtTime(160, at + d);
    env(g, at, .005, d, v); o.connect(fl).connect(g); out(g, 1, .05); o.start(at); o.stop(at + d + .05);
  }

  // the bed: a slow pad in D, four chords, with a bell now and then
  const CHORDS = [[50, 57, 64, 66, 69], [47, 54, 62, 66, 69], [43, 50, 59, 62, 66], [45, 52, 61, 64, 67]];
  let chord = 0;
  function padChord() {
    const at = now() + .05, len = 9;
    for (const m of CHORDS[chord++ % 4]) for (const det of [-7, 7]) {
      const o = ctx.createOscillator(), fl = ctx.createBiquadFilter(), g = ctx.createGain();
      o.type = "sawtooth"; o.frequency.value = D(m); o.detune.value = det;
      fl.type = "lowpass"; fl.frequency.setValueAtTime(380, at); fl.frequency.linearRampToValueAtTime(900, at + len * .5); fl.frequency.linearRampToValueAtTime(420, at + len);
      g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(.016, at + 3); g.gain.setValueAtTime(.016, at + len - 2.5); g.gain.linearRampToValueAtTime(0, at + len + 1);
      o.connect(fl).connect(g); out(g, 1, .55, padBus); o.start(at); o.stop(at + len + 1.2);
    }
  }
  function startBed() {
    if (padTimer) return;
    padChord(); padTimer = setInterval(padChord, 8000);
    const spark = () => { if (on) bell(D([74, 76, 78, 81, 83, 86][Math.floor(Math.random() * 6)]), .025); sparkTimer = setTimeout(spark, 3500 + Math.random() * 5000); };
    sparkTimer = setTimeout(spark, 2500);
  }
  function duck(depth = .25, hold = 8) { if (!ctx) return; const g = padBus.gain, t = now(); g.cancelScheduledValues(t); g.setTargetAtTime(depth, t, .08); g.setTargetAtTime(1, t + hold, .8); }

  // ---------------------------------------------------------------- what the page asks for
  const live = () => on && ctx && ctx.state === "running";
  const S = {
    get on() { return on; },
    ui(kind) { // hovers and clicks: barely there
      if (!live()) return; const t = performance.now();
      if (kind === "hover") { if (t - lastUi < 70) return; lastUi = t; tone(2400 + Math.random() * 300, { d: .035, v: .018, send: .1 }); }
      if (kind === "click") { tone(880, { d: .12, v: .07, to: 1320, send: .3 }); tone(1760, { at: now() + .04, d: .2, v: .03, send: .5 }); }
    },
    type() { if (live()) noise({ d: .012, v: .03, type: "highpass", f: 4000 + Math.random() * 2000, send: 0 }); },
    whoosh(v = .08) { if (live()) noise({ d: .7, a: .25, v, f: 300, to: 4200, q: .8, send: .4 }); },
    chime(step) { if (live()) bell(D([69, 71, 74, 76, 81][step % 5]), .05); },
    impact() { // the anime impact frame
      if (!live()) return; const t = performance.now(); if (t - lastImpact < 1500) return; lastImpact = t;
      kick(now(), .55); noise({ d: .5, v: .25, f: 5000, to: 400, q: .7, send: .5 }); tone(2600, { d: .6, v: .04, to: 3400, send: .7 });
    },
    hit(name) { // the "sound on every hit" tile
      if (!live()) return; const at = now();
      ({ whoosh: () => noise({ at, d: .35, a: .1, v: .06, f: 400, to: 3000, send: .3 }), impact: () => kick(at, .3), creak: () => tone(180, { at, type: "sawtooth", a: .05, d: .3, v: .02, to: 140 }),
        splash: () => noise({ at, d: .4, v: .05, type: "highpass", f: 2500, send: .4 }), pop: () => tone(600, { at, d: .08, v: .07, to: 1500 }), thud: () => tone(90, { at, d: .2, v: .2, to: 50 }),
        chime: () => bell(D(88), .03, at), tick: () => tone(3200, { at, d: .02, v: .04 }) }[name] || (() => {}))();
    },
    // the hero edit is a little track at 120 BPM: a soft first cut, then the cut on the drop at 0:08
    beat(b, final) {
      if (!live() || scrollY > innerHeight * .9) return;
      const at = now() + .02, half = .25, roots = [38, 35, 31, 33];
      if (!final) { if (b % 4 === 0) kick(at, .22); hat(at + half, .025); return; }
      if (b < 12) { if (b % 2 === 0) kick(at, .35); hat(at + half, .035); }
      else if (b === 12) { // the build
        noise({ at, d: 2, a: 1.9, v: .14, f: 300, to: 6000, q: 1.2, send: .4 }); tone(D(50), { at, type: "sawtooth", a: 1.9, d: .1, v: .03, to: D(62) });
        for (let k = 0; k < 8; k++) clap(at + k * .25 * (1 - k * .06), .05 + k * .015);
      } else if (b === 16) { // the drop
        duck(.3, 7.5); kick(at, .9); bass(D(roots[0]), at, .45, .2); noise({ at, d: 1.6, v: .2, type: "highpass", f: 6000, send: .6 });
        for (const m of [62, 66, 69, 74]) bell(D(m), .05, at);
      } else if (b > 16 && b < 32) {
        kick(at, .7); bass(D(roots[Math.floor((b - 16) / 4) % 4]), at, .4); if (b % 2 === 1) clap(at, .16); hat(at + half, .05);
      }
    },
    set(want) {
      if (want) init();
      on = !!want; localStorage.setItem(KEY, on ? "on" : "off");
      document.documentElement.classList.toggle("sound-on", on);
      if (!ctx) return;
      const t = now(); master.gain.cancelScheduledValues(t); master.gain.setTargetAtTime(on ? .9 : 0, t, on ? .4 : .15);
      if (on) { ctx.resume(); startBed(); }
    },
  };
  window.Sound = S;

  // ---------------------------------------------------------------- the door
  const door = $("[data-intro]");
  let open;
  window.FlicelloEntered = new Promise((r) => (open = r));
  const skip = !door || /[?&]nointro\b/.test(location.search);
  function enter(withSound) {
    if (withSound) {
      S.set(true);
      const at = now() + .05; noise({ at, d: 1.6, a: .6, v: .16, f: 200, to: 5000, q: .6, send: .6 });
      kick(at + .9, .5); for (const m of [50, 57, 62, 66, 69, 74]) bell(D(m), .04, at + .9 + (m - 50) * .006);
    } else S.set(false);
    door.classList.add("out"); document.documentElement.classList.remove("locked");
    setTimeout(() => open(), 700); setTimeout(() => door.remove(), 1700);
  }
  if (skip) { door?.remove(); document.documentElement.classList.remove("locked"); open(); }
  else {
    door.querySelector("[data-enter=sound]").addEventListener("click", () => enter(true));
    door.querySelector("[data-enter=quiet]").addEventListener("click", () => enter(false));
    addEventListener("keydown", function k(e) { if (e.key === "Enter") { removeEventListener("keydown", k); enter(true); } });
    if (window.Film) Film.live(door.querySelector("canvas"), (c, w, h, t) => Film.draw(c, w, h, 4, t * .6 + .3));
  }

  // the switch in the corner
  const sw = $("[data-sound]");
  sw?.addEventListener("click", () => { S.set(!on); if (on) S.ui("click"); });
  document.documentElement.classList.toggle("sound-on", false);

  // small sounds everywhere: hover, press, sections arriving
  document.addEventListener("pointerover", (e) => { if (e.target.closest("a, button")) S.ui("hover"); });
  document.addEventListener("click", (e) => { if (e.target.closest("a, button:not([data-sound]):not([data-enter])")) S.ui("click"); });
})();
