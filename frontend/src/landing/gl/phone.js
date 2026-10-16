// The payoff: a phone that plays the finished edit. Its body is a rounded box with a cheap studio light (rim +
// moving sheen); its screen cuts between tiles of the same footage on the beat, graded, with a title card.
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js';
import { phoneScreenVertex, phoneScreenFragment } from './shaders.js';

const bodyVertex = /* glsl */ `
  varying vec3 vN;
  varying vec3 vView;
  varying vec3 vPos;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vN = normalize(normalMatrix * normal);
    vView = normalize(-mv.xyz);
    vPos = position;
    gl_Position = projectionMatrix * mv;
  }
`;
const bodyFragment = /* glsl */ `
  uniform float uTime;
  uniform float uOn;
  varying vec3 vN;
  varying vec3 vView;
  varying vec3 vPos;
  void main() {
    float fres = pow(1.0 - max(dot(vN, vView), 0.0), 3.0);
    vec3 base = vec3(0.018, 0.018, 0.026);
    vec3 rim = mix(vec3(1.0, 0.5, 0.3), vec3(0.62, 0.42, 1.0), smoothstep(-2.0, 2.0, vPos.y));
    float sheen = smoothstep(0.94, 1.0, sin(vPos.y * 1.2 - vPos.x * 0.8 + uTime * 0.5) * 0.5 + 0.5);
    float edgeLine = smoothstep(0.86, 1.0, fres);
    vec3 c = base + rim * fres * 0.38 + rim * edgeLine * 0.5 + vec3(sheen) * 0.06 * (1.0 - fres);
    gl_FragColor = vec4(c * uOn, 1.0);
    #include <colorspace_fragment>
  }
`;

function titleTexture() {
  const W = 540, H = 1170;
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');
  const draw = () => {
    ctx.clearRect(0, 0, W, H);
    const g = ctx.createLinearGradient(0, H * 0.3, 0, H * 0.75);
    g.addColorStop(0, 'rgba(0,0,0,0)');
    g.addColorStop(0.5, 'rgba(0,0,0,0.28)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    ctx.font = "800 150px Bricolage, sans-serif";
    ctx.letterSpacing = '22px';
    ctx.fillText('SALT', W / 2 + 11, H * 0.5);
    ctx.letterSpacing = '0px';
    ctx.font = "italic 46px Serif, serif";
    ctx.fillStyle = 'rgba(255,255,255,0.92)';
    ctx.fillText('a film by you', W / 2, H * 0.5 + 70);
    ctx.font = "500 22px Mono, monospace";
    ctx.letterSpacing = '6px';
    ctx.fillStyle = 'rgba(255,255,255,0.7)';
    ctx.fillText('MADE WITH FLICELLO', W / 2, H - 70);
  };
  draw();
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  // redraw once the brand fonts are in, so the first frames don't show a fallback face
  if (document.fonts) {
    Promise.all(["800 150px Bricolage", "italic 46px Serif", "500 22px Mono"].map((f) => document.fonts.load(f)))
      .then(() => { draw(); tex.needsUpdate = true; })
      .catch(() => {});
  }
  return tex;
}

export function createPhone(videoTexture) {
  const group = new THREE.Group();
  const W = 1.9, H = 4.08, D = 0.24;
  const body = new THREE.Mesh(
    new RoundedBoxGeometry(W, H, D, 6, 0.27),
    new THREE.ShaderMaterial({ vertexShader: bodyVertex, fragmentShader: bodyFragment,
      uniforms: { uTime: { value: 0 }, uOn: { value: 1 } } }),
  );
  group.add(body);

  // the edit: 16 shots, each a tile and a camera move, cut on the beat
  const shots = [4, 0, 6, 2, 9, 11, 5, 3, 8, 1, 7, 10, 4, 6, 0, 9];
  const moves = shots.map((_, i) => [0, 1, 2, 0, 1, 0, 2, 1][i % 8]);
  const screenUniforms = {
    uVideo: { value: videoTexture },
    uTitle: { value: titleTexture() },
    uShot: { value: 0 },
    uBeatFrac: { value: 0 },
    uOn: { value: 0 },
    uTitleA: { value: 1 },
    uTime: { value: 0 },
    uShots: { value: shots },
    uMoves: { value: moves },
  };
  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(W - 0.1, (W - 0.1) * (19.5 / 9)),
    new THREE.ShaderMaterial({ vertexShader: phoneScreenVertex, fragmentShader: phoneScreenFragment, uniforms: screenUniforms }),
  );
  screen.position.z = D / 2 + 0.003;
  group.add(screen);

  // the screen's light on the dark around it
  const glowMat = new THREE.ShaderMaterial({
    uniforms: { uOn: { value: 0 } },
    vertexShader: `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `uniform float uOn; varying vec2 vUv;
      void main() {
        vec2 p = (vUv - 0.5) * vec2(1.0, 1.25);
        float g = exp(-dot(p, p) * 12.0) * smoothstep(0.5, 0.3, length(vUv - 0.5));
        vec3 c = mix(vec3(1.0, 0.42, 0.25), vec3(0.6, 0.3, 1.0), vUv.y) * g * 0.3 * uOn;
        gl_FragColor = vec4(c, 1.0);
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const glow = new THREE.Mesh(new THREE.PlaneGeometry(7, 9), glowMat);
  glow.position.z = -0.4;
  group.add(glow);
  group.visible = false;

  return {
    group,
    body,
    screen,
    uniforms: screenUniforms,
    bodyUniforms: body.material.uniforms,
    glowUniforms: glowMat.uniforms,
  };
}
