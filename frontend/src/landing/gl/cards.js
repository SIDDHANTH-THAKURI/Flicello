// The instanced cards: one mesh, every chapter's layout baked into per-card attributes. Layouts are computed
// for the screen's shape (wide or portrait) and rebuilt only when that shape changes.
import * as THREE from 'three';
import { cardVertex, cardFragment } from './shaders.js';

const TILES = 12;
const GOLDEN = Math.PI * (3 - Math.sqrt(5));

function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// what the screen can show at z = 0 for the landing camera
export function frame(aspect, fov = 35, dist = 14) {
  const halfH = dist * Math.tan((fov * Math.PI) / 360);
  return { halfH, halfW: halfH * aspect, wide: aspect >= 1.05 };
}

export function buildLayouts(aspect) {
  const { halfH, halfW, wide } = frame(aspect);
  const N = wide ? 300 : 168;
  const rand = rng(20261016);
  const L = {
    N, wide, halfW, halfH,
    tile: new Float32Array(N), seed: new Float32Array(N * 4),
    l0: new Float32Array(N * 4), l1: new Float32Array(N * 4), l2: new Float32Array(N * 4),
    l3: new Float32Array(N * 4), l4: new Float32Array(N * 4), l5: new Float32Array(N * 4),
    vis: new Float32Array(N * 4), flag: new Float32Array(N * 4), fan: new Float32Array(N),
  };
  const set4 = (arr, i, a, b, c, d) => { arr[i * 4] = a; arr[i * 4 + 1] = b; arr[i * 4 + 2] = c; arr[i * 4 + 3] = d; };

  // ---- galaxy: two loose arms of raw footage, off to the right of the headline
  const galaxy = wide ? new THREE.Vector3(halfW * 0.56, -0.2, -2.4) : new THREE.Vector3(0, halfH * 0.86, -3.6);
  const rMax = wide ? Math.min(halfW * 0.8, 7.4) : halfW * 1.05;
  for (let i = 0; i < N; i++) {
    L.tile[i] = Math.floor(rand() * TILES);
    set4(L.seed, i, rand(), rand(), rand(), rand());
    const u = rand();
    const r = 0.6 + (rMax - 0.6) * Math.pow(u, 0.62);
    const arm = (i % 3) * (Math.PI * 2 / 3);
    const th = arm + r * 0.62 + (rand() - 0.5) * 0.9;
    const h = (rand() - 0.5) * (0.5 + 1.4 * (1 - u));
    const scale = (wide ? 1.0 : 0.78) * (1.1 - 0.45 * u) * (0.7 + rand() * 0.6);
    set4(L.l0, i, r, th, h, scale);
  }

  // ---- the wall: a contact sheet, tilted away to the right; the beam judges it column by column
  const cols = wide ? 25 : 12;
  const rows = Math.ceil(N / cols);
  const wallH = wide ? halfH * 1.72 : halfH * 1.05;
  const pitchY = wallH / rows;
  const cardH = pitchY * 0.86;
  const pitchX = cardH * 0.5625 + pitchY * 0.14;
  const wallRot = wide ? -0.42 : 0;
  const wallAt = wide ? new THREE.Vector3(halfW * 0.22, -0.05, -1.2) : new THREE.Vector3(0, halfH * 0.3, -1.4);
  const kept = [];
  for (let i = 0; i < N; i++) {
    const c = i % cols;
    const r = Math.floor(i / cols);
    const local = new THREE.Vector3((c - (cols - 1) / 2) * pitchX, ((rows - 1) / 2 - r) * pitchY, 0);
    local.applyAxisAngle(new THREE.Vector3(0, 1, 0), wallRot).add(wallAt);
    set4(L.l1, i, local.x, local.y, local.z, cardH);
    const keep = rand() < 0.34 ? 1 : 0;
    if (keep) kept.push(i);
    set4(L.flag, i, keep, -1, -1, c / (cols - 1));
  }

  // ---- story: the kept cards become three constellations (three pitches); the rest become far stars
  const centres = wide
    ? [new THREE.Vector3(-halfW * 0.5, 0.55, 0), new THREE.Vector3(0.1, 0.35, 0.3), new THREE.Vector3(halfW * 0.52, 0.55, 0)]
    : [new THREE.Vector3(-halfW * 0.62, halfH * 0.38, 0), new THREE.Vector3(0, halfH * 0.1, 0.3), new THREE.Vector3(halfW * 0.62, halfH * 0.38, 0)];
  const front = wide ? new THREE.Vector3(halfW * 0.3, 0.1, 1.4) : new THREE.Vector3(0, halfH * 0.22, 1.6);
  const spread = wide ? 1.75 : 0.85;
  const perCluster = [0, 0, 0];
  const chosenCards = [];
  kept.forEach((i, n) => {
    const k = n % 3;
    const j = perCluster[k]++;
    const rr = spread * Math.sqrt((j + 0.5) / (kept.length / 3));
    const a = j * GOLDEN;
    const p = centres[k].clone().add(new THREE.Vector3(Math.cos(a) * rr, Math.sin(a) * rr * 0.82, (rand() - 0.5) * 0.6));
    const s = (wide ? 0.62 : 0.4) * (1.1 - 0.35 * (rr / spread));
    set4(L.l2, i, p.x, p.y, p.z, s);
    L.fan[i] = (rand() - 0.5) * 0.35;
    L.flag[i * 4 + 1] = k;
    if (k === 1) chosenCards.push(i);
  });
  for (let i = 0; i < N; i++) {
    if (L.flag[i * 4] > 0.5) continue;
    const far = new THREE.Vector3((rand() - 0.5) * halfW * 3.4, (rand() - 0.5) * halfH * 3.2, -7 - rand() * 12);
    set4(L.l2, i, far.x, far.y, far.z, 0.5 + rand() * 0.4);
    L.fan[i] = (rand() - 0.5) * 0.8;
  }

  // ---- timeline: the chosen pitch is laid out on the beat (1-, 2- and half-beat shots)
  const beats = [2, 1, 1, 2, 1, 1, 0.5, 0.5, 1, 2, 1, 1, 2, 1, 0.5, 0.5, 1, 1, 2, 1, 1, 2];
  const slots = wide ? 14 : 10;
  // wide screens: the timeline lives to the right of the copy; portrait: full width under the scene
  const tlW = wide ? halfW * 0.9 : halfW * 1.9;
  const tlX = wide ? halfW * 0.42 : 0;
  const totalBeats = beats.slice(0, slots).reduce((a, b) => a + b, 0);
  const tlY = wide ? -halfH * 0.36 : halfH * 0.14;
  const unit = (tlW - 0.05 * (slots - 1)) / totalBeats;
  let x = tlX - tlW / 2;
  const slotX = [];
  for (let s = 0; s < slots; s++) {
    const w = beats[s] * unit;
    slotX.push([x + w / 2, w, beats[s]]);
    x += w + 0.05;
  }
  for (let i = 0; i < N; i++) {
    const idx = chosenCards.indexOf(i);
    if (idx >= 0 && idx < slots) {
      const [cx, w] = slotX[idx];
      // timeline strips are drawn in card units: width is scaled against the 9:16 card
      set4(L.l3, i, cx, tlY, 0.4, w / 0.5625);
      L.flag[i * 4 + 2] = idx;
    } else {
      const far = new THREE.Vector3((rand() - 0.5) * halfW * 3.2, (rand() - 0.5) * halfH * 3, -8 - rand() * 12);
      set4(L.l3, i, far.x, far.y, far.z, 0.45 + rand() * 0.3);
    }
  }

  // ---- phone: the timeline pours into the phone; everything else waits in the dark
  const phoneAt = wide ? new THREE.Vector3(halfW * 0.42, 0, 1.2) : new THREE.Vector3(0, halfH * 0.18, 1.2);
  for (let i = 0; i < N; i++) {
    if (L.flag[i * 4 + 2] > -0.5) {
      set4(L.l4, i, phoneAt.x + (rand() - 0.5) * 0.3, phoneAt.y + (rand() - 0.5) * 0.6, phoneAt.z - 0.3, 0.02);
    } else {
      set4(L.l4, i, L.l3[i * 4], L.l3[i * 4 + 1], L.l3[i * 4 + 2] - 2, L.l3[i * 4 + 3] * 0.9);
    }
  }

  // ---- ambient: a slow far field behind the rest of the page
  for (let i = 0; i < N; i++) {
    set4(L.l5, i, (rand() - 0.5) * halfW * 3.6, (rand() - 0.5) * halfH * 3.4, -9 - rand() * 14, 0.6 + rand() * 0.6);
  }

  // brightness per chapter
  for (let i = 0; i < N; i++) {
    const k = L.flag[i * 4] > 0.5;
    const inTl = L.flag[i * 4 + 2] > -0.5;
    set4(L.vis, i, 0.92, k ? 1.0 : 0.2, inTl ? 1.0 : 0.16, inTl ? 0.0 : 0.13);
  }

  L.galaxy = galaxy;
  L.wallRot = wallRot;
  L.centres = centres;
  L.front = front;
  L.timeline = { x: tlX, y: tlY, w: tlW, slots: slotX, height: Math.min(1.05, unit * 1.9) };
  L.phoneAt = phoneAt;
  L.wall = { cols, rows, at: wallAt, rot: wallRot, pitchX, pitchY, cardH };
  L.kept = kept;
  L.chosen = chosenCards.slice(0, slots);
  return L;
}

