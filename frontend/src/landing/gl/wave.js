// The beat chapter's instruments: the score's waveform over the timeline, beat ticks, and the playhead blade.
import * as THREE from 'three';

const barVertex = /* glsl */ `
  attribute vec2 aBar; // x, amplitude
  uniform float uWave;
  uniform float uPulse;
  uniform float uY;
  uniform float uWidth;
  uniform float uPlayhead;
  varying float vPlayed;
  varying float vX;
  varying float vV;
  void main() {
    float h = (0.06 + aBar.y * (1.25 + uPulse * 0.45)) * uWave;
    vec3 p = vec3(aBar.x + position.x * uWidth, uY + position.y * h, 0.4);
    vPlayed = step(aBar.x, uPlayhead);
    vX = aBar.x;
    vV = position.y;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }
`;
const barFragment = /* glsl */ `
  uniform float uWave;
  uniform float uSpan;
  uniform float uX;
  varying float vPlayed;
  varying float vX;
  varying float vV;
  void main() {
    float u = (vX - uX) / uSpan + 0.5;
    vec3 a = vec3(1.0, 0.70, 0.28), b = vec3(1.0, 0.42, 0.24), c = vec3(1.0, 0.18, 0.49), d = vec3(0.56, 0.36, 1.0);
    vec3 sun = u < 0.33 ? mix(a, b, u / 0.33) : (u < 0.66 ? mix(b, c, (u - 0.33) / 0.33) : mix(c, d, (u - 0.66) / 0.34));
    vec3 col = mix(vec3(0.16, 0.16, 0.2), sun * 0.85, vPlayed);
    col *= 0.75 + 0.25 * (1.0 - abs(vV) * 2.0);
    gl_FragColor = vec4(col * uWave, 1.0);
    #include <colorspace_fragment>
  }
`;

const bladeFragment = /* glsl */ `
  uniform float uOn;
  uniform float uPulse;
  varying vec2 vUv;
  void main() {
    float core = 1.0 - smoothstep(0.0, 0.08, abs(vUv.x - 0.5));
    float glow = 1.0 - smoothstep(0.0, 0.5, abs(vUv.x - 0.5));
    float fade = smoothstep(0.0, 0.08, vUv.y) * smoothstep(1.0, 0.85, vUv.y);
    vec3 c = vec3(1.0, 0.86, 0.72) * core * 0.95 + vec3(1.0, 0.35, 0.3) * glow * glow * (0.35 + uPulse * 0.4);
    gl_FragColor = vec4(c * fade * uOn, 1.0);
    #include <colorspace_fragment>
  }
`;
const bladeVertex = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

// a believable score envelope: kicks on the beat, a swell into the drop, breath in the bridge
function envelope(n) {
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / n;
    const beat = (i % 6) === 0 ? 1 : (i % 3 === 0 ? 0.6 : 0.3);
    const swell = 0.45 + 0.55 * Math.sin(Math.PI * Math.min(1, t * 1.25)) * (t > 0.55 && t < 0.62 ? 0.35 : 1);
    const noise = 0.75 + 0.25 * Math.sin(i * 12.9898) * Math.sin(i * 4.1414);
    out[i] = Math.min(1, beat * swell * noise);
  }
  return out;
}

export function createWave() {
  const group = new THREE.Group();
  const n = 96;
  const geo = new THREE.InstancedBufferGeometry();
  const base = new THREE.PlaneGeometry(1, 1);
  geo.index = base.index;
  geo.setAttribute('position', base.getAttribute('position'));
  const bars = new Float32Array(n * 2);
  geo.setAttribute('aBar', new THREE.InstancedBufferAttribute(bars, 2));
  geo.instanceCount = n;
  const uniforms = {
    uWave: { value: 0 }, uPulse: { value: 0 }, uY: { value: 0 }, uWidth: { value: 0.05 },
    uPlayhead: { value: -99 }, uSpan: { value: 10 }, uX: { value: 0 },
  };
  const mesh = new THREE.Mesh(geo, new THREE.ShaderMaterial({ vertexShader: barVertex, fragmentShader: barFragment, uniforms }));
  mesh.frustumCulled = false;
  group.add(mesh);

  const bladeUniforms = { uOn: { value: 0 }, uPulse: { value: 0 } };
  const blade = new THREE.Mesh(new THREE.PlaneGeometry(0.34, 1),
    new THREE.ShaderMaterial({ vertexShader: bladeVertex, fragmentShader: bladeFragment, uniforms: bladeUniforms }));
  blade.frustumCulled = false;
  group.add(blade);

  const amp = envelope(n);
  function fit(layouts) {
    const { w, y, x } = layouts.timeline;
    const span = w;
    for (let i = 0; i < n; i++) {
      bars[i * 2] = x - span / 2 + (i + 0.5) * (span / n);
      bars[i * 2 + 1] = amp[i];
    }
    geo.attributes.aBar.needsUpdate = true;
    uniforms.uWidth.value = (span / n) * 0.55;
    uniforms.uSpan.value = span;
    uniforms.uX.value = x;
    uniforms.uY.value = y + (layouts.wide ? 1.85 : 1.35);
    const top = uniforms.uY.value + 1.0;
    const bottom = y - 0.62;
    blade.scale.y = top - bottom;
    blade.position.set(-99, (top + bottom) / 2, 0.45);
  }
  return { group, uniforms, blade, bladeUniforms, fit };
}
