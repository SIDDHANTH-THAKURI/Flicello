// One renderer behind the whole page. The DOM scrolls over it; the story module feeds it a state each frame.
import * as THREE from 'three';
import {
  EffectComposer, RenderPass, EffectPass, BloomEffect, ChromaticAberrationEffect, VignetteEffect, NoiseEffect,
  SMAAEffect, BlendFunction, SMAAPreset,
} from 'postprocessing';
import { createCards, frame } from './cards.js';
import { createPhone } from './phone.js';
import { createWave } from './wave.js';

const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (x) => { x = Math.min(1, Math.max(0, x)); return x * x * (3 - 2 * x); };

export function createStage(canvas, video) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'high-performance', stencil: false });
  renderer.setClearColor('#06060a');
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#06060a');
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 80);
  camera.position.set(0, 0, 14);

  const texture = new THREE.VideoTexture(video);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = false;

  let size = { w: 0, h: 0, aspect: 1.6 };
  const cards = createCards(texture, 1.6);
  scene.add(cards.mesh);
  const phone = createPhone(texture);
  scene.add(phone.group);
  const wave = createWave();
  scene.add(wave.group);
  wave.fit(cards.layouts);

  // the light at the heart of the galaxy (additive, so cards in front of it stay crisp)
  const coreUniforms = { uOn: { value: 1 }, uTime: { value: 0 } };
  const core = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.ShaderMaterial({
    uniforms: coreUniforms,
    vertexShader: `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `uniform float uOn; uniform float uTime; varying vec2 vUv;
      void main() {
        vec2 p = vUv - 0.5;
        float r = length(p);
        float g = (exp(-r * r * 30.0) * 0.85 + exp(-r * r * 9.0) * 0.16) * smoothstep(0.5, 0.28, r);
        float a = atan(p.y, p.x);
        float rays = 0.85 + 0.15 * sin(a * 7.0 + uTime * 0.3) * sin(a * 3.0 - uTime * 0.2);
        vec3 c = mix(vec3(1.0, 0.62, 0.34), vec3(1.0, 0.24, 0.52), smoothstep(0.0, 0.32, r)) * g * rays;
        gl_FragColor = vec4(c * uOn * 0.62, 1.0);
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  }));
  core.scale.set(11, 11, 1);
  scene.add(core);

  const composer = new EffectComposer(renderer, { frameBufferType: THREE.HalfFloatType });
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new BloomEffect({ intensity: 0.85, luminanceThreshold: 0.62, luminanceSmoothing: 0.18, mipmapBlur: true, radius: 0.72 });
  const chroma = new ChromaticAberrationEffect({ offset: new THREE.Vector2(0.0006, 0.0004), radialModulation: true, modulationOffset: 0.35 });
  const vignette = new VignetteEffect({ darkness: 0.62, offset: 0.28 });
  const noise = new NoiseEffect({ blendFunction: BlendFunction.SOFT_LIGHT, premultiply: false });
  noise.blendMode.opacity.value = 0.22;
  const smaa = new SMAAEffect({ preset: SMAAPreset.HIGH });
  // convolution effects (chromatic aberration, SMAA) each need a pass of their own
  composer.addPass(new EffectPass(camera, bloom, vignette, noise));
  composer.addPass(new EffectPass(camera, chroma));
  composer.addPass(new EffectPass(camera, smaa));

  // camera marks per chapter (wide screens); portrait keeps the camera straighter
  const marks = (L) => L.wide ? [
    { p: [0, 0, 14], t: [0, 0, 0] },
    { p: [-0.9, 0.1, 12.6], t: [0.5, 0, -1] },
    { p: [0, 0.3, 14.6], t: [0, 0.25, 0] },
    { p: [0, -0.1, 13.4], t: [0, -0.35, 0] },
    { p: [0.5, 0, 11.6], t: [L.halfW * 0.22, 0, 0] },
    { p: [0, 0, 15], t: [0, 0, -2] },
  ] : [
    { p: [0, 0, 14], t: [0, 0, 0] },
    { p: [0, 0, 13.2], t: [0, 0.2, -1] },
    { p: [0, 0.2, 14.4], t: [0, 0.3, 0] },
    { p: [0, 0.1, 13.6], t: [0, 0.2, 0] },
    { p: [0, 0.3, 12.2], t: [0, 0.4, 0] },
    { p: [0, 0, 15], t: [0, 0, -2] },
  ];

  function resize(force = false) {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!force && w === size.w && h === size.h) return;
    const before = size.aspect;
    size = { w, h, aspect: w / Math.max(1, h) };
    const dpr = Math.min(window.devicePixelRatio || 1, size.aspect >= 1.05 ? 1.75 : 1.5);
    renderer.setPixelRatio(dpr);
    renderer.setSize(w, h, false);
    composer.setSize(w, h, false);
    camera.aspect = size.aspect;
    camera.updateProjectionMatrix();
    const wasWide = before >= 1.05;
    if (force || wasWide !== size.aspect >= 1.05 || Math.abs(before - size.aspect) / before > 0.12) {
      cards.rebuild(size.aspect);
      wave.fit(cards.layouts);
    }
  }

  const look = new THREE.Vector3();
  const mouseNdc = new THREE.Vector2(0, 0);
  const mouseWorld = new THREE.Vector3();
  const ray = new THREE.Raycaster();
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  let mouseOn = 0;

  function setPointer(x, y, inside) {
    mouseNdc.set((x / size.w) * 2 - 1, -(y / size.h) * 2 + 1);
    mouseOn = inside ? 1 : 0;
  }

  // state: { time, morph, scan, pick, playhead (0..1), wave, phone (0..1), pulse, shot, beatFrac, titleA }
  function render(state) {
    resize();
    const L = cards.layouts;
    const u = cards.uniforms;
    u.uTime.value = state.time;
    u.uMorph.value = state.morph;
    u.uScan.value = state.scan;
    u.uPick.value = state.pick;
    u.uPulse.value = state.pulse;
    const heroOn = 1 - smooth(state.morph / 0.7);
    core.visible = heroOn > 0.005;
    core.position.copy(L.galaxy);
    coreUniforms.uOn.value = heroOn;
    coreUniforms.uTime.value = state.time;

    // camera between chapter marks, plus a little parallax from the pointer
    const m = marks(L);
    const k = Math.min(m.length - 2, Math.floor(state.morph));
    const f = smooth(state.morph - k);
    const a = m[k], b = m[k + 1];
    camera.position.set(
      lerp(a.p[0], b.p[0], f) + mouseNdc.x * 0.35,
      lerp(a.p[1], b.p[1], f) + mouseNdc.y * 0.22,
      lerp(a.p[2], b.p[2], f),
    );
    look.set(lerp(a.t[0], b.t[0], f), lerp(a.t[1], b.t[1], f), lerp(a.t[2], b.t[2], f));
    camera.lookAt(look);
    camera.updateMatrixWorld();

    ray.setFromCamera(mouseNdc, camera);
    if (ray.ray.intersectPlane(plane, mouseWorld)) u.uMouse.value.copy(mouseWorld);
    u.uMouseOn.value = lerp(u.uMouseOn.value, mouseOn, 0.08);

    // the beat chapter's instruments
    const tl = L.timeline;
    const head = tl.x - tl.w / 2 + tl.w * state.playhead;
    u.uPlayhead.value = state.wave > 0.01 ? head : -99;
    wave.uniforms.uWave.value = state.wave;
    wave.uniforms.uPulse.value = state.pulse;
    wave.uniforms.uPlayhead.value = head;
    wave.blade.position.x = head;
    wave.bladeUniforms.uOn.value = state.wave;
    wave.bladeUniforms.uPulse.value = state.pulse;
    wave.group.visible = state.wave > 0.005;

    // the phone rises in chapter four and leaves in five
    const on = state.phone;
    phone.group.visible = on > 0.005;
    if (phone.group.visible) {
      const at = L.phoneAt;
      const s = (L.wide ? 1 : 0.78) * (0.6 + 0.4 * smooth(on));
      phone.group.scale.setScalar(s);
      phone.group.position.set(at.x, at.y + (1 - smooth(on)) * -1.2 + Math.sin(state.time * 0.8) * 0.05, at.z);
      phone.group.rotation.set(-0.06 + mouseNdc.y * -0.12, -0.32 + mouseNdc.x * 0.22 + (1 - on) * 0.6, 0.02);
      phone.uniforms.uOn.value = smooth((on - 0.35) / 0.65);
      phone.uniforms.uShot.value = state.shot;
      phone.uniforms.uBeatFrac.value = state.beatFrac;
      phone.uniforms.uTitleA.value = state.titleA;
      phone.uniforms.uTime.value = state.time;
      phone.bodyUniforms.uTime.value = state.time;
      phone.bodyUniforms.uOn.value = smooth(on * 1.4);
      phone.glowUniforms.uOn.value = smooth((on - 0.3) / 0.7);
    }
    composer.render();
  }

  // where a wall card sits on screen (for the scan's DOM tags)
  const tmp = new THREE.Vector3();
  function project(index, layout = 'l1') {
    const L = cards.layouts;
    const arr = L[layout];
    tmp.set(arr[index * 4], arr[index * 4 + 1], arr[index * 4 + 2]).project(camera);
    return { x: (tmp.x * 0.5 + 0.5) * size.w, y: (-tmp.y * 0.5 + 0.5) * size.h, z: tmp.z };
  }
  function projectPoint(v) {
    tmp.copy(v).project(camera);
    return { x: (tmp.x * 0.5 + 0.5) * size.w, y: (-tmp.y * 0.5 + 0.5) * size.h };
  }

  resize(true);
  return {
    renderer, render, setPointer, project, projectPoint, resize,
    get layouts() { return cards.layouts; },
    frame: () => frame(size.aspect),
    dispose() { renderer.dispose(); },
  };
}
