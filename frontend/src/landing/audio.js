// The page's beat. A clock always runs (it times the cuts and pulses even in silence); sound is opt-in and made
// in code, so there is nothing to license and nothing plays before the visitor asks. Levels are kept low and
// the top end is rolled off: nothing sharp, nothing loud.
const BPM = 112;
const SPB = 60 / BPM;          // seconds per beat
const CHORDS = [               // Am  F  C  G, voiced close and low
  [220.0, 261.63, 329.63],
  [174.61, 220.0, 261.63],
  [196.0, 261.63, 329.63],
  [196.0, 246.94, 293.66],
];
const ROOTS = [55.0, 43.65, 65.41, 49.0];

export function createBeat() {
  const t0 = performance.now() / 1000;
  let ctx = null, master = null, started = 0, on = false, timer = 0, nextStep = 0, step = 0;
  let padBus = null, delay = null, noiseBuf = null;
  const listeners = new Set();
  // the silent clock can be re-anchored so turning sound off doesn't jump the cuts
  let tShift = t0;
  const silent = () => performance.now() / 1000 - tShift;
  const clock = () => (on && ctx ? ctx.currentTime - started : silent());

  function build() {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain();
    master.gain.value = 0;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18; comp.knee.value = 12; comp.ratio.value = 3; comp.attack.value = 0.01; comp.release.value = 0.25;
    const tone = ctx.createBiquadFilter();
    tone.type = 'lowpass'; tone.frequency.value = 7200; tone.Q.value = 0.3;
    master.connect(comp); comp.connect(tone); tone.connect(ctx.destination);

    padBus = ctx.createGain();
    padBus.gain.value = 0.5;
    const padFilter = ctx.createBiquadFilter();
    padFilter.type = 'lowpass'; padFilter.frequency.value = 900; padFilter.Q.value = 0.6;
    const lfo = ctx.createOscillator(); const lfoGain = ctx.createGain();
    lfo.frequency.value = 0.05; lfoGain.gain.value = 350; lfo.connect(lfoGain); lfoGain.connect(padFilter.frequency); lfo.start();
    padBus.connect(padFilter); padFilter.connect(master);

    delay = ctx.createDelay(1.0);
    delay.delayTime.value = SPB * 0.75;
    const fb = ctx.createGain(); fb.gain.value = 0.32;
    const wet = ctx.createGain(); wet.gain.value = 0.28;
    const dampen = ctx.createBiquadFilter(); dampen.type = 'lowpass'; dampen.frequency.value = 2400;
    delay.connect(dampen); dampen.connect(fb); fb.connect(delay); dampen.connect(wet); wet.connect(master);

    noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 0.5, ctx.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  }

  function kick(t) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine';
    o.frequency.setValueAtTime(120, t); o.frequency.exponentialRampToValueAtTime(42, t + 0.12);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.9, t + 0.004); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.42);
    o.connect(g); g.connect(master); o.start(t); o.stop(t + 0.45);
    // the pad breathes around the kick
    padBus.gain.cancelScheduledValues(t);
    padBus.gain.setValueAtTime(0.22, t); padBus.gain.linearRampToValueAtTime(0.5, t + SPB * 0.8);
  }
  function hat(t, level) {
    const s = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = noiseBuf; f.type = 'bandpass'; f.frequency.value = 6500; f.Q.value = 0.9;
    g.gain.setValueAtTime(level, t); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
    s.connect(f); f.connect(g); g.connect(master); s.start(t); s.stop(t + 0.06);
  }
  function clap(t) {
    const s = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    s.buffer = noiseBuf; f.type = 'bandpass'; f.frequency.value = 1700; f.Q.value = 0.7;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.16, t + 0.006); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    s.connect(f); f.connect(g); g.connect(master); s.start(t); s.stop(t + 0.2);
  }
  function bass(t, hz) {
    const o = ctx.createOscillator(), g = ctx.createGain(), f = ctx.createBiquadFilter();
    o.type = 'triangle'; o.frequency.value = hz;
    f.type = 'lowpass'; f.frequency.value = 220;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.32, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + SPB * 0.45);
    o.connect(f); f.connect(g); g.connect(master); o.start(t); o.stop(t + SPB * 0.5);
  }
  function pad(t, chord) {
    const dur = SPB * 4;
    chord.forEach((hz) => {
      [-6, 6].forEach((cents) => {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'sawtooth'; o.frequency.value = hz; o.detune.value = cents;
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.035, t + 0.5);
        g.gain.setValueAtTime(0.035, t + dur - 0.4); g.gain.exponentialRampToValueAtTime(0.0001, t + dur + 0.6);
        o.connect(g); g.connect(padBus); o.start(t); o.stop(t + dur + 0.7);
      });
    });
  }
  function pluck(t, hz) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'triangle'; o.frequency.value = hz * 2;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.06, t + 0.005); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
    o.connect(g); g.connect(master); g.connect(delay); o.start(t); o.stop(t + 0.3);
  }

  function schedule() {
    const ahead = ctx.currentTime + 0.12;
    while (nextStep < ahead) {
      const s = step % 64;               // 4 bars of 16ths
      const bar = Math.floor(s / 16);
      const chord = CHORDS[bar];
      const t = nextStep;
      if (s % 4 === 0) kick(t);
      if (s % 16 === 10 && bar % 2 === 1) kick(t);
      if (s % 8 === 4) clap(t);
      if (s % 4 === 2) hat(t, 0.05);
      else if (s % 2 === 1) hat(t, 0.018);
      if (s % 2 === 0) bass(t, ROOTS[bar] * (s % 8 === 6 ? 2 : 1));
      if (s % 16 === 0) pad(t, chord);
      if ([0, 3, 6, 10, 12].includes(s % 16)) pluck(t, chord[(s >> 1) % 3]);
      nextStep += SPB / 4;
      step++;
    }
  }

  async function setSound(want) {
    if (want && !ctx) build();
    if (!ctx) return;
    if (want) {
      await ctx.resume();
      if (!on) {
        // keep the visual clock continuous: the audio clock starts where the silent one is
        const now = clock();
        started = ctx.currentTime - now;
        const beatPos = Math.ceil(now / (SPB / 4));
        step = beatPos;
        nextStep = started + beatPos * (SPB / 4);
        timer = window.setInterval(schedule, 25);
      }
      on = true;
      master.gain.cancelScheduledValues(ctx.currentTime);
      master.gain.setValueAtTime(master.gain.value, ctx.currentTime);
      master.gain.linearRampToValueAtTime(0.42, ctx.currentTime + 1.2);
    } else if (on) {
      master.gain.cancelScheduledValues(ctx.currentTime);
      master.gain.setValueAtTime(master.gain.value, ctx.currentTime);
      master.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.5);
      const stopAt = ctx.currentTime + 0.55;
      const offset = clock();
      window.setTimeout(() => {
        window.clearInterval(timer);
        ctx.suspend();
        on = false;
        // hand the clock back to the silent timer without a jump
        const now = performance.now() / 1000;
        tShift = now - offset;
      }, Math.max(0, (stopAt - ctx.currentTime) * 1000));
    }
    listeners.forEach((fn) => fn(on || want));
  }

  function read() {
    const t = on && ctx ? ctx.currentTime - started : silent();
    const beats = t / SPB;
    const idx = Math.floor(beats);
    const frac = beats - idx;
    return { t, beat: idx, frac, pulse: Math.exp(-frac * 5.5), playing: on };
  }

  return { read, setSound, onChange: (fn) => listeners.add(fn), get on() { return on; }, BPM };
}