export function createCards(videoTexture, aspect) {
  const geo = new THREE.InstancedBufferGeometry();
  const base = new THREE.PlaneGeometry(1, 1, 1, 1);
  geo.index = base.index;
  geo.setAttribute('position', base.getAttribute('position'));
  geo.setAttribute('uv', base.getAttribute('uv'));

  const uniforms = {
    uVideo: { value: videoTexture },
    uTime: { value: 0 },
    uMorph: { value: 0 },
    uGalaxy: { value: new THREE.Vector3() },
    uTilt: { value: new THREE.Vector2(-0.42, 0.26) },
    uMouse: { value: new THREE.Vector3(99, 99, 0) },
    uMouseOn: { value: 0 },
    uWallRot: { value: 0 },
    uTimelineH: { value: 0.9 },
    uPulse: { value: 0 },
    uPick: { value: 0 },
    uCentre0: { value: new THREE.Vector3() },
    uCentre1: { value: new THREE.Vector3() },
    uCentre2: { value: new THREE.Vector3() },
    uFront: { value: new THREE.Vector3() },
    uScan: { value: -1 },
    uPlayhead: { value: -99 },
    uBg: { value: new THREE.Color('#06060a') },
    uGalaxyF: { value: new THREE.Vector3() },
  };
  const material = new THREE.ShaderMaterial({ vertexShader: cardVertex, fragmentShader: cardFragment, uniforms });
  const mesh = new THREE.Mesh(geo, material);
  mesh.frustumCulled = false;

  let layouts = null;
  function apply(aspectNow) {
    layouts = buildLayouts(aspectNow);
    const L = layouts;
    const attr = (name, arr, size) => geo.setAttribute(name, new THREE.InstancedBufferAttribute(arr, size));
    attr('aTile', L.tile, 1);
    attr('aSeed', L.seed, 4);
    attr('aL0', L.l0, 4);
    attr('aL1', L.l1, 4);
    attr('aL2', L.l2, 4);
    attr('aL3', L.l3, 4);
    attr('aL4', L.l4, 4);
    attr('aL5', L.l5, 4);
    attr('aVis', L.vis, 4);
    attr('aFlag', L.flag, 4);
    attr('aR', L.fan, 1);
    geo.instanceCount = L.N;
    uniforms.uGalaxy.value.copy(L.galaxy);
    uniforms.uGalaxyF.value.copy(L.galaxy);
    uniforms.uWallRot.value = L.wallRot;
    uniforms.uCentre0.value.copy(L.centres[0]);
    uniforms.uCentre1.value.copy(L.centres[1]);
    uniforms.uCentre2.value.copy(L.centres[2]);
    uniforms.uFront.value.copy(L.front);
    uniforms.uTimelineH.value = L.timeline.height;
    uniforms.uTilt.value.set(L.wide ? -0.42 : -0.5, L.wide ? 0.26 : 0.12);
    return L;
  }
  apply(aspect);
  return { mesh, uniforms, get layouts() { return layouts; }, rebuild: apply };
}
