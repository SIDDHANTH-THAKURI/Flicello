// A lower third: a bar grows, the name is wiped in beside it, the line under it opens up, it holds, and it leaves
// accelerating to the left. Variables: name, role, x, y (frame fractions of its left edge and centre), size, side,
// color, accent, font, in_at, out_at, duration.
window.FlicelloMotion.blocks.lowerthird = function (M) {
  const { V, W, H, EASE, ms, anim, FONTS } = M;
  const wrap = M.el('div', 'fm-lowerthird', M.root);
  const lt = M.el('div', 'lt', wrap);
  const scrim = M.el('div', 'lt-scrim', lt);
  const bar = M.el('span', 'lt-bar', lt);
  const text = M.el('div', 'lt-text', lt);
  const name = M.el('span', 'lt-name', text);
  const role = M.el('span', 'lt-role', text);
  lt.style.left = `${(V.x ?? 0.07) * 100}%`;
  lt.style.top = `${(V.y ?? 0.78) * 100}%`;
  lt.style.fontSize = `${(V.size || 0.05) * H}px`;
  lt.style.fontFamily = FONTS[V.font] || FONTS.display;
  name.textContent = V.name || '';
  name.style.color = V.color || '#ffffff';
  role.textContent = V.role || '';
  role.style.color = V.accent || '#ffd38a';
  bar.style.background = V.accent || '#ffd38a';
  if (!V.role) role.style.display = 'none';
  M.fit(lt, (1 - (V.x ?? 0.07) - (V.side ?? 0.07)) * W);
  const at = (V.in_at ?? 0.1) * 1000;
  const out = (V.out_at ?? Math.max(0.8, (V.duration || 3) - ms(10) / 1000)) * 1000;
  if (typeof V.scrim === 'number') M.scrim(scrim, V.scrim);
  anim(scrim, [{ opacity: 0 }, { opacity: 1 }], { duration: ms(10), delay: at, easing: EASE.softLand });
  anim(bar, [{ transform: 'scaleY(0)' }, { transform: 'scaleY(1)' }], { duration: ms(10), delay: at, easing: EASE.softLand });
  anim(name, [{ clipPath: 'inset(-10% 100% -10% 0)', translate: '-0.2em 0' }, { clipPath: 'inset(-10% 0 -10% 0)', translate: '0 0' }],
    { duration: ms(12), delay: at + ms(5), easing: EASE.softLand });
  anim(role, [{ opacity: 0, letterSpacing: '0.6em' }, { opacity: 1, letterSpacing: '0.22em' }],
    { duration: ms(12), delay: at + ms(11), easing: EASE.softLand });
  anim(lt, [{ opacity: 1, translate: '0 0' }, { opacity: 0, translate: '-0.6em 0' }],
    { duration: ms(9), delay: out, easing: EASE.accelExit });
};
