// flicello.com: every demo on this page is drawn live, in code. No recordings, no screenshots.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fine = matchMedia("(pointer: fine)").matches;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const DPR = Math.min(2, devicePixelRatio || 1);
  const BEAT = 0.5; // 120 BPM
  const sound = (name, ...a) => window.Sound?.[name]?.(...a);
  const entered = window.FlicelloEntered || Promise.resolve();
  const mouse = { x: innerWidth / 2, y: innerHeight / 3, sx: innerWidth / 2, sy: innerHeight / 3 };
  addEventListener("pointermove", (e) => { mouse.x = e.clientX; mouse.y = e.clientY; }, { passive: true });

  // ---------------------------------------------------------------- 1. the light field: a WebGL shader, like film light
  (function shader() {
    const cv = $(".shader");
    const gl = cv.getContext("webgl", { antialias: false, premultipliedAlpha: false });
    if (!gl) return;
    const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    const fs = `precision highp float;
      uniform vec2 r; uniform float t; uniform vec2 m; uniform float s;
      float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
        return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
      float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p*=2.03;a*=.5;}return v;}
      void main(){
        vec2 uv=gl_FragCoord.xy/r; vec2 p=uv; p.x*=r.x/r.y;
        float tt=t*.045;
        vec2 q=vec2(fbm(p*1.6+tt),fbm(p*1.6-tt+3.1));
        float f=fbm(p*1.9+q*1.7+vec2(tt*.6,-tt*.4)+s*.25);
        vec3 a=vec3(.48,.36,1.); vec3 b=vec3(1.,.31,.55); vec3 c=vec3(1.,.63,.29);
        vec3 col=mix(a,b,smoothstep(.35,.75,f)); col=mix(col,c,smoothstep(.62,.95,f)*.55);
        float beam=pow(max(0.,1.-length((uv-vec2(.5+.25*sin(tt*2.),1.05))*vec2(1.6,.9))),3.);
        vec2 mm=m/r; float glow=exp(-8.*length((uv-vec2(mm.x,1.-mm.y))*vec2(r.x/r.y,1.)));
        float v=smoothstep(.15,.95,f)*.55*(1.-uv.y*.35);
        vec3 o=col*v*.42+col*beam*.18+b*glow*.10;
        o*=1.-smoothstep(.4,1.4,length(uv-.5));
        gl_FragColor=vec4(o,1.);
      }`;
    const sh = (type, src) => { const o = gl.createShader(type); gl.shaderSource(o, src); gl.compileShader(o); return o; };
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(pr);
    if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) return;
    gl.useProgram(pr);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const U = { r: gl.getUniformLocation(pr, "r"), t: gl.getUniformLocation(pr, "t"), m: gl.getUniformLocation(pr, "m"), s: gl.getUniformLocation(pr, "s") };
    const scale = 0.5; // rendered at half size: it is a soft field
    function size() { cv.width = innerWidth * scale; cv.height = innerHeight * scale; gl.viewport(0, 0, cv.width, cv.height); }
    size(); addEventListener("resize", size);
    const t0 = performance.now();
    (function draw(now) {
      gl.uniform2f(U.r, cv.width, cv.height); gl.uniform1f(U.t, calm ? 20 : (now - t0) / 1000);
      gl.uniform2f(U.m, mouse.sx * scale, mouse.sy * scale); gl.uniform1f(U.s, scrollY / innerHeight);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      if (!calm) requestAnimationFrame(draw);
    })(t0);
  })();

  // ---------------------------------------------------------------- 2. the cursor glow, magnetic buttons, spotlights, tilt
  const glow = $(".glow-cursor");
  (function follow() {
    mouse.sx = lerp(mouse.sx, mouse.x, 0.12); mouse.sy = lerp(mouse.sy, mouse.y, 0.12);
    if (glow) glow.style.transform = `translate(${mouse.sx}px, ${mouse.sy}px)`;
    requestAnimationFrame(follow);
  })();
  if (fine && !calm) {
    $$(".magnetic").forEach((b) => {
      b.addEventListener("pointermove", (e) => { const r = b.getBoundingClientRect();
        b.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * .22}px, ${(e.clientY - r.top - r.height / 2) * .3}px)`; });
      b.addEventListener("pointerleave", () => { b.style.transition = "transform .5s cubic-bezier(.2,.8,.2,1)"; b.style.transform = ""; setTimeout(() => (b.style.transition = ""), 500); });
    });
    $$(".spot").forEach((el) => el.addEventListener("pointermove", (e) => { const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", `${e.clientX - r.left}px`); el.style.setProperty("--my", `${e.clientY - r.top}px`); }));
    $$(".tilt").forEach((el) => {
      el.addEventListener("pointermove", (e) => { const r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
        el.style.transform = `rotateY(${x * 12}deg) rotateX(${-y * 10}deg) translateZ(10px)`; });
      el.addEventListener("pointerleave", () => (el.style.transform = ""));
    });
    const scene = $("[data-tilt]"), studio = $(".studio", scene);
    addEventListener("pointermove", (e) => {
      const x = e.clientX / innerWidth - .5, y = e.clientY / innerHeight - .5;
      studio.style.transform = `rotateY(${-14 + x * 16}deg) rotateX(${8 - y * 12}deg)`;
      $$(".float", scene).forEach((f) => { const z = parseFloat(getComputedStyle(f).getPropertyValue("--z")) || 80;
        f.style.transform = `translateZ(${z}px) translate(${x * z * .35}px, ${y * z * .25}px)`; });
    }, { passive: true });
    const wm = $("[data-wordmark]");
    wm?.addEventListener("pointermove", (e) => { const r = wm.getBoundingClientRect(); wm.style.setProperty("--wx", `${e.clientX - r.left}px`); });
  }

  // ---------------------------------------------------------------- 3. text that arrives letter by letter, reveals, counters
  $$(".split").forEach((h) => {
    let k = 0;
    $$(".line", h).forEach((line) => {
      const host = line.querySelector("em") || line;
      const text = host.textContent; host.textContent = "";
      for (const word of text.split(/(\s+)/)) {
        if (/^\s+$/.test(word)) { host.append(word); continue; }
        const w = document.createElement("span"); w.style.display = "inline-block"; w.style.whiteSpace = "nowrap";
        for (const ch of word) { const c = document.createElement("span"); c.className = "char"; c.textContent = ch; c.style.transitionDelay = `${.15 + k++ * .03}s`; w.append(c); }
        host.append(w);
      }
    });
    entered.then(() => setTimeout(() => h.classList.add("in"), 120));
  });
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); if (e.target.matches("h2")) sound("whoosh", .05); } }), { threshold: .12, rootMargin: "0px 0px -8% 0px" });
  $$(".reveal-text").forEach((el) => io.observe(el));
  $$(".tile, .engine, .stack div, .term, .machine, .lede").forEach((el, i) => { el.classList.add("reveal"); el.style.transitionDelay = `${(i % 3) * .08}s`; io.observe(el); });
  $$("[data-count]").forEach((el) => {
    const to = +el.dataset.count;
    entered.then(() => { const t0 = performance.now() + 700; (function up(now) { const u = clamp((now - t0) / 1600, 0, 1); el.textContent = Math.round(to * (1 - Math.pow(1 - u, 3))); if (u < 1) requestAnimationFrame(up); })(t0); });
  });

  // ---------------------------------------------------------------- 4. the hero: an edit assembling itself while Cello talks
  const H = { video: $("[data-video]"), wave: $("[data-wave]"), beats: $("[data-beats]"), head: $("[data-playhead]"), ruler: $("[data-ruler]"),
    ask: $("[data-ask]"), answer: $("[data-answer]"), answerText: $("[data-answer-text]"), frame: $("[data-frame]"), film: $("[data-film]"),
    title: $("[data-title]"), flash: $("[data-flash]"), cap: $("[data-cap]"), tc: $("[data-tc]") };
  const LEN = 16;
  for (let s = 0; s <= LEN; s += 4) { const t = document.createElement("span"); t.textContent = `0:${String(s).padStart(2, "0")}`; H.ruler.append(t); }
  for (let b = 0; b <= LEN / BEAT; b++) { const i = document.createElement("i"); i.style.left = `${(b * BEAT / LEN) * 100}%`; if (b % 4 === 0) i.className = "big"; H.beats.append(i); }
  // the made-up film "the night we jumped" (film.js): the first cut, then the cut Cello makes on the drop
  const firstCut = [[0, 3.5, 0], [3.5, 2.5, 3], [6, 4, 1], [10, 3, 5], [13, 3, 7]];
  const onDrop = [[0, 2, 0], [2, 2, 1], [4, 2, 2], [6, 2, 3], [8, 1, 4], [9, 1, 5], [10, 2, 6], [12, 4, 7]];
  const fctx = H.film.getContext("2d");
  const words = [[1, "the night"], [4.2, "we finally"], [8, "<b>jumped</b>"], [9.6, ""], [10.5, "no going"], [13, "<b>back</b>"]];
  function lay(cut) {
    const clips = $$(".clip", H.video);
    cut.forEach(([a, len], k) => {
      let c = clips[k];
      if (!c) { c = document.createElement("div"); c.className = "clip"; H.video.append(c); }
      c.style.backgroundImage = `url(${Film.thumb(cut[k][2])})`;
      c.style.left = `${(a / LEN) * 100}%`; c.style.width = `calc(${(len / LEN) * 100}% - 3px)`;
      setTimeout(() => c.classList.add("in"), 80 + k * 90);
    });
    clips.slice(cut.length).forEach((c) => c.remove());
  }
  const wctx = H.wave.getContext("2d");
  function drawWave(play) {
    const w = H.wave.width = H.wave.clientWidth * DPR, h = H.wave.height = H.wave.clientHeight * DPR;
    for (let x = 0; x < w; x += 3 * DPR) {
      const t = (x / w) * LEN, loud = t < 8 ? .25 + t / 8 * .35 : .95 - (t - 8) / 16 * .4;
      const a = loud * (.45 + .55 * Math.exp(-((t % BEAT) / BEAT) * 5)) * (.7 + .3 * Math.sin(x * .7)) * h * .45;
      wctx.fillStyle = t < play ? (t >= 8 ? "#ff4f8b" : "#8b6bff") : "#ffffff22";
      wctx.fillRect(x, h / 2 - a, 2 * DPR, a * 2);
    }
  }
  const typeInto = async (el, text, speed = 24, tick) => { el.textContent = ""; for (const ch of text) { el.textContent += ch; if (tick && ch !== " ") sound("type"); await sleep(speed); } };
  const pad = (n) => String(n).padStart(2, "0");
  function play(cut, final) {
    const t0 = performance.now(); let last = -1, lastWord = -1, lastBeat = -1;
    return new Promise((done) => {
      (function step(now) {
        const t = (Math.max(0, now - t0) / 1000) % LEN;
        H.head.style.left = `calc(10px + (100% - 20px) * ${t / LEN})`;
        drawWave(t);
        H.tc.textContent = `00:00:${pad(Math.floor(t))}:${pad(Math.floor(t * 24) % 24)}`;
        const b = Math.floor(t / BEAT); if (b !== lastBeat && now - t0 < LEN * 1000) { lastBeat = b; sound("beat", b, final); }
        const k = cut.findIndex(([a, len]) => t >= a && t < a + len);
        if (k >= 0) { const [W, Hh] = Film.fit(H.film); Film.draw(fctx, W, Hh, cut[k][2], t - cut[k][0] + .8); }
        if (k !== last && k >= 0) {
          last = k;
          $$(".clip", H.video).forEach((c, i) => c.classList.toggle("hot", i === k));
          if (final && Math.abs(cut[k][0] - 8) < .01) {
            for (const [el, cls] of [[H.frame, "shake"], [H.flash, "on"], [H.title, "on"]]) { el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); }
          }
        }
        if (final) {
          const wk = words.findLastIndex(([a]) => t >= a);
          if (wk !== lastWord) { lastWord = wk; H.cap.innerHTML = wk >= 0 ? words[wk][1] : ""; }
        }
        if (now - t0 < LEN * 1000) requestAnimationFrame(step); else done();
      })(t0);
    });
  }
  async function hero() {
    while (true) {
      H.ask.classList.remove("on"); H.frame.classList.remove("cine"); H.cap.innerHTML = "";
      H.answerText.textContent = "I watched your 14 clips. Here is a first cut."; H.answer.classList.add("on");
      $$(".clip", H.video).forEach((c) => c.remove());
      await sleep(500); lay(firstCut); await play(firstCut, false);
      H.answer.classList.remove("on"); await sleep(350);
      H.ask.classList.add("on"); await sleep(1000);
      H.answer.classList.add("on");
      await typeInto(H.answerText, "Done: the jump lands on the drop at 0:08, cut on the beat, with captions, a flash and a shake.", 24, scrollY < innerHeight);
      lay(onDrop); H.frame.classList.add("cine"); await sleep(700);
      await play(onDrop, true); await sleep(900);
    }
  }
  if (calm) { lay(onDrop); drawWave(LEN); { const [W, Hh] = Film.fit(H.film); Film.draw(fctx, W, Hh, 4, 1); } H.frame.classList.add("cine"); H.ask.classList.add("on"); H.answer.classList.add("on"); H.answerText.textContent = "The jump lands on the drop at 0:08."; }
  else hero();

  // ---------------------------------------------------------------- 5. scroll story: raw to ready
  (function story() {
    const sec = $("#story"), stage = $("[data-stage]"), steps = $$("li", $("[data-steps]"));
    const screen = $("[data-st-screen]"), frame = $("[data-st-frame]"), cap = $("[data-st-cap]"), graphic = $("[data-st-graphic]"), score = $("[data-st-score]");
    const clipsHost = $("[data-st-clips]"), track = $("[data-st-track]"), beats = $("[data-st-beats]"), wave = $("[data-st-wave]");
    const colours = ["#7a5cff", "#ff4f8b", "#ffa24a", "#4fe0d0", "#ff7ad1", "#6aa8ff", "#c6ff6a", "#ff6a6a", "#8b6bff", "#ffd166"];
    const keep = [0, 1, 2, 3, 5, 7, 8, 9]; // the clips that make the cut (4 and 6 are the shaky ones)
    const clips = colours.map((c, i) => { const d = document.createElement("div"); d.className = "st-clip";
      d.style.backgroundImage = `url(${Film.thumb(i % 8, .6 + i * .37)})`; clipsHost.append(d);
      if (!keep.includes(i)) { d.classList.add("shaky"); d.innerHTML = "<em>shaky</em>"; }
      else d.innerHTML = `<em class="ok">moment 0:${String(2 + (i * 7) % 50).padStart(2, "0")}</em>`;
      return { el: d, sx: 8 + (i * 37) % 76, sy: 10 + (i * 53) % 70, sr: ((i * 47) % 40) - 20 }; });
    const wctx = wave.getContext("2d");
    const now = { shot: 0, since: 0 };
    Film.live($("[data-st-film]"), (c, w, h, t) => Film.draw(c, w, h, now.shot, t - now.since + .5));
    let lastStep = -1;
    function update(forced) {
      const r = sec.getBoundingClientRect(), total = sec.offsetHeight - innerHeight;
      const p = forced == null ? clamp(-r.top / total, 0, 1) : (forced + .5 + (performance.now() / 4000) % .45) / 5.2;
      const step = Math.min(4, Math.floor(p * 5.2));
      if (step !== lastStep) {
        if (lastStep >= 0) sound("chime", step);
        lastStep = step;
        steps.forEach((li, i) => { li.classList.toggle("on", i === step); li.classList.toggle("done", i < step); });
      }
      const W = stage.clientWidth, Hh = stage.clientHeight;
      const order = (i) => keep.indexOf(i);
      clips.forEach((c, i) => {
        const o = order(i);
        if (step === 0) { // scattered, a pile of raw footage
          c.el.classList.remove("out");
          c.el.style.left = `${c.sx * .85}%`; c.el.style.top = `${c.sy * .9}%`; c.el.style.width = "22%";
          c.el.style.transform = `rotate(${c.sr}deg)`; c.el.style.opacity = 1;
        } else if (o < 0) { c.el.classList.add("out"); c.el.style.opacity = 0; }
        else { // on the timeline: even spacing, then on the beat
          c.el.classList.remove("out"); c.el.style.opacity = 1;
          const n = keep.length, slot = 88 / n;
          const beatW = step >= 2 ? [1.5, 1, 1, 1, .5, .5, 1.5, 1][o] : 1;
          const widths = keep.map((_, k) => (step >= 2 ? [1.5, 1, 1, 1, .5, .5, 1.5, 1][k] : 1));
          const sum = widths.reduce((a, b) => a + b, 0);
          const left = 6 + widths.slice(0, o).reduce((a, b) => a + b, 0) / sum * 88;
          c.el.style.left = `${left}%`; c.el.style.top = "59.2%";
          c.el.style.width = `calc(${beatW / sum * 88}% - 4px)`; c.el.style.transform = "none";
          c.el.style.height = `${Hh * .13}px`; c.el.style.aspectRatio = "auto";
        }
        if (step === 0) { c.el.style.height = ""; c.el.style.aspectRatio = ""; }
      });
      track.classList.toggle("on", step >= 1); beats.classList.toggle("on", step >= 2);
      screen.classList.toggle("on", step >= 1);
      cap.classList.toggle("on", step >= 3); graphic.classList.toggle("on", step >= 3);
      score.classList.toggle("on", step >= 4); wave.classList.toggle("on", step >= 2);
      // the playing frame follows the scroll through the cut
      const sub = (p * 5.2) % 1, k = keep[Math.min(keep.length - 1, Math.floor(sub * keep.length))] % 8;
      if (k !== now.shot) { now.shot = k; now.since = performance.now() / 1000; }
      clips.forEach((c, i) => c.el.classList.toggle("placed", step >= 1));
      if (step >= 2) {
        const w = wave.width = wave.clientWidth * DPR, h = wave.height = wave.clientHeight * DPR;
        for (let x = 0; x < w; x += 4 * DPR) { const u = x / w; const a = (u < .45 ? .3 + u : .95 - (u - .45) * .6) * h * .45 * (.5 + .5 * Math.abs(Math.sin(x * .09 + p * 30)));
          wctx.fillStyle = u < .45 ? "#8b6bff" : "#ff4f8b"; wctx.fillRect(x, h / 2 - a, 2.5 * DPR, a * 2); }
      }
    }
    if (innerWidth > 1060) { addEventListener("scroll", () => update(), { passive: true }); addEventListener("resize", () => update()); update(); }
    else { // small screens: it plays by itself, a step every few seconds
      let s = 0; const tick = () => update(s++ % 5);
      tick(); setInterval(tick, 2600); addEventListener("resize", () => update((s + 4) % 5));
    }
  })();

  // ---------------------------------------------------------------- 6. Cello's voice: a particle orb that listens to the cursor
  (function orb() {
    const cv = $("[data-orb]"); if (!cv) return;
    const ctx = cv.getContext("2d"), tile = cv.parentElement;
    const N = 520, pts = Array.from({ length: N }, (_, i) => { const phi = Math.acos(1 - 2 * (i + .5) / N), th = Math.PI * (1 + Math.sqrt(5)) * i; return { x: Math.sin(phi) * Math.cos(th), y: Math.sin(phi) * Math.sin(th), z: Math.cos(phi) }; });
    let lx = 0, ly = 0, energy = 0;
    tile.addEventListener("pointermove", (e) => { const r = tile.getBoundingClientRect(); lx = (e.clientX - r.left) / r.width - .5; ly = (e.clientY - r.top) / r.height - .5; energy = 1; });
    (function draw(now) {
      const w = cv.width = tile.clientWidth * DPR, h = cv.height = tile.clientHeight * DPR;
      const cx = w * .68, cy = h * .4, R = Math.min(w, h) * .26;
      const t = now / 1000, talk = .5 + .5 * Math.sin(t * 7) * Math.sin(t * 2.3);
      energy = lerp(energy, 0, .02);
      const ay = t * .35 + lx * 1.2, ax = .3 + ly * .8;
      for (const p of pts) {
        let x = p.x * Math.cos(ay) - p.z * Math.sin(ay), z = p.x * Math.sin(ay) + p.z * Math.cos(ay), y = p.y * Math.cos(ax) - z * Math.sin(ax); z = p.y * Math.sin(ax) + z * Math.cos(ax);
        const wob = 1 + .08 * talk * Math.sin(p.y * 6 + t * 6) + .12 * energy * Math.sin(p.x * 9 + t * 9);
        const s = (z + 2) / 3, px = cx + x * R * wob, py = cy + y * R * wob;
        ctx.fillStyle = `hsla(${300 + z * 40 + y * 30},95%,${62 + z * 15}%,${.25 + .6 * s})`;
        ctx.beginPath(); ctx.arc(px, py, (1 + 1.6 * s) * DPR, 0, 7); ctx.fill();
      }
      if (!calm) requestAnimationFrame(draw);
    })(0);
    const said = $("[data-said]"), lines = ["zoom in when I say now", "make it warmer", "captions, bold, at the top", "what's in shot three?", "slower here, then hit the drop"];
    let k = 0; (async () => { while (true) { await typeInto(said, `“${lines[k++ % lines.length]}”`, 42); await sleep(1700); } })();
  })();

  // ---------------------------------------------------------------- 7. the small demos
  const tr = $("[data-transcript]");
  if (tr) {
    const ws = tr.innerHTML.split(/\s+/);
    tr.innerHTML = ws.map((w) => `<span${/<i>/.test(w) ? " data-um" : ""}>${w.replace(/<\/?i>/g, "")}</span>`).join(" ");
    const spans = $$("span", tr); let k = 0;
    setInterval(() => { spans.forEach((s, i) => { s.classList.toggle("said2", i < k); s.classList.toggle("now", i === k && !s.hasAttribute("data-um")); s.classList.toggle("cut", i <= k && s.hasAttribute("data-um")); }); k = (k + 1) % (spans.length + 4); }, 320);
  }
  const mc = $("[data-music]");
  if (mc) {
    const m = mc.getContext("2d");
    (function draw(now) {
      const w = mc.width = mc.clientWidth * DPR, h = mc.height = mc.clientHeight * DPR, grow = ((now / 1000) % 6) / 4.5;
      for (let x = 0; x < w * Math.min(1, grow); x += 4 * DPR) {
        const u = x / w, sec = u < .2 ? .25 : u < .45 ? .25 + (u - .2) * 2.2 : u < .8 ? .95 : .5 - (u - .8);
        const a = sec * h * .36 * (.55 + .45 * Math.abs(Math.sin(x * .13 + now / 400)));
        const g = m.createLinearGradient(0, h * .45 - a, 0, h * .45 + a); g.addColorStop(0, u >= .45 && u < .8 ? "#ff4f8b" : "#8b6bff"); g.addColorStop(1, "#ffa24a");
        m.fillStyle = g; m.fillRect(x, h * .45 - a, 2.5 * DPR, a * 2);
      }
      if (!calm) requestAnimationFrame(draw);
    })(0);
  }
  const hits = $("[data-hits]");
  let hitsSeen = false;
  if (hits) new IntersectionObserver((es) => (hitsSeen = es[0].isIntersecting), { threshold: .5 }).observe(hits);
  if (hits && !calm) {
    const names = ["whoosh", "impact", "creak", "splash", "pop", "thud", "chime", "tick"]; let n = 0;
    setInterval(() => {
      const x = 15 + Math.random() * 70, y = 20 + Math.random() * 60, col = ["#ffa24a", "#ff4f8b", "#8b6bff", "#4fe0d0"][n % 4];
      const r = document.createElement("div"); r.className = "hit"; r.style.left = `${x}%`; r.style.top = `${y}%`; r.style.borderColor = col;
      const l = document.createElement("div"); l.className = "lab"; l.textContent = names[n % names.length]; l.style.left = `${x}%`; l.style.top = `${y}%`;
      if (hitsSeen) sound("hit", names[n % names.length]);
      hits.append(r, l); n++; setTimeout(() => { r.remove(); l.remove(); }, 1350);
    }, 500);
  }

  // the editor tile: the film plays under its playhead; the clip being trimmed is the one on screen
  const lanes = $("[data-lanes]");
  if (lanes) {
    const vids = $$(".lane:first-child b", lanes), shotOf = [0, 2, 4, 6];
    vids.forEach((b, i) => (b.style.backgroundImage = `url(${Film.thumb(shotOf[i], 1.2)})`));
    { // the music lane: its waveform, loud after the drop
      const cv = document.createElement("canvas"), m = cv.getContext("2d"); cv.width = 600; cv.height = 60;
      for (let x = 0; x < 600; x += 4) { const u = x / 600, a = (u < .5 ? .25 + u * .6 : .9 - (u - .5) * .5) * 26 * (.45 + .55 * Math.abs(Math.sin(x * .21) * Math.cos(x * .047)));
        m.fillStyle = u < .5 ? "#4fe0d0" : "#ff4f8b"; m.fillRect(x, 30 - a, 2.5, a * 2); }
      $(".lane.m b", lanes).style.background = `url(${cv.toDataURL()}) center/100% 100% no-repeat, #ffffff06`;
    }
    const head = $("[data-ed-head]"), LOOP = 8;
    Film.live($("[data-ed-prev]"), (c, w, h, t) => {
      const u = (t % LOOP) / LOOP, x = u * lanes.clientWidth; head.style.left = `${x}px`;
      let k = 0, from = 0;
      vids.forEach((b, i) => { if (x >= b.offsetLeft) { k = i; from = b.offsetLeft / lanes.clientWidth; } });
      Film.draw(c, w, h, shotOf[k], (u - from) * LOOP + .5);
    });
  }
  // the cartoon engines, each in its own scene
  $$("[data-scene]").forEach((cv) => Film.live(cv, Film.scenes[cv.dataset.scene]));

  // ---------------------------------------------------------------- 8. the terminal
  const term = $("[data-term]");
  const script = [["p", "$ "], ["", "git clone https://github.com/SIDDHANTH-THAKURI/Flicello\n"], ["p", "$ "], ["", "cd Flicello && ./flicello\n"],
    ["dim", "  setting up · building the studio · checking your machine\n"], ["ok", "  ✓ Flicello is running at http://127.0.0.1:8765\n\n"],
    ["p", "you › "], ["you", "make a 30 second hype edit from my trip, cut on the beat"]];
  $("[data-copy]")?.addEventListener("click", async (e) => { await navigator.clipboard?.writeText("git clone https://github.com/SIDDHANTH-THAKURI/Flicello\ncd Flicello && ./flicello"); e.target.textContent = "Copied"; setTimeout(() => (e.target.textContent = "Copy"), 1600); });
  async function typeTerm() {
    while (true) {
      term.innerHTML = "";
      for (const [cls, text] of script) { const s = document.createElement("span"); if (cls) s.className = cls; term.append(s);
        for (const ch of text) { s.textContent += ch; if (cls !== "dim" && cls !== "ok" && ch !== " ") sound("type"); await sleep(cls === "dim" || cls === "ok" ? 5 : 30); } if (cls === "dim") await sleep(500); }
      const c = document.createElement("span"); c.className = "caret"; term.append(c); await sleep(4200);
    }
  }
  let termOn = false;
  new IntersectionObserver((es) => { if (es[0].isIntersecting && !termOn) { termOn = true; typeTerm(); } }).observe(term);
})();
