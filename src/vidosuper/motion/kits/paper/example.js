// The first two scenes of "The Paper Studio" (16:9, 1920 x 1080), written with the paper kit: the bar to beat.
// Scene 1 (0 - 8.25): a valley at night turns to dawn, the land pops up layer by layer, a filmmaker and a camera on a
// hill, a viewfinder finds the sunrise: "Every film starts as an idea." Scene 2 (8.25 - 16.34): the same valley goes
// grey, editing software rises like towers, a clock replaces the sun, files rain down, the filmmaker types and
// slumps: "...and then it gets stuck in hours of editing." Both live in one scene here because they share the land.
{
  const { C, cam, T, E } = scene('valley', 0, 16.34);
  to(cam, { s: 1.05 }, T, E - T, 'sine.inOut');
  shake(cam, 15.4, 16.34, 5);
  const SUNX = 1180;
  // the sky: night turning to dawn, later the grey of being stuck
  const sky = skyBands(C, [-60, 160, 290, 400, 495, 580], ['#221f42', '#2d2954', '#3d2f64', '#533670', '#6d3f75', '#89497a'], 101);
  recolor(sky, ['#2d2a4a', '#4b3466', '#7b3f6e', '#b5566f', '#e27a6c', '#f5a77d'], 0.2, 5.6);
  recolor(sky, ['#1c1f32', '#252a41', '#30364f', '#3b425b', '#474e66', '#545a70'], 8.25, 1.5);
  const stars = starfield(C, 50, [0, 0, W, 430], '#fbe9d0', 5);
  to(stars, { o: 0 }, 1.4, 3.2, 'sine.inOut');
  const sn = sun(C, SUNX, 1000, 104);
  to(sn, { y: 468 }, 0.4, 6.4, 'power1.out');
  to(sn, { y: 900 }, 8.25, 1.4, 'power2.in');
  const clk = clock(C, SUNX, 900, 120, 9);
  hide(clk); set(clk, { o: 1 }, 8.9); to(clk, { y: 330 }, 8.9, 1.4, 'back.out(1.3)');
  [[240, 330, 260], [1650, 215, 300], [1850, 365, 180], [980, 300, 150]].forEach(([x, y, w], i) => {
    const c = cloud(C, x, y, w, '#4d4170', 30 + i);
    to(c, { x: x + 90 }, 0, 16.34, 'none');
    fill(c.__path, '#f7cfc6', 0.4, 5.4); fill(c.__path, '#6d7187', 8.25, 1.5);
  });
  // the land, recoloured with the light: night, dawn, grey (each row: the colour drawn, then what it becomes)
  const PAL = [
    ['#4a3f7a', '#6a4486', '#4a4f6e'], ['#3d3369', '#573a75', '#3f4360'], ['#8a76b4', '#ffa48c', '#8f93a8'], ['#a99cc8', '#f8d7d0', '#c9cbd6'],
    ['#382c62', '#4a2d66', '#383c58'], ['#2f2556', '#3c2457', '#2f324b'], ['#76649e', '#f0857a', '#7b7f96'],
    ['#2a2050', '#33204f', '#2a2d45'], ['#231a45', '#291943', '#22253a'], ['#62548a', '#d9707a', '#666a82'],
    ['#221a40', '#28183f', '#202236'], ['#1c1436', '#1f1434', '#1a1b2e'], ['#332958', '#3a2a5c', '#2c2f48'],
    ['#150e28', '#170d29', '#14152a'], ['#100a20', '#120a20', '#101124'],
  ];
  const land = el('g', {}, C);
  const farL = pl(land, H + 60, 1); lowpoly(farL.__in, { top: 560, amp: 230, n: 7, col: '#4a3f7a', shade: '#3d3369', rim: '#8a76b4', seed: 11, sunX: SUNX, snow: '#a99cc8' });
  const midL = pl(land, H + 60, 2); lowpoly(midL.__in, { top: 655, amp: 200, n: 6, col: '#382c62', shade: '#2f2556', rim: '#76649e', seed: 12, sunX: SUNX });
  const towers = el('g', {}, land); // the software rises here, between the middle and near ridges
  const nearL = pl(land, H + 60, 2); lowpoly(nearL.__in, { top: 775, amp: 130, n: 9, col: '#2a2050', shade: '#231a45', rim: '#62548a', seed: 13, sunX: SUNX });
  const hB = bump(915, 70, 1560, 480, 8, 0.004), hA = bump(948, 96, 380, 430, 10, 0.005);
  const hillB = pl(land, H + 60, 2); hill(hillB.__in, hB, -140, W + 140, '#221a40', 14); crest(hillB.__in, hB, -140, W + 140, '#62548a');
  const pinesB = [[1230, 120], [1320, 90], [1700, 150], [1790, 105], [1880, 135], [1050, 80]].map(([x, h], i) => pine(land, x, hB(x) + 8, h, '#150e28', '#100a20', 40 + i));
  const hillA = pl(land, H + 60, 3); hill(hillA.__in, hA, -140, W + 140, '#1c1436', 15); grass(hillA.__in, -120, W + 120, hA, '#332958', 16); crest(hillA.__in, hA, -140, W + 140, '#62548a');
  const pinesA = [[40, 190], [140, 130], [1110, 95]].map(([x, h], i) => pine(land, x, hA(x) + 8, h, '#150e28', '#100a20', 60 + i));
  popUp(midL, 0.12, 0.7); popUp(nearL, 0.38, 0.7); popUp(hillB, 0.52, 0.6); popUp(hillA, 0.62, 0.6);
  [...pinesB, ...pinesA].forEach((p, i) => popUp(p, 0.75 + i * 0.06, 0.45));
  palette(land, PAL, [0.2, 8.25], [5.6, 1.5]);
  [[2.2, 340, 0.8], [2.45, 300, 0.6], [2.8, 385, 0.7], [4.6, 255, 0.5]].forEach(([t0, y, s], i) => {
    const b = bird(C, -80, y, s, '#2d2a4a', 50 + i);
    hide(b); set(b, { o: 1 }, t0); to(b, { x: W + 120, y: y - 70 }, t0, 7.2, 'none');
  });
  sfx(2.25, 'chirp'); sfx(2.9, 'chirp', { gain: -4 });
  // the filmmaker on the hill, the camera on its tripod
  const fm = person(C, 420, hA(420) + 2, 1.3);
  popUp(fm, 1.0, 0.5);
  const tp = tripod(C, 560, hA(560) + 2, 1.2);
  popUp(tp, 1.12, 0.5); popDown(tp, 8.45, 0.3);
  // what the camera sees: a viewfinder round the sunrise, and the shutter
  const vf = pup(C, { x: SUNX, y: 470 });
  const VW = 760, VH = 430;
  for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    const cx = (sx * VW) / 2, cy = (sy * VH) / 2, br = el('g', { filter: shadow(1) }, vf);
    cutShape(br, [[cx, cy], [cx - sx * 74, cy], [cx - sx * 74, cy - sy * 11], [cx - sx * 11, cy - sy * 11], [cx - sx * 11, cy - sy * 74], [cx, cy - sy * 74]], '#fbf6ec', 300 + sx * 2 + sy, 0.8);
  }
  const recDot = el('circle', { cx: -VW / 2 + 44, cy: -VH / 2 + 44, r: 10, fill: '#ff4a4a' }, vf);
  txt(vf, 'REC', -VW / 2 + 64, -VH / 2 + 54, 28, { font: 'Geist Mono', weight: 400, fill: '#fbf6ec', anchor: 'start', spacing: 2 });
  LIVE.push((t) => recDot.setAttribute('opacity', Math.floor(t * 2.2) % 2 ? '0.2' : '1'));
  const shutter = pup(vf, { o: 0 }); el('rect', { x: -VW / 2, y: -VH / 2, width: VW, height: VH, fill: '#fff6e0' }, shutter);
  focusIn(vf, 4.3);
  to(vf, { s: 0.95 }, 6.95, 0.07, 'power2.in'); to(vf, { s: 1 }, 7.02, 0.35, 'back.out(3)');
  to(shutter, { o: 0.85 }, 6.95, 0.05, 'none'); to(shutter, { o: 0 }, 7.0, 0.45, 'power2.out');
  sfx(6.95, 'clack', { gain: -6 });
  to(vf, { o: 0 }, 7.75, 0.3, 'power1.in');
  const t1 = strip(C, 'Every film starts as an idea.', 960, 128, 82, { accent: 'an idea.', tilt: -1.2, seed: 21 });
  stripIn(t1, 1.4); stripOut(t1, 7.85);

  // ---- stuck in editing: a stand-up desk and a laptop where the tripod was; he types, and types, and slumps
  const desk = pup(C, { x: 540, y: hA(540) + 2, sx: 1.3, sy: 1.3 }, { filter: shadow(2) });
  el('line', { x1: -44, y1: 0, x2: -36, y2: -84, stroke: '#5a3a2a', 'stroke-width': 7, 'stroke-linecap': 'round' }, desk);
  el('line', { x1: 44, y1: 0, x2: 36, y2: -84, stroke: '#5a3a2a', 'stroke-width': 7, 'stroke-linecap': 'round' }, desk);
  cutRect(desk, -66, -94, 132, 14, '#c8955f', 220, 3, 1);
  el('ellipse', { cx: -18, cy: -140, rx: 40, ry: 50, fill: '#7fe8ff', opacity: 0.16 }, desk);
  cutRect(desk, -46, -102, 82, 9, '#2b2236', 221, 2, 0.6);
  cutShape(desk, [[26, -100], [42, -172], [50, -170], [34, -100]], '#3a3550', 222, 0.8);
  popUp(desk, 8.7, 0.45);
  pose(fm, 8.9, 16.4, P_TYPE);
  pose(fm, 13.2, 16.4, P_SLUMP);
  sfx(8.95, 'keys', { until: 16.3 });
  sfx(9.0, 'ticks', { until: 16.3, every: 0.25 });
  // the software piles up into towers behind the hills
  popUp(ui.timeline(towers, 520, 800), 8.55, 0.6);
  const menu = pl(towers, 800, 0); ui.menu(menu.__in, 900, 240); popUp(menu, 9.05, 0.6);
  popUp(ui.plugins(towers, 1450, 800), 9.55, 0.6);
  const board = pl(towers, 800, 0);
  cutRect(board.__in, 1703, 450, 18, 360, '#3b3760', 801, 3, 1);
  const rb = ui.render(board.__in, 1712, 398, '3%', '6 h left');
  popUp(board, 10.05, 0.6);
  // and the clutter rains down on the hills, one piece a beat
  const fall = el('g', {}, C);
  [[B(22), 'sticky', 700], [B(23), 'key', 930], [B(24), 'file', 650, 'final_v7_REAL.mp4'], [B(25), 'wheel', 1110],
    [B(26), 'sticky', 1390], [B(27), 'key', 1040], [B(28), 'file', 1520, 'final_FINAL_2.mp4'], [B(29), 'sticky', 250]]
    .forEach(([t0, kind, x, name], i) => {
      const ground = (x < 1100 ? hA(x) : hB(x)) - 16, r = (rng(810 + i)() - 0.5) * 50;
      const p = kind === 'sticky' ? ui.sticky(fall, x, 0) : kind === 'key' ? ui.keyframe(fall, x, 0, i % 2 ? '#ffd36b' : '#8f5bff')
        : kind === 'file' ? ui.file(fall, x, 0, name) : ui.wheel(fall, x, 0);
      drop(p, t0, ground, r);
    });
  const t2a = strip(C, '…and then it gets stuck in', 500, 104, 62, { tilt: -1.4, seed: 23 });
  const t2b = strip(C, 'hours of editing.', 560, 200, 92, { accent: 'hours of editing.', tilt: 1.8, seed: 24 });
  stripIn(t2a, 10.25); stripIn(t2b, 10.7);
}
