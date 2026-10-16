// GLSL for the landing page. One instanced card mesh plays the whole story: every card carries its position in
// each chapter's layout, and uMorph (scroll) blends between them with a per-card stagger. Cards are opaque
// (corners are discarded, fades darken toward the stage colour), so no sorting is ever needed.

export const COMMON = /* glsl */ `
  vec3 rotX(vec3 p, float a) { float c = cos(a), s = sin(a); return vec3(p.x, c * p.y - s * p.z, s * p.y + c * p.z); }
  vec3 rotY(vec3 p, float a) { float c = cos(a), s = sin(a); return vec3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z); }
  vec3 rotZ(vec3 p, float a) { float c = cos(a), s = sin(a); return vec3(c * p.x - s * p.y, s * p.x + c * p.y, p.z); }
  float ease(float x) { x = clamp(x, 0.0, 1.0); return x * x * x * (x * (x * 6.0 - 15.0) + 10.0); }
  float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

  // the film look used wherever a card is "edited": lifted blacks, soft S-curve, teal shadows, warm highlights
  vec3 grade(vec3 c) {
    float l = dot(c, vec3(0.299, 0.587, 0.114));
    c = mix(vec3(l), c, 1.22);
    c = c * c * (3.0 - 2.0 * c) * 0.92 + c * 0.08;
    vec3 shadowTint = vec3(-0.02, 0.025, 0.05);
    vec3 lightTint = vec3(0.06, 0.02, -0.04);
    c += shadowTint * (1.0 - smoothstep(0.0, 0.55, l)) + lightTint * smoothstep(0.45, 1.0, l);
    return clamp(c * 1.04 - 0.012, 0.0, 1.0);
  }
  // raw phone footage: flat and a little grey, which is what makes the edited version land
  vec3 raw(vec3 c) {
    float l = dot(c, vec3(0.299, 0.587, 0.114));
    return mix(vec3(l), c, 0.9) * 0.94 + 0.02;
  }
  vec3 toDisplay(vec3 c) { return pow(max(c, 0.0), vec3(1.0 / 2.2)); }
  vec3 toLinear(vec3 c) { return pow(max(c, 0.0), vec3(2.2)); }
  vec2 tileUV(float tile, vec2 uv) {
    float col = mod(tile, 4.0);
    float row = floor(tile / 4.0);
    // the mosaic is 4 x 3 tiles; video textures are flipped so row 0 is at the top of v
    return vec2((col + uv.x) / 4.0, 1.0 - (row + 1.0 - uv.y) / 3.0);
  }
`;

