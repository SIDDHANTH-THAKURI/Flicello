// A thing that moves: what someone says, made real on screen (a red ball dropping from their head). Its motion is
// worked out beforehand by the physics (motion/things.py) as key frames, so preview and render play the same frames;
// this draws it, shaded like an object with light on it, a soft shadow on the floor under anything that falls.
// Variables:
// - copies [{keys: [[t, x, y, sx, sy, rot, op]], s}] (seconds into the block, frame shares; s: its size factor);
// - shape (a library name) or svg (a drawing made for it), color, size (share of the frame's height);
// - floor (share of the height where it lands: a shadow lies there), label (optional words beside it), duration.
window.FlicelloMotion.blocks.thing = function (M) {
  const { V, W, H, anim } = M;
  const D = Math.max(0.1, V.duration || 3) * 1000;
  const wrap = M.el('div', 'fm-thing', M.root);
  const px = (V.size || 0.07) * H;
  let uid = 0;

  // ---------------------------------------------------------------- colour helpers
  const hex = (c) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(c || '');
    const n = parseInt(m ? m[1] : 'e8322d', 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  };
  const mix = (c, k) => { // k > 0 towards white, k < 0 towards black
    const [r, g, b] = hex(c);
    const t = k > 0 ? 255 : 0;
    const a = Math.abs(k);
    return `rgb(${Math.round(r + (t - r) * a)}, ${Math.round(g + (t - g) * a)}, ${Math.round(b + (t - b) * a)})`;
  };

  // ---------------------------------------------------------------- the library: each drawn on a 100 x 100 box
  const C = V.color || '#e8322d';
  const lit = () => { // a light from the top left, a darker underside, a rim: an object, not a sticker
    const id = `t${uid += 1}`;
    return { id, defs: `<radialGradient id="${id}" cx="36%" cy="30%" r="75%"><stop offset="0" stop-color="${mix(C, 0.55)}"/>`
      + `<stop offset=".45" stop-color="${C}"/><stop offset="1" stop-color="${mix(C, -0.5)}"/></radialGradient>` };
  };
  const glint = '<ellipse cx="34" cy="28" rx="13" ry="8" fill="#fff" opacity=".55" transform="rotate(-30 34 28)"/>';
  const L = {
    ball: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="50" cy="50" r="46" fill="url(#${g.id})"/>`
      + `<circle cx="50" cy="50" r="46" fill="none" stroke="${mix(C, -0.6)}" stroke-opacity=".35" stroke-width="2"/>${glint}`; },
    star: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M50 4 L62 36 L96 37 L69 58 L79 92 L50 72 L21 92 L31 58 L4 37 L38 36 Z" `
      + `fill="url(#${g.id})" stroke="${mix(C, -0.45)}" stroke-width="2.5" stroke-linejoin="round"/>`
      + '<path d="M50 14 L58 37 L50 34 Z" fill="#fff" opacity=".5"/>'; },
    heart: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M50 90 C18 66 4 48 6 30 C8 14 22 6 34 8 C42 9 47 14 50 21 `
      + `C53 14 58 9 66 8 C78 6 92 14 94 30 C96 48 82 66 50 90 Z" fill="url(#${g.id})" stroke="${mix(C, -0.5)}" stroke-width="2"/>`
      + '<ellipse cx="28" cy="26" rx="10" ry="6" fill="#fff" opacity=".5" transform="rotate(-35 28 26)"/>'; },
    bolt: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M58 2 L18 56 L44 56 L36 98 L82 38 L54 38 Z" fill="url(#${g.id})" `
      + `stroke="${mix(C, -0.5)}" stroke-width="2.5" stroke-linejoin="round"/>`; },
    flame: () => `<defs><radialGradient id="f${uid += 1}" cx="50%" cy="75%" r="70%"><stop offset="0" stop-color="#fff6c2"/>`
      + `<stop offset=".35" stop-color="#ffc23a"/><stop offset=".75" stop-color="${C}"/><stop offset="1" stop-color="${mix(C, -0.4)}"/>`
      + `</radialGradient></defs><path d="M50 96 C24 96 12 78 14 60 C16 42 30 36 30 18 C42 26 46 38 46 46 C52 34 58 22 54 4 `
      + `C74 18 88 40 86 62 C84 82 70 96 50 96 Z" fill="url(#f${uid})"/>`,
    cloud: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M24 78 C10 78 4 66 8 56 C11 48 19 45 26 46 C27 32 38 24 50 26 `
      + `C60 18 78 22 82 38 C94 38 99 50 96 60 C94 72 84 78 74 78 Z" fill="url(#${g.id})" stroke="${mix(C, -0.25)}" stroke-width="2"/>`; },
    sun: () => { const g = lit(); const rays = Array.from({ length: 12 }, (_, k) => `<rect x="47" y="0" width="6" height="16" rx="3" `
      + `fill="${mix(C, -0.1)}" transform="rotate(${k * 30} 50 50)"/>`).join(''); return `<defs>${g.defs}</defs>${rays}`
      + `<circle cx="50" cy="50" r="30" fill="url(#${g.id})"/>${glint}`; },
    moon: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M62 6 C36 10 20 30 20 52 C20 76 40 94 64 94 C76 94 86 90 94 82 `
      + `C66 84 46 64 46 40 C46 26 52 14 62 6 Z" fill="url(#${g.id})" stroke="${mix(C, -0.35)}" stroke-width="2"/>`; },
    drop: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M50 4 C62 28 82 46 82 66 C82 84 68 96 50 96 C32 96 18 84 18 66 `
      + `C18 46 38 28 50 4 Z" fill="url(#${g.id})" stroke="${mix(C, -0.4)}" stroke-width="2"/>`
      + '<ellipse cx="38" cy="62" rx="6" ry="12" fill="#fff" opacity=".55"/>'; },
    coin: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="50" cy="50" r="46" fill="url(#${g.id})" stroke="${mix(C, -0.45)}" `
      + `stroke-width="3"/><circle cx="50" cy="50" r="34" fill="none" stroke="${mix(C, -0.3)}" stroke-width="3"/>`
      + `<text x="50" y="64" text-anchor="middle" font-size="40" font-weight="800" font-family="sans-serif" fill="${mix(C, -0.45)}">$</text>${glint}`; },
    cash: () => `<rect x="4" y="24" width="92" height="52" rx="6" fill="${C}" stroke="${mix(C, -0.45)}" stroke-width="3"/>`
      + `<rect x="12" y="32" width="76" height="36" rx="4" fill="none" stroke="${mix(C, 0.35)}" stroke-width="2"/>`
      + `<circle cx="50" cy="50" r="12" fill="${mix(C, 0.3)}"/><text x="50" y="57" text-anchor="middle" font-size="18" font-weight="800" `
      + `font-family="sans-serif" fill="${mix(C, -0.45)}">$</text>`,
    bulb: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M50 4 C26 4 12 22 14 42 C15 56 24 62 30 72 L70 72 C76 62 85 56 86 42 `
      + `C88 22 74 4 50 4 Z" fill="url(#${g.id})" stroke="${mix(C, -0.4)}" stroke-width="2"/>`
      + '<rect x="32" y="74" width="36" height="8" rx="3" fill="#9aa0a6"/><rect x="34" y="84" width="32" height="8" rx="3" fill="#7d838a"/>'
      + `<path d="M40 60 L44 40 L50 52 L56 40 L60 60" fill="none" stroke="${mix(C, -0.5)}" stroke-width="3" stroke-linejoin="round"/>${glint}`; },
    check: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="50" cy="50" r="46" fill="url(#${g.id})"/>`
      + '<path d="M28 52 L44 68 L74 34" fill="none" stroke="#fff" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>'; },
    cross: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="50" cy="50" r="46" fill="url(#${g.id})"/>`
      + '<path d="M32 32 L68 68 M68 32 L32 68" stroke="#fff" stroke-width="11" stroke-linecap="round"/>'; },
    question: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="50" cy="50" r="46" fill="url(#${g.id})"/>`
      + '<path d="M36 38 C36 26 44 20 52 20 C62 20 68 27 68 35 C68 47 52 48 52 60" fill="none" stroke="#fff" stroke-width="10" stroke-linecap="round"/>'
      + '<circle cx="52" cy="76" r="6" fill="#fff"/>'; },
    exclaim: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="50" cy="50" r="46" fill="url(#${g.id})"/>`
      + '<path d="M50 22 L50 58" stroke="#fff" stroke-width="12" stroke-linecap="round"/><circle cx="50" cy="76" r="7" fill="#fff"/>'; },
    arrow: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M6 40 L58 40 L58 18 L96 50 L58 82 L58 60 L6 60 Z" fill="url(#${g.id})" `
      + `stroke="${mix(C, -0.45)}" stroke-width="2.5" stroke-linejoin="round"/>`; },
    note: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M38 76 L38 18 L84 8 L84 64" fill="none" stroke="${mix(C, -0.2)}" stroke-width="7"/>`
      + `<path d="M38 18 L84 8 L84 22 L38 32 Z" fill="${mix(C, -0.2)}"/><ellipse cx="28" cy="78" rx="14" ry="11" fill="url(#${g.id})"/>`
      + `<ellipse cx="74" cy="66" rx="14" ry="11" fill="url(#${g.id})"/>`; },
    clock: () => `<circle cx="50" cy="50" r="46" fill="${C}" stroke="#2b2b30" stroke-width="5"/>`
      + Array.from({ length: 12 }, (_, k) => `<rect x="48.5" y="9" width="3" height="${k % 3 ? 5 : 9}" fill="#2b2b30" transform="rotate(${k * 30} 50 50)"/>`).join('')
      + '<path d="M50 50 L50 24 M50 50 L68 58" stroke="#2b2b30" stroke-width="5" stroke-linecap="round"/><circle cx="50" cy="50" r="4" fill="#e8322d"/>',
    trophy: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M26 8 L74 8 L72 40 C70 56 60 62 50 62 C40 62 30 56 28 40 Z" fill="url(#${g.id})" `
      + `stroke="${mix(C, -0.45)}" stroke-width="2"/><path d="M26 14 C10 14 10 38 30 40 M74 14 C90 14 90 38 70 40" fill="none" stroke="${mix(C, -0.2)}" stroke-width="5"/>`
      + `<rect x="44" y="62" width="12" height="16" fill="${mix(C, -0.25)}"/><rect x="28" y="78" width="44" height="14" rx="3" fill="${mix(C, -0.4)}"/>${glint}`; },
    crown: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M8 78 L14 26 L34 50 L50 16 L66 50 L86 26 L92 78 Z" fill="url(#${g.id})" `
      + `stroke="${mix(C, -0.45)}" stroke-width="2.5" stroke-linejoin="round"/><rect x="8" y="78" width="84" height="12" rx="3" fill="${mix(C, -0.3)}"/>`
      + '<circle cx="50" cy="64" r="6" fill="#e8322d"/><circle cx="28" cy="66" r="4" fill="#2f7cf6"/><circle cx="72" cy="66" r="4" fill="#2fbf5b"/>'; },
    bomb: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="46" cy="58" r="38" fill="url(#${g.id})"/>`
      + `<rect x="58" y="14" width="16" height="14" rx="3" fill="${mix(C, 0.25)}" transform="rotate(35 66 21)"/>`
      + '<path d="M72 14 C78 6 86 8 90 4" fill="none" stroke="#c9a46a" stroke-width="3"/><circle cx="91" cy="4" r="4" fill="#ffd23a"/>'
      + '<ellipse cx="32" cy="42" rx="10" ry="6" fill="#fff" opacity=".35" transform="rotate(-30 32 42)"/>'; },
    gift: () => { const g = lit(); return `<defs>${g.defs}</defs><rect x="10" y="38" width="80" height="56" rx="4" fill="url(#${g.id})"/>`
      + `<rect x="6" y="26" width="88" height="16" rx="3" fill="${mix(C, 0.15)}"/><rect x="44" y="26" width="12" height="68" fill="#ffd23a"/>`
      + '<path d="M50 26 C36 4 20 14 30 26 Z M50 26 C64 4 80 14 70 26 Z" fill="#ffd23a" stroke="#c9a12e" stroke-width="2"/>'; },
    rocket: () => `<path d="M50 4 C70 18 76 42 72 70 L28 70 C24 42 30 18 50 4 Z" fill="${C}" stroke="#4a4f57" stroke-width="2.5"/>`
      + '<circle cx="50" cy="36" r="10" fill="#2f7cf6" stroke="#4a4f57" stroke-width="3"/>'
      + '<path d="M28 52 L12 76 L30 70 Z M72 52 L88 76 L70 70 Z" fill="#e8322d"/>'
      + '<path d="M38 70 C40 86 50 96 50 96 C50 96 60 86 62 70 Z" fill="#ffb02e"/>',
    planet: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="50" cy="50" r="30" fill="url(#${g.id})"/>`
      + `<ellipse cx="50" cy="52" rx="48" ry="12" fill="none" stroke="${mix(C, 0.4)}" stroke-width="5" transform="rotate(-18 50 50)"/>${glint}`; },
    sparkle: () => `<path d="M50 2 C54 34 66 46 98 50 C66 54 54 66 50 98 C46 66 34 54 2 50 C34 46 46 34 50 2 Z" fill="${C}"/>`
      + '<path d="M50 22 C52 40 60 48 78 50 C60 52 52 60 50 78 C48 60 40 52 22 50 C40 48 48 40 50 22 Z" fill="#fff" opacity=".7"/>',
    confetti: () => `<rect x="30" y="20" width="40" height="60" rx="4" fill="${C}"/>`,
    eye: () => `<ellipse cx="50" cy="50" rx="46" ry="30" fill="#fff" stroke="#2b2b30" stroke-width="4"/>`
      + `<circle cx="50" cy="50" r="20" fill="${C}"/><circle cx="50" cy="50" r="9" fill="#111"/><circle cx="44" cy="44" r="5" fill="#fff"/>`,
    smile: () => { const g = lit(); return `<defs>${g.defs}</defs><circle cx="50" cy="50" r="46" fill="url(#${g.id})"/>`
      + '<ellipse cx="36" cy="40" rx="6" ry="9" fill="#2b2b30"/><ellipse cx="64" cy="40" rx="6" ry="9" fill="#2b2b30"/>'
      + '<path d="M28 58 C36 76 64 76 72 58" fill="none" stroke="#2b2b30" stroke-width="6" stroke-linecap="round"/>'; },
    cube: () => `<path d="M50 6 L90 28 L50 50 L10 28 Z" fill="${mix(C, 0.35)}"/><path d="M10 28 L50 50 L50 96 L10 74 Z" fill="${C}"/>`
      + `<path d="M90 28 L50 50 L50 96 L90 74 Z" fill="${mix(C, -0.35)}"/>`,
    ring: () => `<circle cx="50" cy="50" r="38" fill="none" stroke="${C}" stroke-width="14"/>`
      + `<circle cx="50" cy="50" r="38" fill="none" stroke="#fff" stroke-opacity=".4" stroke-width="3" stroke-dasharray="40 200"/>`,
    pin: () => { const g = lit(); return `<defs>${g.defs}</defs><path d="M50 98 C38 74 16 58 16 36 C16 16 32 2 50 2 C68 2 84 16 84 36 C84 58 62 74 50 98 Z" `
      + `fill="url(#${g.id})"/><circle cx="50" cy="36" r="13" fill="#fff"/>`; },
  };
  const confettiColors = ['#ff5fa8', '#ffd23a', '#22d3ee', '#2fbf5b', '#8b5cf6', '#ff8a1f'];
  const drawing = (k) => {
    if (V.svg) return V.svg;
    const shape = L[V.shape] ? V.shape : 'ball';
    if (shape === 'confetti') {
      return `<svg viewBox="0 0 100 100"><rect x="30" y="20" width="40" height="60" rx="4" fill="${confettiColors[k % confettiColors.length]}"/></svg>`;
    }
    return `<svg viewBox="0 0 100 100">${L[shape]()}</svg>`;
  };

  // ---------------------------------------------------------------- each copy, its shadow, its motion
  const floor = V.floor;
  const ROUND = new Set(['ball', 'coin', 'planet', 'smile', 'eye', 'clock', 'bomb']);
  (V.copies || []).forEach((copy, k) => {
    const raw = copy.keys || [];
    if (!raw.length) return;
    // held before its first frame and after its last (else the browser fills those ends with the bare element: a
    // piece back at its start, fully seen)
    const keys = [[0, ...raw[0].slice(1)], ...raw, [D / 1000, ...raw[raw.length - 1].slice(1)]];
    const size = px * (copy.s || 1);
    const at = (t) => Math.max(0, Math.min(1, (t * 1000) / D));
    if (floor != null) { // a soft shadow on the floor, smaller and fainter the higher it is
      const sh = M.el('div', 'shadow', wrap);
      sh.style.width = `${size * 1.1}px`;
      sh.style.height = `${size * 0.26}px`;
      anim(sh, keys.map(([t, x, y, , , , op]) => {
        const up = Math.max(0, floor - (y + (copy.s || 1) * (V.size || 0.07) / 2));
        const s = Math.max(0.25, 1 - up * 1.6);
        return { offset: at(t), opacity: op * (0.42 * s), transform: `translate(${x * W - size * 0.55}px, ${floor * H - size * 0.13}px) scale(${s})` };
      }), { duration: D, easing: 'linear' });
    }
    const body = M.el('div', 'body', wrap);
    body.style.width = `${size}px`;
    body.style.height = `${size}px`;
    body.innerHTML = drawing(k);
    // squash and stretch stay true to the floor; the spin turns the drawing inside (a lit sphere keeps its light)
    anim(body, keys.map(([t, x, y, sx, sy, , op]) => ({
      offset: at(t), opacity: op, transform: `translate(${x * W - size / 2}px, ${y * H - size / 2}px) scale(${sx}, ${sy})`,
    })), { duration: D, easing: 'linear' });
    const art = body.firstElementChild;
    if (art && !ROUND.has(V.shape) && !V.svg) {
      anim(art, keys.map(([t, , , , , rot]) => ({ offset: at(t), transform: `rotate(${rot}deg)` })), { duration: D, easing: 'linear' });
    } else if (art && V.svg) { // a drawing made for it: a gentle tilt, never upside down
      anim(art, keys.map(([t, , , , , rot]) => ({ offset: at(t), transform: `rotate(${Math.max(-25, Math.min(25, rot % 360 > 180 ? (rot % 360) - 360 : rot % 360))}deg)` })),
        { duration: D, easing: 'linear' });
    }
  });
  if (V.label && (V.copies || [])[0]) { // a few words beside it, when they asked for words too
    const first = V.copies[0].keys;
    const [, x, y] = first[Math.min(first.length - 1, 8)];
    const tag = M.el('div', 'label', wrap);
    tag.textContent = V.label;
    tag.style.fontSize = `${Math.max(18, px * 0.42)}px`;
    tag.style.left = `${Math.min(W * 0.92, x * W + px * 0.7)}px`;
    tag.style.top = `${y * H - px * 0.25}px`;
    anim(tag, [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'translateY(0)' }],
      { duration: M.ms(9), delay: M.ms(6), easing: M.EASE.softLand });
    anim(tag, [{ opacity: 1 }, { opacity: 0 }], { duration: M.ms(7), delay: Math.max(0, D - M.ms(8)), easing: M.EASE.accelExit, fill: 'forwards' });
  }
};
