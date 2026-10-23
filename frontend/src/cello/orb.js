// Cello's body: a being of light, not a button.
// - A glass sphere holding a living plasma (domain-warped noise that never repeats).
// - An inner core like an iris: it turns towards whoever or whatever has its attention (you while you speak, the shot
//   it is working on while it acts). It widens to listen, tightens and spins to think, and pulses with its own voice.
// - The rim moves with the actual sound, eight bands of it, so speech has a shape and not just a size.
// - Sparks orbit it while its hands are busy.
// - Small gestures: a nod when it has understood, a flicker when it notices something.
// Colour says the state at a glance; every value eases to the next, so it never steps.
// The voice pipeline only touches: phase, look(x, y), level, bands, nod(), notice(), frame(dt).

const PHASES = {
  sleep: { c1: [0.42, 0.45, 0.62], c2: [0.06, 0.06, 0.14], swirl: 0.05, dim: 0.55, ring: 0, dilate: 0.25, spin: 0 },
  idle: { c1: [1.0, 0.62, 0.42], c2: [0.42, 0.08, 0.36], swirl: 0.18, dim: 0.95, ring: 0, dilate: 0.45, spin: 0.05 },
  hearing: { c1: [0.45, 0.95, 1.0], c2: [0.06, 0.22, 0.62], swirl: 0.35, dim: 1.1, ring: 0, dilate: 0.85, spin: 0.02 },
  thinking: { c1: [0.78, 0.6, 1.0], c2: [0.2, 0.05, 0.55], swirl: 1.1, dim: 1.05, ring: 0.25, dilate: 0.3, spin: 1.0 },
  speaking: { c1: [1.0, 0.93, 0.85], c2: [0.85, 0.32, 0.25], swirl: 0.5, dim: 1.2, ring: 0, dilate: 0.6, spin: 0.15 },
  acting: { c1: [1.0, 0.74, 0.3], c2: [0.6, 0.18, 0.02], swirl: 0.8, dim: 1.15, ring: 1, dilate: 0.5, spin: 0.55 },
  halted: { c1: [1.0, 0.35, 0.42], c2: [0.32, 0.0, 0.06], swirl: 0.1, dim: 1.0, ring: 0, dilate: 0.2, spin: 0 },
  offline: { c1: [0.5, 0.52, 0.6], c2: [0.07, 0.08, 0.12], swirl: 0.08, dim: 0.6, ring: 0, dilate: 0.3, spin: 0 },
};

const VERT = 'attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }';
const FRAG = `
precision highp float;
uniform vec2 uRes; uniform float uT; uniform vec3 uC1; uniform vec3 uC2;
uniform float uLevel; uniform float uSwirl; uniform float uRing; uniform float uDim; uniform vec2 uLook;
uniform float uDilate; uniform float uSpin; uniform float uNod; uniform float uNotice; uniform float uBands[8];
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y); }
float fbm(vec2 p){ float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; } return v; }
float band(float ang){
  float v = 0.0;
  for (int i = 0; i < 8; i++) { float k = float(i + 2); v += uBands[i] * sin(ang * k + uT * (1.2 + 0.3 * float(i))) / k; }
  return v;
}
void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
  uv.y *= 1.0 + 0.09 * uNod;                       // a nod: a quick soft squash
  float r = length(uv); float ang = atan(uv.y, uv.x);
  float R = 0.27 + 0.035 * uLevel + 0.005 * sin(uT * 1.3) + 0.06 * band(ang);
  vec3 col = vec3(0.0); float a = 0.0;
  float d = r / R;
  if (d < 1.0) {
    float z = sqrt(1.0 - d * d);
    vec3 n = vec3(uv / R, z);
    float t = uT * (0.1 + uSwirl);
    vec2 q = n.xy * 1.6 + vec2(t * 0.5, -t * 0.4);
    float warp = fbm(q * 1.4 + vec2(t, -t * 0.6));
    float f = fbm(q + warp * 1.6 + uLook * 0.3);
    float fres = pow(1.0 - z, 2.2);
    vec3 base = mix(uC2, uC1, smoothstep(0.2, 0.9, f));
    col = base * (0.25 + 0.9 * f) + uC1 * fres * 1.3;
    // the core: an iris that looks where its attention is
    vec2 c = n.xy - uLook * 0.32;
    float cr = length(c); float ca = atan(c.y, c.x) + uT * uSpin;
    float iris = 0.2 + 0.2 * uDilate + 0.05 * uLevel;
    float fibres = 0.55 + 0.45 * sin(ca * 18.0 + fbm(c * 6.0 + uT * 0.3) * 6.0);
    float ring = smoothstep(iris + 0.12, iris, cr) * smoothstep(iris * 0.35, iris * 0.75, cr);
    col += mix(uC1, vec3(1.0), 0.35) * ring * fibres * (0.55 + 0.9 * uLevel);
    float pupil = smoothstep(iris * 0.42, iris * 0.18, cr);
    col = mix(col, vec3(1.0, 0.98, 0.95) * (0.9 + 0.6 * uLevel), pupil * (0.55 + 0.4 * uDilate));
    col += vec3(1.0) * pow(max(dot(n, normalize(vec3(-0.45, 0.55, 0.8))), 0.0), 40.0) * 0.6;   // glass glint
    a = smoothstep(1.0, 0.96, d);
  }
  float halo = exp(-max(r - R, 0.0) * (11.0 - 4.0 * uLevel)) * (0.32 + 0.85 * uLevel + 0.6 * uNotice);
  halo *= smoothstep(0.5, 0.36, r);                 // gone well before the canvas's edge: no square
  col += uC1 * halo * (1.0 - a);
  a = max(a, halo * 0.75);
  // sparks orbiting while it works
  for (int i = 0; i < 3; i++) {
    float fi = float(i);
    float rr = R + 0.07 + 0.035 * fi;
    float sa = ang - uT * (1.4 + fi * 0.5) * (mod(fi, 2.0) < 1.0 ? 1.0 : -1.0);
    float spark = pow(max(0.0, cos(sa * (3.0 + fi))), 40.0) * smoothstep(0.012, 0.0, abs(r - rr));
    col += mix(uC1, vec3(1.0), 0.5) * spark * uRing * 2.2;
    a = max(a, spark * uRing);
  }
  col *= uDim;
  a = clamp(a, 0.0, 1.0);
  gl_FragColor = vec4(min(col, vec3(1.0)) * a, a);   // premultiplied: no pale box round it
}`;

