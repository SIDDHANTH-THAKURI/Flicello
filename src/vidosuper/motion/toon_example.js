// toon_example.js: the quality bar for the drawn engine, read before drawing. A lead character drawn with care (an
// anime heroine: cel-shaded in two tones, a face in profile with expressions, hair, collar, ribbon and skirt that the
// wind carries, animated on twos) and a whole shot that uses her: layered depth, a light source, particles in the
// air, a build and an impact frame on the beat.
//
// Note how she is drawn: in units of her own height (c.scale(s * dir, s)), facing right, so one drawing serves every
// size and both directions; every loose part (hair, collar, ribbon, skirt) is driven by time, so she is never still;
// her face reads at a glance. Draw each lead like this, in the film's own look; never a stick figure for a lead.

function heroine(c, x, y, s, t, ink, dir, face, pose) {
  const k = (col) => ink || col, wv = (n) => Math.sin(t * 5 + n), f = .02 + .02 * wv(1), f2 = .025 + .02 * wv(2.3);
  const HAIR = "#2b2456", HAIR2 = "#1d1840", SKIN = "#ffe6d8", SKIN2 = "#f2c4b4", NAVY = "#27306a", NAVY2 = "#1b2150", WHITE = "#f7f8ff", WHITE2 = "#cfd5f2", RED = "#e8344e";
  c.save(); c.translate(x, y); c.scale(s * dir, s); c.lineJoin = c.lineCap = "round";
  const poly = (col, pts, close = true) => { c.fillStyle = k(col); c.beginPath(); pts.forEach(([a, b], i) => (i ? c.lineTo(a, b) : c.moveTo(a, b))); if (close) c.closePath(); c.fill(); };
  // long hair streaming back
  for (let i = 0; i < 7; i++) toon.ribbon(c, -.05 + i * .004, -.875 + i * .028, .3 + i * .03 + .02 * wv(i), .075 - i * .005, t + i * .17, k(i % 2 ? HAIR2 : HAIR), .12 + i * .06, 6);
  // legs, socks, shoes, posed by the rig from the hip (far one first); her thighs are under the skirt
  const p = pose || toon.pose.stand(t), lift = (p.bob || 0);
  c.translate(0, -lift);
  [[-.018, p.legs[1], SKIN2, NAVY2], [.012, p.legs[0], SKIN, NAVY]].forEach(([dx, [a1, a2], sk, sock]) => {
    c.save(); c.translate(dx, -.47); c.rotate(-a1);
    poly(sk, [[-.02, 0], [.022, 0], [.019, .27], [-.016, .27]]);
    c.translate(0, .27); c.rotate(-(a2 - a1));
    poly(sk, [[-.016, 0], [.019, 0], [.013, .17], [-.009, .17]]);
    poly(sock, [[-.014, .03], [.018, .03], [.013, .17], [-.009, .17]]);
    c.fillStyle = k("#3a2226"); c.beginPath(); c.ellipse(.012, .185, .034, .015, 0, 0, Math.PI * 2); c.fill();
    c.restore();
  });
  // pleated skirt, lifted by the wind
  const hem = []; for (let i = 0; i <= 8; i++) hem.push([toon.lerp(.105, -.16 - f, i / 8), -.295 + (i % 2 ? .012 : 0) - f * .7 * i / 8]);
  c.fillStyle = k(NAVY); c.beginPath(); c.moveTo(-.05, -.47); c.lineTo(.048, -.47); c.quadraticCurveTo(.08, -.38, .105, -.295);
  hem.forEach(([a, b]) => c.lineTo(a, b)); c.quadraticCurveTo(-.1 - f * .5, -.4, -.05, -.47); c.fill();
  if (!ink) { c.strokeStyle = NAVY2; c.lineWidth = .006; for (let i = 1; i < 8; i += 2) { c.beginPath(); c.moveTo(toon.lerp(.04, -.045, i / 8), -.47); c.lineTo(...hem[i]); c.stroke(); } }
  // blouse
  c.fillStyle = k(WHITE); c.beginPath(); c.moveTo(-.048, -.47); c.lineTo(.046, -.47); c.quadraticCurveTo(.072, -.58, .068, -.63);
  c.quadraticCurveTo(.06, -.7, .03, -.725); c.lineTo(-.04, -.725); c.quadraticCurveTo(-.062, -.6, -.048, -.47); c.fill();
  if (!ink) poly(WHITE2, [[-.048, -.47], [-.01, -.47], [-.02, -.6], [-.03, -.72], [-.04, -.725], [-.06, -.6]]);
  // sailor collar flying back, with its stripe
  poly(NAVY, [[.025, -.735], [-.045, -.735], [-.115 - f2, -.645 + f2 * .3], [-.065 - f2 * .5, -.6], [-.02, -.66]]);
  if (!ink) { c.strokeStyle = WHITE; c.lineWidth = .005; c.beginPath(); c.moveTo(-.04, -.722); c.lineTo(-.1 - f2, -.645 + f2 * .3); c.lineTo(-.064 - f2 * .5, -.613); c.stroke(); }
  c.strokeStyle = k(NAVY); c.lineWidth = .012; c.beginPath(); c.moveTo(.028, -.728); c.lineTo(.06, -.655); c.stroke();
  // arm, sleeve swaying
  const sw = .01 * wv(2), [ua, fa] = p.arms[0], el = toon.joint(0, -.7, ua + sw, .12), wr = toon.joint(el[0], el[1], fa + sw, .11);
  c.strokeStyle = k(WHITE2); c.lineWidth = .044; c.beginPath(); c.moveTo(.0, -.7); c.lineTo(...el); c.lineTo(...wr); c.stroke();
  c.strokeStyle = k(WHITE); c.lineWidth = .036; c.stroke();
  const cuff = toon.joint(el[0], el[1], fa + sw, .1);
  c.strokeStyle = k(NAVY); c.lineWidth = .04; c.beginPath(); c.moveTo(...cuff); c.lineTo(...wr); c.stroke();
  c.fillStyle = k(SKIN); c.beginPath(); c.arc(...toon.joint(wr[0], wr[1], fa + sw, .016), .017, 0, Math.PI * 2); c.fill();
  // the red ribbon at her collar, its tails in the wind
  toon.ribbon(c, .058, -.655, .2, .03, t + .4, k(RED), .25, 7);
  poly(RED, [[.058, -.655], [.08, -.675], [.082, -.64]]); poly(RED, [[.058, -.655], [.04, -.672], [.038, -.64]]);
  // neck and head
  poly(SKIN2, [[-.012, -.795], [.02, -.795], [.02, -.72], [-.012, -.72]]);
  c.fillStyle = k(SKIN); c.beginPath(); c.arc(-.005, -.84, .07, 0, Math.PI * 2); c.fill();
  c.beginPath(); c.moveTo(.045, -.895); c.quadraticCurveTo(.07, -.87, .068, -.852); c.lineTo(.081, -.836); c.lineTo(.068, -.828);
  c.quadraticCurveTo(.071, -.817, .064, -.81); c.quadraticCurveTo(.058, -.79, .038, -.784); c.quadraticCurveTo(.0, -.776, -.03, -.8); c.closePath(); c.fill();
  if (!ink) {
    c.fillStyle = "rgba(255,110,140,.45)"; c.beginPath(); c.ellipse(.034, -.814, .015, .006, 0, 0, Math.PI * 2); c.fill();
    c.strokeStyle = "#b5606a"; c.lineWidth = .003; c.beginPath(); c.moveTo(.06, -.812); c.lineTo(.066, -.811); c.stroke();
    const blink = (t % 3.6) < .17, open = blink ? .12 : face === "surprised" ? 1.25 : face === "angry" ? .7 : 1;
    if (face === "happy") { // eyes closed in a smile
      c.strokeStyle = "#1d1840"; c.lineWidth = .006; c.beginPath(); c.arc(.047, -.836, .012, Math.PI * 1.1, Math.PI * 1.9); c.stroke();
    } else {
      c.fillStyle = "#fff"; c.beginPath(); c.ellipse(.046, -.84, .013, .02 * open, 0, 0, Math.PI * 2); c.fill();
      c.fillStyle = "#3b2a7a"; c.beginPath(); c.ellipse(.049, -.839, .01 * (face === "surprised" ? .8 : 1), .018 * open, 0, 0, Math.PI * 2); c.fill();
      if (open > .5) {
        c.fillStyle = "#8a6cff"; c.beginPath(); c.ellipse(.049, -.832, .008, .009, 0, 0, Math.PI * 2); c.fill();
        c.fillStyle = "#fff"; c.beginPath(); c.arc(.052, -.848, .0045, 0, Math.PI * 2); c.fill();
      }
    }
    const talk = face === "talk" && Math.floor(t * 12) % 3 !== 0, mouthOpen = face === "surprised" || talk;
    if (mouthOpen) { c.fillStyle = "#7a2a3a"; c.beginPath(); c.ellipse(.062, -.809, .006, talk ? .005 : .008, 0, 0, Math.PI * 2); c.fill(); }
    if (face === "sad" || face === "angry") { c.strokeStyle = "#1d1840"; c.lineWidth = .004; c.beginPath();
      if (face === "sad") { c.moveTo(.036, -.872); c.lineTo(.06, -.88); } else { c.moveTo(.036, -.884); c.lineTo(.06, -.868); } c.stroke(); }
    c.strokeStyle = "#1d1840"; c.lineWidth = .006; c.beginPath(); c.moveTo(.03, -.859); c.quadraticCurveTo(.048, -.866, .062, -.857); c.stroke();
    if (face !== "sad" && face !== "angry") { c.lineWidth = .003; c.beginPath(); c.moveTo(.036, -.877); c.lineTo(.06, -.874); c.stroke(); }
  }
  // hair cap, fringe and a side lock
  c.fillStyle = k(HAIR); c.beginPath(); c.moveTo(-.076, -.79); c.quadraticCurveTo(-.092, -.9, -.01, -.918); c.quadraticCurveTo(.062, -.916, .08, -.866);
  for (const [a, b] of [[.064, -.864], [.072, -.846], [.052, -.86], [.055, -.84], [.036, -.862]]) c.lineTo(a, b);
  c.quadraticCurveTo(.012, -.852, -.002, -.822); c.lineTo(-.018, -.758); c.lineTo(-.034, -.8); c.closePath(); c.fill();
  toon.ribbon(c, .0, -.83, .13, .022, t + 1, k(HAIR2), .2, 6);
  if (!ink) { c.strokeStyle = "#5b4ea0"; c.lineWidth = .009; c.beginPath(); c.arc(-.01, -.85, .056, Math.PI * 1.12, Math.PI * 1.55); c.stroke(); }
  c.restore();
}