export const cardVertex = /* glsl */ `
  ${COMMON}
  attribute float aTile;
  attribute vec4 aSeed;
  attribute vec4 aL0;   // galaxy: radius, angle, height, scale
  attribute vec4 aL1;   // wall: x, y, z, scale
  attribute vec4 aL2;   // story: x, y, z, scale
  attribute vec4 aL3;   // timeline: x, y, z, width
  attribute vec4 aL4;   // phone: x, y, z, scale
  attribute vec4 aL5;   // ambient: x, y, z, scale
  attribute vec4 aVis;  // brightness in wall, story, timeline, phone
  attribute vec4 aFlag; // kept, cluster (0..2, -1), timeline slot (-1 none), wall column (0..1)
  attribute float aR;   // story fan angle

  uniform float uTime;
  uniform float uMorph;
  uniform vec3 uGalaxy;     // galaxy centre
  uniform vec2 uTilt;       // galaxy tilt (x, z)
  uniform vec3 uMouse;      // pointer on the z = 0 plane
  uniform float uMouseOn;
  uniform float uWallRot;
  uniform float uTimelineH;
  uniform float uPulse;     // beat pulse 0..1
  uniform float uPick;      // story: 0 = three pitches, 1 = one chosen
  uniform vec3 uCentre0;    // story cluster centres (left, chosen, right on wide screens)
  uniform vec3 uCentre1;
  uniform vec3 uCentre2;
  uniform vec3 uFront;      // where the chosen pitch comes forward to

  varying vec2 vUv;
  varying float vTile;
  varying float vVis;
  varying vec4 vFlag;
  varying float vDepth;
  varying float vSeed;
  varying vec3 vWorld;
  varying float vLayout;
  varying vec2 vScale;

  vec3 galaxy(out vec2 scl, out vec3 rot) {
    float r = aL0.x;
    float th = aL0.y + uTime * 0.05 * (1.8 / (0.7 + r));
    vec3 p = vec3(r * cos(th), aL0.z + sin(uTime * 0.4 + aSeed.x * 6.28) * 0.08, r * sin(th));
    p = rotX(p, uTilt.x);
    p = rotZ(p, uTilt.y);
    p += uGalaxy;
    scl = vec2(aL0.w);
    rot = vec3((aSeed.y - 0.5) * 0.5, (aSeed.z - 0.5) * 0.7 - th * 0.05, (aSeed.w - 0.5) * 0.35);
    return p;
  }

  vec3 place(float k, out vec2 scl, out vec3 rot, out float vis) {
    float drift = sin(uTime * 0.6 + aSeed.x * 6.283) * 0.04;
    if (k < 0.5) { vis = 1.0; return galaxy(scl, rot); }
    if (k < 1.5) { scl = vec2(aL1.w); rot = vec3(0.0, uWallRot, 0.0); vis = aVis.x; return aL1.xyz + vec3(0.0, drift * 0.3, 0.0); }
    if (k < 2.5) {
      bool chosen = aFlag.y > 0.5 && aFlag.y < 1.5;
      vec3 p = aL2.xyz;
      float v = aVis.y;
      if (aFlag.y >= -0.5) {
        // the chosen pitch comes forward, the other two step back
        vec3 centre = aFlag.y < 0.5 ? uCentre0 : (chosen ? uCentre1 : uCentre2);
        vec3 rel = p - centre;
        vec3 back = centre + (centre - uFront) * 0.35 + rel * 0.8 + vec3(0.0, 0.0, -3.0);
        vec3 front = uFront + rel * 1.25;
        p = mix(p, chosen ? front : back, ease(uPick));
        v = mix(v, chosen ? 1.0 : 0.3, ease(uPick));
      }
      scl = vec2(aL2.w);
      rot = vec3(0.0, 0.0, aR);
      vis = v;
      return p + vec3(0.0, drift, 0.0);
    }
    if (k < 3.5) { scl = vec2(aL3.w, uTimelineH); rot = vec3(0.0); vis = aVis.z; return aL3.xyz; }
    if (k < 4.5) { scl = vec2(aL4.w); rot = vec3(0.0); vis = aVis.w; return aL4.xyz; }
    scl = vec2(aL5.w); rot = vec3((aSeed.y - 0.5) * 0.6, (aSeed.z - 0.5) * 0.9, (aSeed.w - 0.5) * 0.6); vis = 0.16;
    return aL5.xyz + vec3(sin(uTime * 0.13 + aSeed.y * 9.0), cos(uTime * 0.11 + aSeed.z * 9.0), 0.0) * 0.35;
  }

  void main() {
    float k0 = floor(uMorph);
    float f = uMorph - k0;
    // stagger: each card leaves a little earlier or later than its neighbours
    float t = ease(clamp(f * 1.6 - aSeed.x * 0.6, 0.0, 1.0));
    vec2 sA; vec3 rA; float vA;
    vec2 sB; vec3 rB; float vB;
    vec3 pA = place(k0, sA, rA, vA);
    vec3 pB = place(k0 + 1.0, sB, rB, vB);
    // a lift on the way between layouts so cards arc rather than slide
    vec3 p = mix(pA, pB, t) + vec3(0.0, 0.0, sin(t * 3.14159) * (0.6 + aSeed.y * 1.4));
    vec2 scl = mix(sA, sB, t);
    vec3 rot = mix(rA, rB, t) + vec3(0.0, 0.0, sin(t * 3.14159) * (aSeed.z - 0.5) * 0.8);
    float vis = mix(vA, vB, t);

    // the pointer parts the galaxy
    float near = 1.0 - smoothstep(0.0, 2.6, distance(p.xy, uMouse.xy));
    float g = (1.0 - smoothstep(0.0, 0.6, uMorph)) * uMouseOn;
    vec2 away = normalize(p.xy - uMouse.xy + 1e-4);
    p.xy += away * near * near * 1.1 * g;
    p.z += near * 0.8 * g;
    rot.y += (p.x - uMouse.x) * near * 0.25 * g;

    // a breath on the beat for cards that are on screen to be cut
    float onBeat = uPulse * step(2.5, uMorph) * step(uMorph, 3.6) * step(-0.5, aFlag.z);
    scl *= 1.0 + onBeat * 0.05;

    vec3 local = position * vec3(scl * vec2(0.5625, 1.0), 1.0);
    local = rotZ(rotY(rotX(local, rot.x), rot.y), rot.z);
    vec4 world = modelMatrix * vec4(local + p, 1.0);
    vec4 mv = viewMatrix * world;
    gl_Position = projectionMatrix * mv;

    vUv = uv;
    vTile = aTile;
    vVis = vis;
    vFlag = aFlag;
    vDepth = -mv.z;
    vSeed = aSeed.w;
    vWorld = world.xyz;
    vLayout = uMorph;
    vScale = scl * vec2(0.5625, 1.0);
  }
`;

