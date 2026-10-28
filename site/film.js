// The made-up films on flicello.com: "the night we jumped" and the three cartoon engines' scenes.
// Every frame is drawn here, in code, as it plays. No recordings.
(() => {
  const TAU = Math.PI * 2;
  const DPR = Math.min(2, devicePixelRatio || 1);
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const hash = (n) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
  function rng(seed) { let s = (seed * 2654435761) >>> 0 || 1; return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; }; }
  const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };

  // ---------------------------------------------------------------- brushes
  function sky(c, w, h, stops, y0 = 0, y1 = h) {
    const g = c.createLinearGradient(0, y0, 0, y1);
    for (const [o, col] of stops) g.addColorStop(o, col);
    c.fillStyle = g; c.fillRect(0, y0, w, y1 - y0);
  }
  function glow(c, x, y, r, col, a = 1) {
    const g = c.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(col, a)); g.addColorStop(.35, rgba(col, a * .35)); g.addColorStop(1, rgba(col, 0));
    c.fillStyle = g; c.fillRect(x - r, y - r, r * 2, r * 2);
  }
  function disc(c, x, y, r, col) { c.fillStyle = col; c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill(); }
  function stars(c, w, h, n, seed, t, maxY = .6) {
    const r = rng(seed); c.fillStyle = "#fff";
    for (let i = 0; i < n; i++) {
      const x = r() * w, y = r() * h * maxY, s = (r() * 1.3 + .4) * h / 420, sp = 1 + r() * 3, base = r();
      c.globalAlpha = .15 + .7 * base * (.5 + .5 * Math.sin(t * sp + i));
      c.fillRect(x, y, s, s);
    }
    c.globalAlpha = 1;
  }
  function clouds(c, w, h, t, seed, col, a, y0, y1, speed = .012) {
    const r = rng(seed), L = w + h * 1.6; c.fillStyle = col;
    for (let i = 0; i < 7; i++) {
      const x = ((r() * L + t * speed * h * (1 + r())) % L) - h * .8, y = h * (y0 + r() * (y1 - y0)), sw = h * (.25 + r() * .45), sh = h * (.012 + r() * .022);
      c.globalAlpha = a * (.45 + r() * .55); c.beginPath(); c.ellipse(x, y, sw, sh, 0, 0, TAU); c.fill();
    }
    c.globalAlpha = 1;
  }
  function birds(c, w, h, t, n, seed, col) {
    const r = rng(seed); c.strokeStyle = col; c.lineWidth = Math.max(1, h * .004); c.lineCap = "round";
    for (let i = 0; i < n; i++) {
      const sp = .025 + r() * .02, x = (((r() + t * sp) % 1.2) - .1) * w, y = h * (.18 + r() * .22) + Math.sin(t * 1.3 + i) * h * .012;
      const s = h * (.01 + r() * .008), f = Math.sin(t * 9 + i * 2);
      c.beginPath(); c.moveTo(x - s, y - s * f * .7); c.quadraticCurveTo(x - s * .4, y - s * .15 * f, x, y); c.quadraticCurveTo(x + s * .4, y - s * .15 * f, x + s, y - s * f * .7); c.stroke();
    }
  }
  function ridge(c, w, h, base, seed, col, amp, off = 0) {
    c.fillStyle = col; c.beginPath(); c.moveTo(0, h);
    for (let x = 0; x <= w + w / 80; x += w / 80) {
      const u = x / h + off + seed;
      c.lineTo(x, base - amp * h * (.5 + .3 * Math.sin(u * 2.1) + .2 * Math.sin(u * 5.3 + 1) + .08 * Math.sin(u * 13.7)));
    }
    c.lineTo(w, h); c.closePath(); c.fill();
  }
  function streaks(c, w, h, t, speed, a = .14, maxY = .78) {
    c.strokeStyle = `rgba(255,255,255,${a})`; c.lineWidth = Math.max(1, h * .003);
    for (let i = 0; i < 16; i++) {
      const y = hash(i) * h * maxY, sp = speed * (.5 + hash(i + 3)), x = (((hash(i + 9) - t * sp) % 1 + 1) % 1) * w * 1.4 - w * .2;
      c.beginPath(); c.moveTo(x, y); c.lineTo(x + h * (.15 + hash(i + 5) * .2), y); c.stroke();
    }
  }

  // a city skyline, made once per seed, scrolled for parallax
  const cities = {};
  function city(seed, minH, maxH) {
    const key = `${seed}:${minH}:${maxH}`; if (cities[key]) return cities[key];
    const r = rng(seed), b = []; let x = 0;
    while (x < 3) { const w = .05 + r() * .13; b.push({ x, w, h: minH + r() * (maxH - minH), ant: r() < .35, tank: r() < .18, step: r() < .3, s: (r() * 997) | 0 }); x += w + r() * .012; }
    return (cities[key] = { b, len: x });
  }
  function skyline(c, w, h, cy, off, base, col, win, amount, t) {
    const L = cy.len * h, o = (((off * h) % L) + L) % L, vis = [];
    c.fillStyle = col;
    for (let rep = 0; rep * L - o < w; rep++) for (const B of cy.b) {
      const x = B.x * h + rep * L - o, bw = B.w * h; if (x > w || x + bw < 0) continue;
      const top = base - B.h * h, st = B.step ? B.h * h * .08 : 0;
      c.fillRect(x, top, bw + .6, h - top);
      if (st) c.fillRect(x + bw * .2, top - st, bw * .6, st + 1);
      if (B.ant) c.fillRect(x + bw * .7, top - st - h * .07, Math.max(1, h * .004), h * .07);
      if (B.tank) c.fillRect(x + bw * .12, top - h * .035, bw * .3, h * .035);
      vis.push([x, bw, top, B]);
    }
    if (!amount) return;
    c.fillStyle = win;
    const ww = h * .009, wh = h * .012, gx = h * .022, gy = h * .03;
    for (const [x, bw, top, B] of vis) {
      let row = 0;
      for (let yy = top + gy * .6; yy < base - gy * .4; yy += gy, row++) {
        let col = 0;
        for (let xx = x + gx * .4; xx < x + bw - ww - gx * .2; xx += gx, col++) {
          const k = hash(B.s + col * 7.1 + row * 13.7);
          if (k < amount) { c.globalAlpha = .5 + .5 * Math.sin(t * .6 + k * 60) ** 2; c.fillRect(xx, yy, ww, wh); }
        }
      }
    }
    c.globalAlpha = 1;
  }

  // ---------------------------------------------------------------- people: a little rig of two-bone limbs
  // angles are from straight down; positive swings forward (towards +x)
  function bone(c, x, y, a, l) { const nx = x + Math.sin(a) * l, ny = y + Math.cos(a) * l; c.lineTo(nx, ny); return [nx, ny]; }
  function limb(c, x, y, a1, l1, a2, l2) { c.beginPath(); c.moveTo(x, y); const [kx, ky] = bone(c, x, y, a1, l1); bone(c, kx, ky, a2, l2); c.stroke(); }
  function ribbon(c, x, y, len, wd, t, col, lift = .1, speed = 9) {
    const n = 12, pts = [];
    for (let i = 0; i <= n; i++) { const u = i / n; pts.push([x - u * len, y + u * len * lift + Math.sin(t * speed - u * 5) * len * .13 * u]); }
    c.fillStyle = col; c.beginPath();
    pts.forEach(([px, py], i) => { const q = wd * (1 - .65 * i / n) / 2; i ? c.lineTo(px, py - q) : c.moveTo(px, py - q); });
    for (let i = n; i >= 0; i--) { const [px, py] = pts[i], q = wd * (1 - .65 * i / n) / 2; c.lineTo(px, py + q); }
    c.closePath(); c.fill();
  }
  function person(c, x, y, s, pose, col, o = {}) {
    c.save(); c.translate(x, y); c.scale(o.dir || 1, 1);
    c.strokeStyle = col; c.fillStyle = col; c.lineCap = "round"; c.lineJoin = "round";
    const leg = s * .25, lean = pose.lean || 0, torso = s * .3;
    const hx = 0, hy = (pose.hipY ?? -s * .5) - (pose.bob || 0) * s;
    const nx = hx + Math.sin(lean) * torso, ny = hy - Math.cos(lean) * torso;
    c.lineWidth = s * .085;
    for (const [a1, a2] of pose.legs) limb(c, hx, hy, a1, leg, a2, leg);
    c.lineWidth = s * .115; c.beginPath(); c.moveTo(hx, hy); c.lineTo(nx, ny); c.stroke();
    if (o.dress) { // a skirt that flutters
      const f = Math.sin((o.t || 0) * 8) * s * .025;
      c.fillStyle = o.dress; c.beginPath(); c.moveTo(hx - s * .05, hy - s * .1); c.lineTo(hx + s * .06, hy - s * .1);
      c.lineTo(hx + s * .13 + f, hy + s * .16); c.quadraticCurveTo(hx - s * .04, hy + s * .21 + f, hx - s * .22 - f * 2, hy + s * .14 + f); c.closePath(); c.fill(); c.fillStyle = col;
    }
    const sx = hx + Math.sin(lean) * torso * .86, sy = hy - Math.cos(lean) * torso * .86;
    c.lineWidth = s * .068;
    for (const [a1, a2] of pose.arms) limb(c, sx, sy, a1, s * .16, a2, s * .15);
    const hr = s * (o.hair ? .092 : .078), hcx = nx + Math.sin(lean) * hr * 1.2, hcy = ny - Math.cos(lean) * hr * 1.2;
    if (o.hair) { // long hair, streaming behind
      for (let i = 0; i < 8; i++) ribbon(c, hcx - hr * .2, hcy - hr * .7 + i * hr * .26, s * (.24 + i * .03), hr * .55, (o.t || 0) + i * .13, col, .25 + i * .06, 6);
    }
    disc(c, hcx, hcy, hr, col);
    if (o.scarf) ribbon(c, nx - s * .01, ny + s * .02, s * .38, s * .06, o.t || 0, o.scarf, .05);
    c.restore();
  }
  const pose = {
    run(ph, amp = 1) {
      const L = (q) => { const th = .85 * amp * Math.sin(q); return [th, th - (.2 + 1.45 * amp * Math.max(0, Math.cos(q)))]; };
      const A = (q) => { const u = -.95 * amp * Math.sin(q); return [u, u + 1.5 * amp]; };
      return { lean: .26 * amp, bob: Math.abs(Math.sin(ph)) * .045 * amp, legs: [L(ph + Math.PI), L(ph)], arms: [A(ph), A(ph + Math.PI)] };
    },
    walk(ph) {
      const L = (q) => { const th = .38 * Math.sin(q); return [th, th - .3 * Math.max(0, Math.cos(q))]; };
      const A = (q) => { const u = -.3 * Math.sin(q); return [u, u + .25]; };
      return { lean: .04, bob: Math.abs(Math.sin(ph)) * .015, legs: [L(ph + Math.PI), L(ph)], arms: [A(ph), A(ph + Math.PI)] };
    },
    stand(t, k = 0) {
      const b = Math.sin(t * 1.6 + k) * .03;
      return { lean: .02 + b * .3, bob: Math.sin(t * 1.6 + k) * .006, legs: [[.09, .06], [-.08, -.05]], arms: [[.12 + b, .08 + b], [-.14 - b, -.05]] };
    },
    point(t) { const p = pose.stand(t, 1); p.arms[0] = [2.55 + Math.sin(t * 2) * .05, 2.7]; return p; },
    sit(t, k = 0) {
      const sw = Math.sin(t * 2.2 + k) * .25;
      return { lean: -.06, hipY: -0.0, legs: [[Math.PI / 2, .15 + sw], [Math.PI / 2 - .1, .05 - sw * .8]], arms: [[-.55, -.35], [-.45, -.25]] };
    },
    jump(u) {
      const tuck = Math.sin(clamp(u, 0, 1) * Math.PI);
      return { lean: .32, legs: [[.6 + .9 * tuck, .2 - .3 * tuck], [-.45 - .2 * tuck, -1.2 - .6 * tuck]], arms: [[2.3 + .4 * tuck, 2.6 + .4 * tuck], [1.9 + .3 * tuck, 2.2]] };
    },
  };

  // ---------------------------------------------------------------- the film: "the night we jumped"
  const ROOF = "#07030d";
  const shots = [
    // 0. dusk on the roof
    function dusk(c, w, h, t) {
      const z = 1 + t * .012; c.save(); c.translate(w * .55, h * .6); c.scale(z, z); c.translate(-w * .55, -h * .6);
      sky(c, w, h, [[0, "#1a1140"], [.42, "#5e2a72"], [.7, "#ff6b6b"], [.88, "#ffb36b"]]);
      const sy = h * (.58 + t * .006);
      glow(c, w * .42, sy, h * .7, "#ffcf8a", .55); disc(c, w * .42, sy, h * .085, "#fff1cf");
      clouds(c, w, h, t, 7, "#ff9aa8", .35, .22, .52);
      birds(c, w, h, t, 4, 5, "#2a1430");
      skyline(c, w, h, city(3, .1, .32), t * .012, h * .86, "#3b1c50", "#ffb36b", .1, t);
      skyline(c, w, h, city(7, .06, .2), t * .03 + .7, h * .93, "#22102f", "#ffd27a", .2, t);
      const edge = w * .6;
      c.fillStyle = ROOF; c.fillRect(edge, h * .82, w, h); c.fillRect(edge - h * .012, h * .815, w, h * .018);
      person(c, edge + h * .04, h * .815, h * .32, pose.sit(t), ROOF, { dir: -1 });
      person(c, edge + h * .26, h * .815, h * .36, pose.stand(t), ROOF, { dir: -1, scarf: "#ff4f8b", t });
      c.restore();
    },
    // 1. running the rooftops
    function run(c, w, h, t) {
      sky(c, w, h, [[0, "#0d0a26"], [.5, "#3d1766"], [.86, "#ff4f8b"]]);
      stars(c, w, h, 60, 11, t, .45);
      glow(c, w * .8, h * .2, h * .3, "#ffd6f0", .35); disc(c, w * .8, h * .2, h * .055, "#ffe9f5");
      skyline(c, w, h, city(21, .14, .42), t * .05, h * .82, "#2a1250", "#ff8ad8", .1, t);
      skyline(c, w, h, city(22, .1, .3), t * .16, h * .9, "#170a2c", "#6ff3ff", .14, t);
      streaks(c, w, h, t, .9, .1);
      const g = h * .8; c.fillStyle = ROOF; c.fillRect(0, g, w, h - g);
      props(c, w, h, t * .8, g);
      person(c, w * .4, g, h * .32, pose.run(t * 12), ROOF, { scarf: "#ff4f8b", t });
    },
    // 2. on the edge, the city far below
    function edge(c, w, h, t) {
      const z = 1 + t * .02; c.save(); c.translate(w * .35, h * .6); c.scale(z, z); c.translate(-w * .35, -h * .6);
      sky(c, w, h, [[0, "#050818"], [.55, "#1b2a5c"], [1, "#40306e"]]);
      stars(c, w, h, 90, 17, t, .55);
      glow(c, w * .7, h * .34, h * .75, "#cfd9ff", .4); disc(c, w * .7, h * .34, h * .16, "#eef1ff");
      c.fillStyle = "rgba(120,130,180,.12)"; for (const [dx, dy, r] of [[-.05, -.03, .035], [.04, .04, .025], [.02, -.06, .015], [-.02, .07, .02]]) disc(c, w * .7 + dx * h, h * .34 + dy * h, r * h, c.fillStyle);
      const r = rng(31), cols = ["#ffb36b", "#ff4f8b", "#6aa8ff", "#ffd27a", "#7af0e0"];
      c.globalCompositeOperation = "lighter";
      for (let i = 0; i < 70; i++) {
        const x = r() * w, y = h * (.6 + r() * .42) + Math.sin(t * .5 + i) * h * .006, rad = h * (.012 + r() * .04);
        glow(c, x, y, rad, cols[i % 5], .25 + .3 * Math.sin(t * .8 + i) ** 2);
      }
      c.globalCompositeOperation = "source-over";
      c.fillStyle = "#04030a"; c.fillRect(0, h * .78, w * .44, h);
      person(c, w * .33, h * .78, h * .42, pose.stand(t), "#04030a", { scarf: "#ff4f8b", t });
      c.restore();
    },
    // 3. the last train over the river
    function train(c, w, h, t) {
      const hz = h * .6;
      sky(c, w, h, [[0, "#03101a"], [.6, "#0c3a4a"], [1, "#1f6f6a"]], 0, hz);
      stars(c, w, h, 50, 23, t, .45);
      glow(c, w * .2, h * .18, h * .25, "#bff6ee", .25); disc(c, w * .2, h * .18, h * .04, "#e8fffb");
      ridge(c, w, h, hz, 3, "#082b36", .16, t * .004);
      sky(c, w, h, [[0, "#0a2f3a"], [1, "#020a10"]], hz, h);
      const deck = hz - h * .09;
      c.fillStyle = "#02090d"; c.fillRect(0, deck, w, h * .022);
      c.strokeStyle = "#02090d"; c.lineWidth = h * .012;
      for (let x = -h * .1; x < w + h * .5; x += h * .5) { c.fillRect(x, deck, h * .03, hz - deck); c.beginPath(); c.arc(x + h * .265, hz, h * .235, Math.PI, 0); c.stroke(); }
      const L = w * 1.7, x0 = w * .85 - ((t * h * .55) % (L + w * 1.6)), cars = 6, cw = L / cars, ty = deck - h * .065;
      const wins = [];
      for (let k = 0; k < cars; k++) {
        const cx = x0 + k * cw; if (cx > w || cx + cw < 0) continue;
        c.fillStyle = "#03141a"; c.beginPath(); c.roundRect(cx + h * .006, ty, cw - h * .012, h * .06, k === 0 ? [h * .03, h * .006, h * .006, h * .006] : h * .006); c.fill();
        for (let j = 0; j < 7; j++) wins.push([cx + h * .03 + j * (cw - h * .05) / 7, ty + h * .014]);
      }
      c.globalCompositeOperation = "lighter";
      for (const [wx, wy] of wins) {
        c.fillStyle = "#ffd88a"; c.fillRect(wx, wy, h * .035, h * .022); glow(c, wx + h * .017, wy + h * .01, h * .06, "#ffb35a", .3);
        const ry = hz + (hz - wy) * .9, wob = Math.sin(t * 3 + wx * .05) * h * .006;
        c.fillStyle = "rgba(255,200,120,.13)"; c.fillRect(wx + wob, ry, h * .03, h * .012); c.fillRect(wx - wob, ry + h * .02, h * .022, h * .008);
      }
      c.globalCompositeOperation = "source-over";
      c.fillStyle = "rgba(160,230,220,.05)"; for (let i = 0; i < 5; i++) c.fillRect(0, hz + h * (.03 + i * .07) + Math.sin(t + i) * h * .005, w, h * .004);
    },
    // 4. the jump (lands on the drop)
    function jump(c, w, h, t) {
      sky(c, w, h, [[0, "#12052a"], [.5, "#4a1260"], [1, "#ff4f8b"]]);
      stars(c, w, h, 70, 41, t, .5);
      glow(c, w * .5, h * .46, h * 1, "#ffe2b8", .55); disc(c, w * .5, h * .46, h * .3, "#fff3dc");
      c.fillStyle = "rgba(255,200,170,.18)"; disc(c, w * .44, h * .4, h * .06, c.fillStyle); disc(c, w * .58, h * .55, h * .04, c.fillStyle);
      skyline(c, w, h, city(41, .05, .16), t * .01, h * .97, "#2a0c3a", "#ffb0d8", .15, t);
      c.fillStyle = ROOF; c.fillRect(0, h * .74, w * .27, h); c.fillRect(w * .73, h * .7, w * .3, h);
      const at = (u) => [lerp(w * .2, w * .8, u), lerp(h * .74, h * .7, u) - Math.sin(u * Math.PI) * h * .42];
      const u = (t * .3 + .24) % 1;
      for (let k = 6; k > 0; k--) { const uu = u - k * .03; if (uu < 0) continue; const [gx, gy] = at(uu); c.globalAlpha = .1 + .05 * (6 - k); person(c, gx, gy, h * .26, pose.jump(uu), "#ff4f8b"); }
      c.globalAlpha = 1;
      c.globalCompositeOperation = "lighter";
      for (let i = 0; i < 26; i++) { const uu = u - hash(i) * .25; if (uu < 0) continue; const [px, py] = at(uu); glow(c, px + (hash(i + 4) - .5) * h * .12, py - h * .12 + (hash(i + 7) - .5) * h * .12, h * .02, "#ffd6a0", .7 * (1 - (u - uu) * 4)); }
      c.globalCompositeOperation = "source-over";
      const [x, y] = at(u); person(c, x, y, h * .26, pose.jump(u), ROOF, { scarf: "#ff4f8b", t });
    },
    // 5. fireworks over the city
    function fireworks(c, w, h, t) {
      sky(c, w, h, [[0, "#07041a"], [.7, "#25103f"], [1, "#3a1450"]]);
      stars(c, w, h, 40, 51, t, .5);
      const pal = ["#ff4f8b", "#ffd166", "#7a5cff", "#4fe0d0", "#ffa24a"], gap = .38, life = 2.4;
      c.globalCompositeOperation = "lighter"; c.lineCap = "round";
      for (let b = Math.floor(t / gap) - 7; b <= Math.floor(t / gap); b++) {
        const age = t - b * gap; if (age < 0 || age > life) continue;
        const x = w * (.12 + hash(b * 3.1) * .76), y = h * (.12 + hash(b * 7.3) * .32), col = pal[((b % 5) + 5) % 5], sp = h * (.18 + hash(b + .5) * .14);
        if (age < .18) glow(c, x, y, h * .25 * (1 - age / .18), col, .5);
        const pos = (a, v, tt) => [x + Math.cos(a) * v * (1 - Math.exp(-3 * tt)), y + Math.sin(a) * v * (1 - Math.exp(-3 * tt)) + h * .07 * tt * tt];
        c.strokeStyle = col; c.lineWidth = Math.max(1, h * .006); c.globalAlpha = Math.pow(1 - age / life, 1.4);
        for (let i = 0; i < 46; i++) {
          const a = i / 46 * TAU + hash(b + i * .37) * .12, v = sp * (.75 + .25 * hash(i * 1.7 + b));
          const [x1, y1] = pos(a, v, Math.max(0, age - .09)), [x2, y2] = pos(a, v, age);
          c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
        }
        c.globalAlpha = 1;
      }
      c.globalCompositeOperation = "source-over";
      skyline(c, w, h, city(51, .1, .3), .3, h * .9, "#0b0516", "#ffcf7a", .14, t);
      c.fillStyle = ROOF; c.fillRect(0, h * .86, w * .38, h);
      person(c, w * .14, h * .86, h * .3, pose.point(t), ROOF);
      person(c, w * .25, h * .86, h * .32, pose.stand(t, 2), ROOF, { scarf: "#ff4f8b", t });
    },
    // 6. under the aurora
    function aurora(c, w, h, t) {
      sky(c, w, h, [[0, "#01050f"], [.6, "#0a1f35"], [1, "#14324a"]]);
      stars(c, w, h, 130, 61, t, .7);
      c.globalCompositeOperation = "lighter";
      const cols = ["#3dffb0", "#4fe0d0", "#9b6bff"], step = w / 110;
      for (let k = 0; k < 3; k++) for (let x = 0; x < w; x += step) {
        const u = x / w, y0 = h * (.14 + k * .07) + Math.sin(u * 4 + t * .4 + k) * h * .06 + Math.sin(u * 9 - t * .7) * h * .02;
        const len = h * (.16 + .08 * Math.sin(u * 7 + t * .9 + k * 2)), g = c.createLinearGradient(0, y0, 0, y0 + len);
        g.addColorStop(0, rgba(cols[k], 0)); g.addColorStop(.6, rgba(cols[k], .32)); g.addColorStop(1, rgba(cols[k], 0));
        c.fillStyle = g; c.fillRect(x, y0, step + 1, len);
      }
      c.globalCompositeOperation = "source-over";
      ridge(c, w, h, h * .8, 9, "#0a1a2c", .22, t * .003);
      ridge(c, w, h, h * .92, 4, "#030a14", .12, t * .006);
      c.fillStyle = "#030a14"; c.beginPath(); c.ellipse(w * .74, h * .86, h * .2, h * .08, 0, Math.PI, TAU); c.fill();
      person(c, w * .7, h * .79, h * .14, pose.sit(t, 1), "#030a14", { dir: -1 });
      person(c, w * .76, h * .79, h * .15, { ...pose.sit(t, 2), arms: [[2.6, 2.75], [-.45, -.25]] }, "#030a14", { dir: -1 });
    },
    // 7. walking home at sunrise
    function sunrise(c, w, h, t) {
      sky(c, w, h, [[0, "#2d2b6e"], [.45, "#c45a8a"], [.72, "#ff9a6b"], [.86, "#ffd48a"]]);
      const sy = h * (.8 - t * .01);
      glow(c, w * .3, sy, h * .8, "#ffd59a", .6); disc(c, w * .3, sy, h * .1, "#fff4d8");
      clouds(c, w, h, t, 77, "#ffc2a8", .55, .2, .55);
      birds(c, w, h, t, 5, 8, "#4a1f52");
      ridge(c, w, h, h * .8, 2, "#7a3a6e", .14, t * .004);
      ridge(c, w, h, h * .88, 6, "#4a1f52", .1, t * .008);
      c.fillStyle = "#1d0b26"; c.beginPath(); c.moveTo(0, h); c.lineTo(0, h * .87); c.quadraticCurveTo(w * .5, h * .83, w, h * .87); c.lineTo(w, h); c.fill();
      const x = w * (.4 + ((t * .025) % .4));
      person(c, x, h * .855, h * .2, pose.walk(t * 5), "#1d0b26", { scarf: "#ff4f8b", t });
      person(c, x + h * .1, h * .856, h * .21, pose.walk(t * 5 + 2.4), "#1d0b26");
    },
  ];
  function props(c, w, h, off, g) { // vents, antennas and tanks going by on the roof
    const L = h * 2.4, o = (((off * h) % L) + L) % L; c.fillStyle = ROOF;
    const items = [[.1, "vent"], [.55, "ant"], [.92, "tank"], [1.5, "ac"], [1.9, "ant"], [2.15, "vent"]];
    for (let rep = 0; rep * L - o < w + L; rep++) for (const [px, k] of items) {
      const x = px * h + rep * L - o; if (x < -h * .3 || x > w + h * .3) continue;
      if (k === "vent") { c.fillRect(x, g - h * .06, h * .05, h * .06); c.fillRect(x - h * .01, g - h * .075, h * .07, h * .018); }
      if (k === "ant") { c.fillRect(x, g - h * .24, h * .006, h * .24); c.fillRect(x - h * .03, g - h * .22, h * .066, h * .005); c.fillRect(x - h * .02, g - h * .17, h * .046, h * .005); }
      if (k === "tank") { c.fillRect(x, g - h * .22, h * .12, h * .13); c.beginPath(); c.moveTo(x - h * .01, g - h * .22); c.lineTo(x + h * .06, g - h * .27); c.lineTo(x + h * .13, g - h * .22); c.fill(); c.fillRect(x + h * .01, g - h * .1, h * .01, h * .1); c.fillRect(x + h * .1, g - h * .1, h * .01, h * .1); }
      if (k === "ac") c.fillRect(x, g - h * .05, h * .09, h * .05);
    }
  }
  function vignette(c, w, h, a = .5) {
    const g = c.createRadialGradient(w / 2, h / 2, h * .3, w / 2, h / 2, Math.hypot(w, h) * .6);
    g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, `rgba(0,0,0,${a})`); c.fillStyle = g; c.fillRect(0, 0, w, h);
  }
  function draw(c, w, h, k, t) {
    c.save(); shots[((k % shots.length) + shots.length) % shots.length](c, w, h, t); c.restore();
    vignette(c, w, h);
  }

  // ---------------------------------------------------------------- engine scenes
  let grain;
  function paper() { // a painted-paper grain, made once
    if (grain) return grain;
    const g = document.createElement("canvas"); g.width = g.height = 160; const x = g.getContext("2d"), d = x.createImageData(160, 160), r = rng(9);
    for (let i = 0; i < d.data.length; i += 4) { const v = 120 + r() * 135; d.data[i] = d.data[i + 1] = d.data[i + 2] = v; d.data[i + 3] = 255; }
    x.putImageData(d, 0, 0); return (grain = g);
  }
  function puff(c, x, y, s, col, shade) {
    const blobs = [[0, 0, 1], [-.9, .25, .7], [.9, .25, .75], [-.4, -.35, .75], [.45, -.3, .7], [1.6, .4, .5], [-1.6, .45, .45]];
    if (shade) { c.fillStyle = shade; for (const [dx, dy, r] of blobs) disc(c, x + dx * s, y + dy * s + s * .2, r * s, shade); }
    c.fillStyle = col; for (const [dx, dy, r] of blobs) disc(c, x + dx * s, y + dy * s, r * s, col);
  }

  // the cartoon engine: a painted meadow, a cat, a butterfly it can't stop watching
  function cartoon(c, w, h, t) {
    sky(c, w, h, [[0, "#ffd9a8"], [.55, "#ffb8c6"], [1, "#ff9fb4"]]);
    glow(c, w * .82, h * .22, h * .7, "#fff2c2", .7); disc(c, w * .82, h * .22, h * .1, "#fff8de");
    puff(c, ((w * .2 + t * h * .02) % (w + h)) - h * .3, h * .2, h * .07, "#fff6ee", "#ffd0cf");
    puff(c, ((w * .7 + t * h * .012) % (w + h)) - h * .3, h * .36, h * .05, "#fff2ea", "#ffc9cc");
    c.fillStyle = "#a7d9a4"; c.beginPath(); c.moveTo(0, h); c.lineTo(0, h * .62); c.bezierCurveTo(w * .3, h * .5, w * .55, h * .66, w, h * .56); c.lineTo(w, h); c.fill();
    const hill = c.createLinearGradient(0, h * .66, 0, h); hill.addColorStop(0, "#74c27f"); hill.addColorStop(1, "#3f8f5c");
    c.fillStyle = hill; c.beginPath(); c.moveTo(0, h); c.lineTo(0, h * .72); c.bezierCurveTo(w * .35, h * .64, w * .65, h * .74, w, h * .68); c.lineTo(w, h); c.fill();
    // the tree, its leaves breathing in the wind
    const tx = w * .12, sway = Math.sin(t * 1.3) * h * .012;
    c.fillStyle = "#7a4a3a"; c.beginPath(); c.moveTo(tx - h * .03, h * .78); c.quadraticCurveTo(tx, h * .5, tx + sway * .5 - h * .015, h * .32); c.lineTo(tx + sway * .5 + h * .015, h * .32); c.quadraticCurveTo(tx + h * .02, h * .5, tx + h * .04, h * .78); c.fill();
    for (const [dx, dy, r, col] of [[-.12, -.02, .13, "#3c7f50"], [.1, 0, .12, "#3c7f50"], [0, -.12, .15, "#4f9f63"], [-.1, -.17, .1, "#62b874"], [.12, -.15, .1, "#4f9f63"], [.02, -.24, .1, "#62b874"], [-.03, -.06, .1, "#62b874"]])
      disc(c, tx + dx * h + sway * (1 - dy * 4), h * .3 + dy * h, r * h, col);
    // the cat
    const cx = w * .58, gy = h * .86, bx = cx + Math.cos(t * .8) * w * .2, by = h * .32 + Math.sin(t * 1.6) * h * .09;
    const breath = 1 + .02 * Math.sin(t * 2.4), look = clamp((bx - cx) / (w * .25), -1, 1), lookY = clamp((by - h * .42) / (h * .2), -1, 1);
    c.lineCap = "round"; c.strokeStyle = "#f0893a"; c.lineWidth = h * .045;
    const sw = Math.sin(t * 2.1);
    c.beginPath(); c.moveTo(cx + h * .1, gy - h * .04); c.bezierCurveTo(cx + h * .3, gy - h * .02, cx + h * .26 + sw * h * .05, gy - h * .25, cx + h * .2 + sw * h * .1, gy - h * .3); c.stroke();
    c.fillStyle = "#0000001a"; c.beginPath(); c.ellipse(cx, gy + h * .01, h * .17, h * .03, 0, 0, TAU); c.fill();
    c.save(); c.translate(cx, gy); c.scale(1, breath);
    c.fillStyle = "#ff9d4d"; c.beginPath(); c.ellipse(0, -h * .13, h * .13, h * .15, 0, 0, TAU); c.fill();
    c.fillStyle = "#ffd6a6"; c.beginPath(); c.ellipse(0, -h * .1, h * .075, h * .1, 0, 0, TAU); c.fill();
    c.strokeStyle = "#e07a2e"; c.lineWidth = h * .014;
    for (const k of [-1, 1]) for (let i = 0; i < 2; i++) { c.beginPath(); c.arc(k * h * .1, -h * (.16 - i * .05), h * .03, k > 0 ? Math.PI * .7 : -Math.PI * .3, k > 0 ? Math.PI * 1.3 : Math.PI * .3); c.stroke(); }
    c.fillStyle = "#ffb070"; c.beginPath(); c.ellipse(-h * .045, -h * .01, h * .035, h * .022, 0, 0, TAU); c.fill(); c.beginPath(); c.ellipse(h * .045, -h * .01, h * .035, h * .022, 0, 0, TAU); c.fill();
    c.restore();
    const hx = cx + look * h * .012, hy = gy - h * .33 * breath + lookY * h * .008;
    c.save(); c.translate(hx, hy); c.rotate(look * .12 + Math.sin(t * .9) * .03);
    const R = h * .11;
    for (const k of [-1, 1]) {
      const tw = (k === 1 && (t % 4) < .25) ? Math.sin((t % 4) * 25) * .15 : 0;
      c.save(); c.rotate(tw);
      c.fillStyle = "#ff9d4d"; c.beginPath(); c.moveTo(k * R * .35, -R * .7); c.lineTo(k * R * .95, -R * 1.3); c.lineTo(k * R * 1, -R * .25); c.fill();
      c.fillStyle = "#ffb3c1"; c.beginPath(); c.moveTo(k * R * .5, -R * .65); c.lineTo(k * R * .9, -R * 1.1); c.lineTo(k * R * .9, -R * .4); c.fill();
      c.restore();
    }
    c.fillStyle = "#ff9d4d"; c.beginPath(); c.ellipse(0, 0, R * 1.08, R, 0, 0, TAU); c.fill();
    c.fillStyle = "#ffd6a6"; c.beginPath(); c.ellipse(0, R * .35, R * .55, R * .42, 0, 0, TAU); c.fill();
    c.fillStyle = "#ff7a8a55"; disc(c, -R * .62, R * .3, R * .18, c.fillStyle); disc(c, R * .62, R * .3, R * .18, c.fillStyle);
    const bl = t % 3.4, open = bl < .14 ? Math.abs(bl - .07) / .07 : 1;
    for (const k of [-1, 1]) {
      c.fillStyle = "#2b1a2a"; c.beginPath(); c.ellipse(k * R * .4, -R * .05, R * .17, R * .23 * Math.max(.08, open), 0, 0, TAU); c.fill();
      if (open > .5) { c.fillStyle = "#fff"; disc(c, k * R * .4 + look * R * .07 - R * .05, -R * .13 + lookY * R * .06, R * .065, "#fff"); }
    }
    c.fillStyle = "#ff6f8f"; c.beginPath(); c.moveTo(-R * .1, R * .18); c.lineTo(R * .1, R * .18); c.lineTo(0, R * .3); c.fill();
    c.strokeStyle = "#2b1a2a"; c.lineWidth = R * .05; c.beginPath(); c.moveTo(-R * .16, R * .38); c.quadraticCurveTo(-R * .08, R * .47, 0, R * .32); c.quadraticCurveTo(R * .08, R * .47, R * .16, R * .38); c.stroke();
    c.strokeStyle = "#ffffffaa"; c.lineWidth = R * .025;
    for (const k of [-1, 1]) for (const a of [-.15, .1]) { c.beginPath(); c.moveTo(k * R * .5, R * .3); c.lineTo(k * R * 1.35, R * (.25 + a * 1.6)); c.stroke(); }
    c.restore();
    // the butterfly
    const flap = Math.abs(Math.sin(t * 14));
    c.save(); c.translate(bx, by); c.rotate(Math.sin(t * .8) * .3);
    for (const k of [-1, 1]) { c.fillStyle = "#7a5cff"; c.beginPath(); c.ellipse(k * h * .022 * flap, -h * .012, h * .024 * flap + h * .003, h * .02, k * .5, 0, TAU); c.fill(); c.fillStyle = "#ff4f8b"; c.beginPath(); c.ellipse(k * h * .016 * flap, h * .012, h * .014 * flap + h * .002, h * .013, -k * .4, 0, TAU); c.fill(); }
    c.fillStyle = "#2b1a2a"; c.fillRect(-h * .003, -h * .02, h * .006, h * .04);
    c.restore();
    // grass and floating seeds
    for (let i = 0; i < 90; i++) {
      const x = hash(i) * w, y = h * (.8 + hash(i + 2) * .22), l = h * (.03 + hash(i + 4) * .05), b = Math.sin(t * 2 + x * .02) * l * .5;
      c.strokeStyle = i % 3 ? "#3f8f5c" : "#5fb06f"; c.lineWidth = h * .008; c.beginPath(); c.moveTo(x, y); c.quadraticCurveTo(x + b * .3, y - l * .6, x + b, y - l); c.stroke();
    }
    for (let i = 0; i < 14; i++) { const x = (((hash(i) + t * (.02 + hash(i + 1) * .02)) % 1)) * w, y = h * (.2 + hash(i + 3) * .5) + Math.sin(t * 1.5 + i) * h * .03; c.globalAlpha = .7; disc(c, x, y, h * .006, "#fff"); }
    c.globalAlpha = 1;
    c.globalCompositeOperation = "multiply"; c.globalAlpha = .1; c.fillStyle = c.createPattern(paper(), "repeat"); c.fillRect(0, 0, w, h);
    c.globalAlpha = 1; c.globalCompositeOperation = "source-over";
  }

  // the anime heroine: drawn facing right in units of her height, then turned to face the wind
  function girl(c, x, y, s, t, ink) {
    const k = (col) => ink || col, wv = (n) => Math.sin(t * 5 + n), f = .02 + .02 * wv(1), f2 = .025 + .02 * wv(2.3);
    const HAIR = "#2b2456", HAIR2 = "#1d1840", SKIN = "#ffe6d8", SKIN2 = "#f2c4b4", NAVY = "#27306a", NAVY2 = "#1b2150", WHITE = "#f7f8ff", WHITE2 = "#cfd5f2", RED = "#e8344e";
    c.save(); c.translate(x, y); c.scale(-s, s); c.lineJoin = c.lineCap = "round";
    const poly = (col, pts, close = true) => { c.fillStyle = k(col); c.beginPath(); pts.forEach(([a, b], i) => (i ? c.lineTo(a, b) : c.moveTo(a, b))); if (close) c.closePath(); c.fill(); };
    // long hair streaming back
    for (let i = 0; i < 7; i++) ribbon(c, -.05 + i * .004, -.875 + i * .028, .3 + i * .03 + .02 * wv(i), .075 - i * .005, t + i * .17, k(i % 2 ? HAIR2 : HAIR), .12 + i * .06, 6);
    // legs, socks, shoes (far one first)
    for (const [dx, sk] of [[-.035, SKIN2], [0, SKIN]]) {
      poly(sk, [[dx, -.31], [dx + .04, -.31], [dx + .03, -.03], [dx + .008, -.03]]);
      poly(dx ? NAVY2 : NAVY, [[dx + .003, -.17], [dx + .036, -.17], [dx + .03, -.03], [dx + .007, -.03]]);
      c.fillStyle = k("#3a2226"); c.beginPath(); c.ellipse(dx + .028, -.014, .036, .015, 0, 0, Math.PI * 2); c.fill();
    }
    // pleated skirt, lifted by the wind
    const hem = []; for (let i = 0; i <= 8; i++) hem.push([lerp(.105, -.16 - f, i / 8), -.295 + (i % 2 ? .012 : 0) - f * .7 * i / 8]);
    c.fillStyle = k(NAVY); c.beginPath(); c.moveTo(-.05, -.47); c.lineTo(.048, -.47); c.quadraticCurveTo(.08, -.38, .105, -.295);
    hem.forEach(([a, b]) => c.lineTo(a, b)); c.quadraticCurveTo(-.1 - f * .5, -.4, -.05, -.47); c.fill();
    if (!ink) { c.strokeStyle = NAVY2; c.lineWidth = .006; for (let i = 1; i < 8; i += 2) { c.beginPath(); c.moveTo(lerp(.04, -.045, i / 8), -.47); c.lineTo(...hem[i]); c.stroke(); } }
    // blouse
    c.fillStyle = k(WHITE); c.beginPath(); c.moveTo(-.048, -.47); c.lineTo(.046, -.47); c.quadraticCurveTo(.072, -.58, .068, -.63);
    c.quadraticCurveTo(.06, -.7, .03, -.725); c.lineTo(-.04, -.725); c.quadraticCurveTo(-.062, -.6, -.048, -.47); c.fill();
    if (!ink) poly(WHITE2, [[-.048, -.47], [-.01, -.47], [-.02, -.6], [-.03, -.72], [-.04, -.725], [-.06, -.6]]);
    // sailor collar flying back, with its stripe
    poly(NAVY, [[.025, -.735], [-.045, -.735], [-.115 - f2, -.645 + f2 * .3], [-.065 - f2 * .5, -.6], [-.02, -.66]]);
    if (!ink) { c.strokeStyle = WHITE; c.lineWidth = .005; c.beginPath(); c.moveTo(-.04, -.722); c.lineTo(-.1 - f2, -.645 + f2 * .3); c.lineTo(-.064 - f2 * .5, -.613); c.stroke(); }
    c.strokeStyle = k(NAVY); c.lineWidth = .012; c.beginPath(); c.moveTo(.028, -.728); c.lineTo(.06, -.655); c.stroke();
    // arm, sleeve swaying
    const sw = .01 * wv(2);
    c.strokeStyle = k(WHITE2); c.lineWidth = .044; c.beginPath(); c.moveTo(.0, -.7); c.lineTo(-.02 + sw, -.58); c.lineTo(-.006 + sw, -.48); c.stroke();
    c.strokeStyle = k(WHITE); c.lineWidth = .036; c.stroke();
    c.strokeStyle = k(NAVY); c.lineWidth = .04; c.beginPath(); c.moveTo(-.008 + sw, -.49); c.lineTo(-.006 + sw, -.475); c.stroke();
    c.fillStyle = k(SKIN); c.beginPath(); c.arc(-.004 + sw, -.458, .017, 0, Math.PI * 2); c.fill();
    // the red ribbon at her collar, its tails in the wind
    ribbon(c, .058, -.655, .2, .03, t + .4, k(RED), .25, 7);
    poly(RED, [[.058, -.655], [.08, -.675], [.082, -.64]]); poly(RED, [[.058, -.655], [.04, -.672], [.038, -.64]]);
    // neck and head
    poly(SKIN2, [[-.012, -.795], [.02, -.795], [.02, -.72], [-.012, -.72]]);
    c.fillStyle = k(SKIN); c.beginPath(); c.arc(-.005, -.84, .07, 0, Math.PI * 2); c.fill();
    c.beginPath(); c.moveTo(.045, -.895); c.quadraticCurveTo(.07, -.87, .068, -.852); c.lineTo(.081, -.836); c.lineTo(.068, -.828);
    c.quadraticCurveTo(.071, -.817, .064, -.81); c.quadraticCurveTo(.058, -.79, .038, -.784); c.quadraticCurveTo(.0, -.776, -.03, -.8); c.closePath(); c.fill();
    if (!ink) {
      c.fillStyle = "rgba(255,110,140,.45)"; c.beginPath(); c.ellipse(.034, -.814, .015, .006, 0, 0, Math.PI * 2); c.fill();
      c.strokeStyle = "#b5606a"; c.lineWidth = .003; c.beginPath(); c.moveTo(.06, -.812); c.lineTo(.066, -.811); c.stroke();
      const open = (t % 3.6) < .17 ? .12 : 1;
      c.fillStyle = "#fff"; c.beginPath(); c.ellipse(.046, -.84, .013, .02 * open, 0, 0, Math.PI * 2); c.fill();
      c.fillStyle = "#3b2a7a"; c.beginPath(); c.ellipse(.049, -.839, .01, .018 * open, 0, 0, Math.PI * 2); c.fill();
      if (open > .5) {
        c.fillStyle = "#8a6cff"; c.beginPath(); c.ellipse(.049, -.832, .008, .009, 0, 0, Math.PI * 2); c.fill();
        c.fillStyle = "#fff"; c.beginPath(); c.arc(.052, -.848, .0045, 0, Math.PI * 2); c.fill();
      }
      c.strokeStyle = "#1d1840"; c.lineWidth = .006; c.beginPath(); c.moveTo(.03, -.859); c.quadraticCurveTo(.048, -.866, .062, -.857); c.stroke();
      c.lineWidth = .003; c.beginPath(); c.moveTo(.036, -.877); c.lineTo(.06, -.874); c.stroke();
    }
    // hair cap, fringe and a side lock
    c.fillStyle = k(HAIR); c.beginPath(); c.moveTo(-.076, -.79); c.quadraticCurveTo(-.092, -.9, -.01, -.918); c.quadraticCurveTo(.062, -.916, .08, -.866);
    for (const [a, b] of [[.064, -.864], [.072, -.846], [.052, -.86], [.055, -.84], [.036, -.862]]) c.lineTo(a, b);
    c.quadraticCurveTo(.012, -.852, -.002, -.822); c.lineTo(-.018, -.758); c.lineTo(-.034, -.8); c.closePath(); c.fill();
    ribbon(c, .0, -.83, .13, .022, t + 1, k(HAIR2), .2, 6);
    if (!ink) { c.strokeStyle = "#5b4ea0"; c.lineWidth = .009; c.beginPath(); c.arc(-.01, -.85, .056, Math.PI * 1.12, Math.PI * 1.55); c.stroke(); }
    c.restore();
  }

  // the anime engine: a hilltop in the wind, animated on twos, with an impact frame on the beat
  function anime(c, w, h, t) {
    const tc = Math.floor(t * 12) / 12, cyc = t % 3.6, impact = cyc > 3.0 && cyc < 3.16;
    sky(c, w, h, [[0, "#2a6fd6"], [.6, "#7fc4ff"], [1, "#d8f0ff"]]);
    glow(c, w * .85, h * .1, h * .9, "#ffffff", .55);
    for (const [k, r] of [[.3, .03], [.5, .018], [.7, .045]]) { c.fillStyle = "rgba(255,255,255,.18)"; disc(c, lerp(w * .85, w * .3, k), lerp(h * .1, h * .7, k), r * h, c.fillStyle); }
    const drift = t * h * .008;
    puff(c, w * .25 - drift, h * .45, h * .12, "#ffffff", "#a9c8f0");
    puff(c, w * .62 - drift * .6, h * .55, h * .09, "#ffffff", "#b6d3f5");
    ridge(c, w, h, h * .8, 5, "#5d8fcf", .14);
    c.fillStyle = "#2a7d52"; c.beginPath(); c.ellipse(w * .62, h * 1.25, w * .7, h * .5, 0, 0, TAU); c.fill();
    c.fillStyle = "#3aa36b"; c.beginPath(); c.ellipse(w * .6, h * 1.27, w * .66, h * .5, 0, 0, TAU); c.fill();
    for (let i = 0; i < 40; i++) {
      const x = w * (.05 + hash(i) * .95), y = h * .78 + Math.abs(x - w * .6) / w * h * .5 + hash(i + 1) * h * .1, l = h * (.03 + hash(i + 5) * .03), b = Math.sin(tc * 3 + i) * l * .6 + l * .4;
      c.strokeStyle = "#2a7d52"; c.lineWidth = h * .008; c.beginPath(); c.moveTo(x, y); c.quadraticCurveTo(x + b * .3, y - l * .6, x + b, y - l); c.stroke();
    }
    const gx = w * .6, gy = h * .78;
    girl(c, gx, gy, h * .62, tc);
    c.fillStyle = "#ffb7d0";
    for (let i = 0; i < 34; i++) {
      const sp = .12 + hash(i) * .1, x = (((hash(i + 2) + t * sp) % 1.1) - .05) * w, y = h * (hash(i + 3) * .9) + Math.sin(t * 2 + i) * h * .04, r = h * (.007 + hash(i + 6) * .007);
      c.save(); c.translate(x, y); c.rotate(t * 3 + i); c.beginPath(); c.ellipse(0, 0, r * 1.6, r, 0, 0, TAU); c.fill(); c.restore();
    }
    if (impact) { // the held hit: ink and paper, speed lines from her
      c.fillStyle = "#0b0b14"; c.fillRect(0, 0, w, h);
      c.strokeStyle = "#f4f1ea"; c.lineCap = "round";
      for (let i = 0; i < 70; i++) { const a = hash(i) * TAU, r0 = h * (.34 + hash(i + 1) * .2), cy = gy - h * .36; c.lineWidth = h * (.003 + hash(i + 2) * .012); c.beginPath(); c.moveTo(gx + Math.cos(a) * r0, cy + Math.sin(a) * r0); c.lineTo(gx + Math.cos(a) * w, cy + Math.sin(a) * w); c.stroke(); }
      window.Sound?.impact?.();
      girl(c, gx, gy, h * .62, tc, "#f4f1ea");
    } else if (cyc > 2.4 && cyc <= 3.0) { // the build: speed lines rush in
      c.strokeStyle = `rgba(255,255,255,${(cyc - 2.4) * .9})`; c.lineWidth = Math.max(1, h * .004);
      for (let i = 0; i < 26; i++) { const y = hash(i) * h, x = (((hash(i + 1) - t * 2.5) % 1) + 1) % 1 * w; c.beginPath(); c.moveTo(x, y); c.lineTo(x + w * .3, y); c.stroke(); }
    }
  }

  // the rig engine: a Blender viewport, a clay character waving, its bones and keys showing
  function rig(c, w, h, t) {
    sky(c, w, h, [[0, "#4a4a4f"], [1, "#2a2a2d"]]);
    const hz = h * .38, f = h * .9, cx0 = w * .5;
    const P = (X, Z) => [cx0 + X * f / Z, hz + f * .6 / Z];
    c.lineWidth = 1; c.strokeStyle = "rgba(255,255,255,.07)";
    for (let X = -8; X <= 8; X++) { const [a, b] = P(X * .5, 1.1), [d, e] = P(X * .5, 14); c.beginPath(); c.moveTo(a, b); c.lineTo(d, e); c.stroke(); }
    for (let Z = 1.1; Z < 14; Z *= 1.18) { const [a, b] = P(-6, Z), [d, e] = P(6, Z); c.beginPath(); c.moveTo(a, b); c.lineTo(d, e); c.stroke(); }
    const [ox, oy] = P(0, 2.2);
    c.strokeStyle = "rgba(201,71,74,.7)"; c.beginPath(); c.moveTo(0, oy); c.lineTo(w, oy); c.stroke();
    c.strokeStyle = "rgba(122,165,58,.7)"; c.beginPath(); c.moveTo(ox, h); c.lineTo(cx0, hz); c.stroke();
    // the character, posed by its bones
    const s = h * .5, px = ox + Math.sin(t * 1.6) * s * .02, py = oy - s * .45 + Math.abs(Math.sin(t * 3.2)) * -s * .015;
    c.fillStyle = "rgba(0,0,0,.28)"; c.beginPath(); c.ellipse(ox, oy, s * .2, s * .035, 0, 0, TAU); c.fill();
    const lean = Math.sin(t * 1.6) * .06, chest = [px + Math.sin(lean) * s * .26, py - Math.cos(lean) * s * .26];
    const head = [chest[0] + Math.sin(lean * 1.5 + Math.sin(t * 2) * .1) * s * .17, chest[1] - Math.cos(lean) * s * .17];
    const B = [];
    const fk = (x, y, a, l) => [x + Math.sin(a) * l, y + Math.cos(a) * l];
    const legs = [[-1, .04 + Math.sin(t * 1.6) * .04], [1, -.04 + Math.sin(t * 1.6) * .04]];
    for (const [k, a] of legs) { const hp = [px + k * s * .08, py], kn = fk(...hp, a, s * .22), an = fk(...kn, a * .5, s * .23); B.push([hp, kn, k < 0 ? "thigh.L" : "thigh.R"], [kn, an, k < 0 ? "shin.L" : "shin.R"]); }
    const shL = [chest[0] - s * .15, chest[1] + s * .03], shR = [chest[0] + s * .15, chest[1] + s * .03];
    const ua = -.18 + Math.sin(t * 1.6) * .05, elL = fk(...shL, ua, s * .17), haL = fk(...elL, ua - .1, s * .16);
    const wave = Math.sin(t * 7), ra = 2.35 + Math.sin(t * 1.4) * .08, elR = fk(...shR, ra, s * .17), haR = fk(...elR, ra + .55 + wave * .45, s * .16);
    B.push([shL, elL, "upper_arm.L"], [elL, haL, "forearm.L"], [shR, elR, "upper_arm.R"], [elR, haR, "forearm.R"]);
    const clay = (x1, y1, x2, y2, r) => {
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, g = c.createRadialGradient(mx - r * 1.2, my - r * 1.4, r * .2, mx, my, Math.hypot(x2 - x1, y2 - y1) / 2 + r * 1.5);
      g.addColorStop(0, "#f2f2f2"); g.addColorStop(.55, "#b9b9bd"); g.addColorStop(1, "#6f6f76");
      c.strokeStyle = g; c.lineWidth = r * 2; c.lineCap = "round"; c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
    };
    for (const [a, b] of B.slice(0, 4)) clay(...a, ...b, s * .062);
    clay(px, py, ...chest, s * .13);
    clay(shL[0], shL[1], shR[0], shR[1], s * .07);
    for (const [a, b] of B.slice(4)) clay(...a, ...b, s * .048);
    const hg = c.createRadialGradient(head[0] - s * .04, head[1] - s * .05, s * .01, head[0], head[1], s * .1);
    hg.addColorStop(0, "#f6f6f6"); hg.addColorStop(.6, "#bdbdc2"); hg.addColorStop(1, "#77777e");
    c.fillStyle = hg; c.beginPath(); c.arc(head[0], head[1], s * .11, 0, TAU); c.fill();
    const bl = (t % 3) < .12 ? .15 : 1;
    c.fillStyle = "#2b2b30"; for (const k of [-1, 1]) { c.beginPath(); c.ellipse(head[0] + k * s * .03, head[1] - s * .005, s * .011, s * .016 * bl, 0, 0, TAU); c.fill(); }
    B.push([[px, py], chest, "spine"], [chest, head, "neck"]);
    // the bones, drawn the way Blender does: octahedral, the active one in orange
    for (const [[x1, y1], [x2, y2], name] of B) {
      const dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy), nx = -dy / L, ny = dx / L, mx = x1 + dx * .18, my = y1 + dy * .18, wd = L * .1;
      const active = name === "forearm.R", sel = name === "upper_arm.R";
      c.beginPath(); c.moveTo(x1, y1); c.lineTo(mx + nx * wd, my + ny * wd); c.lineTo(x2, y2); c.lineTo(mx - nx * wd, my - ny * wd); c.closePath();
      c.fillStyle = active ? "rgba(255,163,26,.4)" : sel ? "rgba(90,160,240,.3)" : "rgba(170,180,200,.12)"; c.fill();
      c.strokeStyle = active ? "#ffa31a" : sel ? "#5aa0f0" : "rgba(17,22,29,.55)"; c.lineWidth = Math.max(1, h * .005); c.stroke();
      c.fillStyle = active ? "#ffa31a" : "#d8dde6"; disc(c, x1, y1, h * .007, c.fillStyle);
    }
    // viewport furniture: the header, the gizmo, the timeline with its keys
    c.font = `${Math.round(h * .045)}px ui-monospace, monospace`; c.fillStyle = "#d5d5d5";
    c.fillText("User Perspective", h * .05, h * .08); c.fillStyle = "#9a9a9a"; c.fillText(`(${Math.floor(t * 24) % 96}) Armature : forearm.R`, h * .05, h * .14);
    const gx = w - h * .13, gy = h * .14, gr = h * .07;
    for (const [a, col, lab] of [[-.4, "#e5484d", "X"], [Math.PI * .5 + .3, "#7fbf3f", "Y"], [-Math.PI * .5, "#4a8fe0", "Z"]]) {
      const ex = gx + Math.cos(a) * gr, ey = gy + Math.sin(a) * gr * .9;
      c.strokeStyle = col; c.lineWidth = h * .008; c.beginPath(); c.moveTo(gx, gy); c.lineTo(ex, ey); c.stroke();
      disc(c, ex, ey, h * .025, col); c.fillStyle = "#111"; c.font = `bold ${Math.round(h * .028)}px ui-sans-serif, sans-serif`; c.fillText(lab, ex - h * .009, ey + h * .01);
    }
    const ty = h * .86; c.fillStyle = "#1f1f22"; c.fillRect(0, ty, w, h - ty);
    c.fillStyle = "#2b2b2f"; c.fillRect(0, ty, w, h * .035);
    const fr = (t * 24) % 96, fx = (n) => h * .06 + (n / 96) * (w - h * .12);
    c.fillStyle = "#8a8a8a"; c.font = `${Math.round(h * .03)}px ui-monospace, monospace`;
    for (let n = 0; n <= 96; n += 24) c.fillText(String(n), fx(n) - h * .01, ty + h * .028);
    for (const n of [0, 12, 24, 36, 48, 60, 72, 84, 96]) { const x = fx(n), y = ty + h * .085; c.fillStyle = Math.abs(n - fr) < 6 ? "#ffd02a" : "#e8b400"; c.beginPath(); c.moveTo(x, y - h * .022); c.lineTo(x + h * .016, y); c.lineTo(x, y + h * .022); c.lineTo(x - h * .016, y); c.fill(); }
    c.fillStyle = "#4772b3"; c.fillRect(fx(fr) - 1, ty, Math.max(2, h * .006), h - ty);
    c.beginPath(); c.roundRect(fx(fr) - h * .035, ty + h * .002, h * .07, h * .03, h * .008); c.fill();
    c.fillStyle = "#fff"; c.fillText(String(Math.floor(fr)), fx(fr) - h * .015, ty + h * .026);
  }

  // ---------------------------------------------------------------- playing them
  const thumbs = {};
  function thumb(k, t = 1.4, w = 192, h = 108) {
    const key = `${k}:${t}:${w}:${h}`; if (thumbs[key]) return thumbs[key];
    const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
    draw(cv.getContext("2d"), w, h, k, t);
    return (thumbs[key] = cv.toDataURL("image/jpeg", .82));
  }
  function fit(cv) {
    const W = Math.max(1, Math.round(cv.clientWidth * DPR)), H = Math.max(1, Math.round(cv.clientHeight * DPR));
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    return [W, H];
  }
  // plays a drawing on a canvas while it is on screen; t in seconds
  function live(cv, paint) {
    const ctx = cv.getContext("2d"); let on = false, running = false;
    const frame = (now) => {
      if (!on) { running = false; return; }
      const [W, H] = fit(cv); paint(ctx, W, H, now / 1000);
      if (calm) { running = false; return; }
      requestAnimationFrame(frame);
    };
    new IntersectionObserver((es) => { on = es[0].isIntersecting; if (on && !running) { running = true; requestAnimationFrame(frame); } }).observe(cv);
  }
  // the drawn engine's card: the page's own film, cut every two seconds
  const reel = [4, 1, 5, 6, 3, 0, 2, 7];
  function drawnReel(c, w, h, t) { const k = Math.floor(t / 2) % reel.length; draw(c, w, h, reel[k], (t % 2) + .3); }
  window.Film = { shots, draw, thumb, fit, live, scenes: { cartoon, anime, rig, drawn: drawnReel } };
})();