toon.cast("heroine", (c, o) => heroine(c, o.x, o.y, o.s, o.t, o.ink, o.dir, o.face, o.pose));

// a whole shot: the hilltop in the wind, 3.6 s
toon.shot({
  blur: 2,
  draw(c, w, h, t) {
    const tc = toon.twos(t), cyc = t % 3.6, impact = cyc > 3.0 && cyc < 3.16;
    const zoom = toon.key(t, [[0, 1], [3.0, 1.08, "in"], [3.16, 1.0, "out"]]);
    toon.camera(c, w, h, { zoom, shake: impact ? toon.shake(t, h * .01) : [0, 0] }, () => {
      toon.sky(c, w, h, [[0, "#2a6fd6"], [.6, "#7fc4ff"], [1, "#d8f0ff"]]);
      toon.glow(c, w * .85, h * .1, h * .9, "#ffffff", .55);
      toon.puff(c, w * .25 - t * h * .008, h * .45, h * .12, "#ffffff", "#a9c8f0");
      toon.ridge(c, w, h, h * .8, 5, "#5d8fcf", .14);
      c.fillStyle = "#3aa36b"; c.beginPath(); c.ellipse(w * .6, h * 1.27, w * .66, h * .5, 0, 0, Math.PI * 2); c.fill();
      toon.grass(c, w, h, tc, { n: 40, y0: .8, y1: 1, cols: ["#2a7d52"] });
      toon.draw("heroine", c, { x: w * .6, y: h * .79, s: h * .62, dir: -1, t: tc, face: cyc > 2.4 ? "surprised" : "neutral" });
      toon.particles(c, w, h, t, { n: 34, kind: "petal", col: "#ffb7d0", wind: .16, fall: .03 });
    });
    if (impact) { // the held hit: ink and paper
      c.fillStyle = "#0b0b14"; c.fillRect(0, 0, w, h);
      toon.speedLines(c, w, h, w * .6, h * .43, { inner: .34 });
      toon.draw("heroine", c, { x: w * .6, y: h * .79, s: h * .62, dir: -1, t: tc, ink: "#f4f1ea" });
    }
    toon.grain(c, w, h, { a: .06, t });
  },
});
