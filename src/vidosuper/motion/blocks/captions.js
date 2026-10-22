// Captions, word by word, the way short videos are read with the sound off.
// - Phrases of a few words (they break at a pause or a sentence end) stand in the safe zone.
// - Each word pops in a frame before it is said and is lit while it is said.
// - The next phrase takes over on the frame its first word shows, so the screen is never blank between phrases.
// Variables:
// - words [{w, s, e}] (seconds from the block's start);
// - style: pop (the word being said in the accent), karaoke (said words stay lit), box (a colour block behind the word);
// - per (words per phrase), y (the phrase's centre, a frame fraction), size (share of frame height), side (margin share);
// - color, accent, font, upper (capitals).
window.FlicelloMotion.blocks.captions = function (M) {
  const { V, H, EASE, ms, anim, FONTS } = M;
  const wrap = M.el('div', 'fm-captions', M.root);
  const style = V.style || 'pop';
  wrap.classList.add(`style-${style}`);
  if (V.upper !== false) wrap.classList.add('style-upper');
  const per = Math.max(1, V.per || 3);
  const lead = ms(1);
  const q = (sec) => Math.round(sec * M.FPS) / M.FPS; // on frames, so a hand-over never shows both phrases at once
  const words = (V.words || []).filter((w) => w && w.w)
    .map((w) => ({ w: String(w.w), s: q(+w.s), e: q(Math.max(+w.e, +w.s + 0.08)) }));
  // phrases: up to `per` words, broken at a pause of 0.45 s or after a sentence ends
  const phrases = [];
  for (const w of words) {
    const cur = phrases[phrases.length - 1];
    const prev = cur && cur[cur.length - 1];
    if (!cur || cur.length >= per || (prev && (w.s - prev.e > 0.45 || /[.!?]$/.test(prev.w)))) phrases.push([w]);
    else cur.push(w);
  }
  const accent = V.accent || '#ffd84d';
  const color = V.color || '#ffffff';
  const side = (V.side ?? 0.08) * 100;
  phrases.forEach((ph, k) => {
    const div = M.el('div', 'phrase', wrap);
    div.style.top = `${(V.y ?? 0.62) * 100}%`;
    div.style.left = `${side}%`;
    div.style.right = `${side}%`;
    div.style.fontSize = `${(V.size || 0.05) * H}px`;
    div.style.fontFamily = FONTS[V.font] || FONTS.display;
    const start = ph[0].s * 1000 - lead;
    const next = phrases[k + 1];
    // on until the frame the next phrase's first word shows (it is invisible on the frame its pop starts), or a
    // moment after its last word when the next one is far off
    const until = next ? Math.min(next[0].s * 1000 - lead + ms(1), (ph[ph.length - 1].e + 0.7) * 1000)
      : (ph[ph.length - 1].e + 0.5) * 1000;
    anim(div, [{ opacity: 1 }, { opacity: 1 }], { duration: Math.max(1, until - start - 0.5), delay: start, fill: 'none' });
    ph.forEach((w, i) => {
      const s = M.el('span', 'wd', div);
      s.textContent = w.w.replace(/[,.;:]+$/, '');
      s.style.color = color;
      if (i < ph.length - 1) div.append(document.createTextNode(' '));
      const at = w.s * 1000 - lead;
      const spoken = Math.max(ms(4), (w.e - w.s) * 1000);
      // in on the word: up from below and out of a slight scale, a touch past full size, then settling (10 frames)
      anim(s, [
        { opacity: 0, translate: '0 0.2em', scale: '0.8', easing: EASE.softLand },
        { opacity: 1, translate: '0 0', scale: '1.06', offset: 0.45, easing: EASE.glide },
        { opacity: 1, translate: '0 0', scale: '1' },
      ], { duration: ms(10), delay: at });
      if (style === 'box') {
        anim(s, [{ backgroundColor: accent, color: '#111' }, { backgroundColor: accent, color: '#111' }],
          { duration: spoken, delay: at, fill: 'none' });
      } else if (style === 'karaoke') {
        anim(s, [{ color }, { color: accent }], { duration: ms(3), delay: at, fill: 'forwards' });
      } else {
        anim(s, [{ color: accent }, { color: accent }], { duration: spoken, delay: at, fill: 'none' });
      }
    });
  });
};