export const cardFragment = /* glsl */ `
  ${COMMON}
  uniform sampler2D uVideo;
  uniform float uTime;
  uniform float uScan;      // wall: beam position along the columns (0..1), < 0 before it starts
  uniform float uPlayhead;  // timeline: playhead x in world units
  uniform float uPulse;
  uniform vec3 uBg;
  uniform vec3 uGalaxyF;

  varying vec2 vUv;
  varying float vTile;
  varying float vVis;
  varying vec4 vFlag;
  varying float vDepth;
  varying float vSeed;
  varying vec3 vWorld;
  varying float vLayout;
  varying vec2 vScale;

  float roundRect(vec2 p, vec2 hb, float r) {
    vec2 q = abs(p) - hb + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
  }

  void main() {
    // rounded corners in card units, so wide timeline strips keep round ends
    vec2 p = (vUv - 0.5) * vScale;
    float radius = min(vScale.x, vScale.y) * 0.075;
    float d = roundRect(p, vScale * 0.5, radius);
    if (d > 0.0) discard;

    // timeline strips show the middle of the tile, not a squashed whole
    vec2 uv = vUv;
    float aspect = vScale.x / vScale.y / 0.5625;
    if (aspect > 1.0) uv.y = 0.5 + (uv.y - 0.5) / aspect;
    vec3 c = toDisplay(texture2D(uVideo, tileUV(vTile, uv)).rgb);

    // how "edited" this card looks: raw in the galaxy and on the wall, graded once it is in a story
    float edited = smoothstep(1.6, 2.4, vLayout) * step(-0.5, vFlag.y);
    c = toLinear(mix(raw(c), grade(c), edited));

    float vis = vVis;
    vec3 ring = vec3(0.0);
    float edge = smoothstep(-0.035, -0.004, d);

    // the galaxy has a light at its heart: cards near it warm up and catch a rim, far ones fall into the dark
    float gal = 1.0 - smoothstep(0.0, 0.7, vLayout);
    if (gal > 0.0) {
      float dist = distance(vWorld, uGalaxyF);
      float core = exp(-dist * dist / 9.0);
      vec3 warm = mix(vec3(1.0, 0.58, 0.32), vec3(1.0, 0.3, 0.58), clamp(dist / 7.0, 0.0, 1.0));
      c = mix(c, c * (0.55 + 0.5 * core) + warm * core * 0.06, gal);
      ring += edge * warm * (0.12 + core * 0.55) * gal;
    }

    // the scan: a beam crosses the wall; behind it every card shows its verdict
    float onWall = 1.0 - smoothstep(0.0, 0.35, abs(vLayout - 1.0));
    if (onWall > 0.0 && uScan > -0.5) {
      float u = vFlag.w;
      float beam = exp(-pow((u - uScan) * 14.0, 2.0));
      float judged = smoothstep(0.0, 0.04, uScan - u);
      float kept = vFlag.x;
      c += beam * 0.35 * onWall;
      float dim = mix(1.0, mix(0.32, 1.06, kept), judged * onWall);
      vis *= dim;
      vec3 sun = mix(vec3(1.0, 0.42, 0.24), vec3(1.0, 0.18, 0.49), vUv.x);
      ring += edge * judged * kept * onWall * sun * 1.1;
      ring += edge * judged * (1.0 - kept) * onWall * vec3(0.7, 0.12, 0.16) * 0.5;
      // the cut ones lose their colour
      float l = dot(c, vec3(0.299, 0.587, 0.114));
      c = mix(c, vec3(l), judged * (1.0 - kept) * onWall * 0.85);
    }

    // the timeline: the playhead lights the shot it is cutting through
    float onTimeline = 1.0 - smoothstep(0.0, 0.4, abs(vLayout - 3.0));
    if (onTimeline > 0.0 && vFlag.z > -0.5) {
      float hit = 1.0 - smoothstep(0.0, vScale.x * 0.5 + 0.02, abs(vWorld.x - uPlayhead));
      c += hit * 0.22 * onTimeline;
      vec3 sun = mix(vec3(1.0, 0.70, 0.28), vec3(0.56, 0.36, 1.0), vUv.x);
      ring += edge * hit * onTimeline * sun * (1.0 + uPulse * 0.6);
    }

    // depth: far cards sink into the dark
    float fog = smoothstep(9.0, 26.0, vDepth);
    vec3 col = c * vis;
    col = mix(col, uBg, fog * 0.85);
    col += ring;
    gl_FragColor = vec4(col, 1.0);
    #include <colorspace_fragment>
  }
`;