export class Orb {
  constructor(canvas) {
    this.canvas = canvas;
    this.t = 0;
    this.phase = 'sleep';
    this.level = 0; // 0..1, whoever is talking
    this.bands = new Float32Array(8);
    this.smooth = new Float32Array(8);
    this.cur = JSON.parse(JSON.stringify(PHASES.sleep));
    this.lookTo = [0, 0];
    this.lookAt = [0, 0];
    this.nodT = 0;
    this.noticeT = 0;
    this.gl = canvas.getContext('webgl', { premultipliedAlpha: true, alpha: true, antialias: true });
    if (this.gl) this.init();
    else this.ctx = canvas.getContext('2d');
    this.resize();
  }

  init() {
    const gl = this.gl;
    const sh = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    this.u = {};
    for (const n of ['uRes', 'uT', 'uC1', 'uC2', 'uLevel', 'uSwirl', 'uRing', 'uDim', 'uLook', 'uDilate', 'uSpin', 'uNod',
      'uNotice', 'uBands']) this.u[n] = gl.getUniformLocation(prog, n);
    gl.clearColor(0, 0, 0, 0);
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const w = Math.max(1, Math.round(this.canvas.clientWidth * dpr));
    const h = Math.max(1, Math.round(this.canvas.clientHeight * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
    if (this.gl) this.gl.viewport(0, 0, w, h);
  }

  look(x, y) { this.lookTo = [Math.max(-1, Math.min(1, x)), Math.max(-1, Math.min(1, y))]; }
  nod() { this.nodT = 1; }
  notice() { this.noticeT = 1; }

  frame(dt) {
    dt = Math.min(dt || 0.016, 0.05);
    this.t += dt;
    const target = PHASES[this.phase] || PHASES.idle;
    const k = 1 - Math.exp(-dt * 4.5);
    const c = this.cur;
    for (let i = 0; i < 3; i += 1) { c.c1[i] += (target.c1[i] - c.c1[i]) * k; c.c2[i] += (target.c2[i] - c.c2[i]) * k; }
    for (const key of ['swirl', 'ring', 'dim', 'dilate', 'spin']) c[key] += (target[key] - c[key]) * k;
    for (let i = 0; i < 8; i += 1) this.smooth[i] += (this.bands[i] - this.smooth[i]) * (1 - Math.exp(-dt * 18));
    for (let i = 0; i < 2; i += 1) this.lookAt[i] += (this.lookTo[i] - this.lookAt[i]) * (1 - Math.exp(-dt * 6));
    this.nodT = Math.max(0, this.nodT - dt * 2.2);
    this.noticeT = Math.max(0, this.noticeT - dt * 1.4);
    const nod = Math.sin((1 - this.nodT) * Math.PI * 2) * this.nodT;
    if (this.gl) {
      const gl = this.gl;
      const u = this.u;
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(u.uRes, this.canvas.width, this.canvas.height);
      gl.uniform1f(u.uT, this.t);
      gl.uniform3fv(u.uC1, c.c1);
      gl.uniform3fv(u.uC2, c.c2);
      gl.uniform1f(u.uLevel, this.level);
      gl.uniform1f(u.uSwirl, c.swirl);
      gl.uniform1f(u.uRing, c.ring);
      gl.uniform1f(u.uDim, c.dim);
      gl.uniform2fv(u.uLook, this.lookAt);
      gl.uniform1f(u.uDilate, c.dilate);
      gl.uniform1f(u.uSpin, c.spin);
      gl.uniform1f(u.uNod, nod);
      gl.uniform1f(u.uNotice, this.noticeT);
      gl.uniform1fv(u.uBands, this.smooth);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    } else {
      const g = this.ctx;
      const { width: w, height: h } = this.canvas;
      const rgb = (v, al = 1) => `rgba(${v.map((x) => Math.round(Math.min(1, x * c.dim) * 255)).join(',')},${al})`;
      const R = Math.min(w, h) * (0.27 + 0.035 * this.level);
      g.clearRect(0, 0, w, h);
      const body = g.createRadialGradient(w / 2 - R * 0.3, h / 2 - R * 0.35, R * 0.1, w / 2, h / 2, R);
      body.addColorStop(0, rgb([1, 1, 1]));
      body.addColorStop(0.35, rgb(c.c1));
      body.addColorStop(1, rgb(c.c2));
      g.fillStyle = body;
      g.beginPath();
      g.arc(w / 2, h / 2, R, 0, Math.PI * 2);
      g.fill();
    }
  }
}