// the phone's screen: a live edit of the same footage, cut on the beat with a film grade
export const phoneScreenVertex = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

export const phoneScreenFragment = /* glsl */ `
  ${COMMON}
  uniform sampler2D uVideo;
  uniform sampler2D uTitle;
  uniform float uShot;      // current shot index (float: beat count)
  uniform float uBeatFrac;  // 0..1 through the current beat
  uniform float uOn;        // screen power 0..1
  uniform float uTitleA;
  uniform float uTime;
  uniform float uShots[16];
  uniform float uMoves[16];
  varying vec2 vUv;

  float roundRect(vec2 p, vec2 hb, float r) {
    vec2 q = abs(p) - hb + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
  }

  vec3 shot(float i, vec2 uv, float frac) {
    int k = int(mod(i, 16.0));
    float tile = 0.0; float move = 0.0;
    for (int j = 0; j < 16; j++) { if (j == k) { tile = uShots[j]; move = uMoves[j]; } }
    // each shot has its own camera move: push in, drift, or a punch on the cut
    vec2 c = uv - 0.5;
    if (move < 0.5) c *= 1.0 - frac * 0.08;
    else if (move < 1.5) c = c * 0.94 + vec2(frac * 0.05 - 0.025, 0.0);
    else c *= 0.9 + 0.1 * smoothstep(0.0, 0.35, frac);
    return toDisplay(texture2D(uVideo, tileUV(tile, c + 0.5)).rgb);
  }

  void main() {
    vec2 p = vUv - 0.5;
    float d = roundRect(p * vec2(9.0 / 19.5, 1.0), vec2(9.0 / 19.5, 1.0) * 0.5, 0.06);
    if (d > 0.0) discard;
    float frac = uBeatFrac;
    vec2 uv = vUv;
    // a whip on every fourth cut: the outgoing shot smears out sideways
    float whip = mod(uShot, 4.0) < 0.5 ? 1.0 - smoothstep(0.0, 0.18, frac) : 0.0;
    vec3 c = vec3(0.0);
    if (whip > 0.0) {
      for (int s = 0; s < 6; s++) {
        float o = (float(s) / 5.0 - 0.5) * 0.18 * whip;
        c += shot(uShot, uv + vec2(o, 0.0), frac);
      }
      c /= 6.0;
    } else {
      c = shot(uShot, uv, frac);
    }
    c = grade(c);
    // a flash frame on the cut, a light leak that wanders
    c += (1.0 - smoothstep(0.0, 0.08, frac)) * 0.18;
    float leak = smoothstep(0.75, 0.0, distance(vUv, vec2(0.15 + 0.1 * sin(uTime * 0.3), 0.85)));
    c += leak * vec3(1.0, 0.45, 0.2) * 0.22;
    // vignette and grain
    c *= 1.0 - 0.45 * pow(length(p * vec2(1.2, 0.9)), 2.0);
    c += (hash(vUv * 900.0 + uTime) - 0.5) * 0.05;
    // the title card sits over the first beats
    vec4 t = texture2D(uTitle, vUv);
    c = mix(c, toDisplay(t.rgb), t.a * uTitleA);
    // glass: a soft diagonal sheen, and the camera island
    float sheen = smoothstep(0.0, 0.35, 0.35 - abs(vUv.x * 0.8 + vUv.y - 1.05)) * 0.06;
    c += sheen;
    vec2 ip = (vUv - vec2(0.5, 0.968)) * vec2(9.0 / 19.5, 1.0);
    float island = roundRect(ip, vec2(0.055, 0.0085), 0.0085);
    c = mix(c, vec3(0.0), 1.0 - smoothstep(0.0, 0.002, island));
    c = toLinear(c) * uOn;
    gl_FragColor = vec4(c, 1.0);
    #include <colorspace_fragment>
  }
`;
