// The footage wall behind the sign-in form: columns of graded clips drifting past like film through a gate, each
// column at its own speed, drawn from the one mosaic video. Pauses when the tab is hidden.
const TILE_W = 288, TILE_H = 512, TILES = 12;
const GRADE = 'contrast(1.16) saturate(1.3) brightness(0.86) sepia(0.1) hue-rotate(-6deg)';

export function footageWall(canvas, video, reduced) {
  if (!video) return;
  const ctx = canvas.getContext('2d');
  let w = 0, h = 0, dpr = 1, raf = 0, cols = [];

  function layout() {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    w = Math.max(2, Math.round(r.width * dpr));
    h = Math.max(2, Math.round(r.height * dpr));
    canvas.width = w;
    canvas.height = h;
    const n = Math.max(3, Math.round(r.width / 230));
    const colW = (w * 1.18) / n;
    cols = Array.from({ length: n }, (_, i) => ({
      x: -w * 0.09 + i * colW, w: colW - 14 * dpr,
      speed: (18 + ((i * 37) % 23)) * dpr * (i % 2 ? 1 : -1),
      offset: (i * 997) % 400, tiles: Array.from({ length: 8 }, (_, k) => (i * 5 + k * 7) % TILES),
    }));
  }

  function draw(t) {
    ctx.clearRect(0, 0, w, h);
    if (video.readyState >= 2) {
      ctx.filter = GRADE;
      for (const c of cols) {
        const th = c.w * (16 / 9);
        const gap = 14 * dpr;
        const span = (th + gap) * c.tiles.length;
        let y = ((c.offset * dpr + (reduced ? 0 : t * 0.001 * c.speed)) % span + span) % span - span;
        for (let k = 0; y < h; k++) {
          const tile = c.tiles[k % c.tiles.length];
          ctx.save();
          ctx.beginPath();
          ctx.roundRect(c.x, y, c.w, th, 18 * dpr);
          ctx.clip();
          ctx.drawImage(video, (tile % 4) * TILE_W, Math.floor(tile / 4) * TILE_H, TILE_W, TILE_H, c.x, y, c.w, th);
          ctx.restore();
          y += th + gap;
        }
      }
      ctx.filter = 'none';
    }
    raf = requestAnimationFrame(draw);
  }

  const play = () => video.play().catch(() => {});
  play();
  window.addEventListener('pointerdown', play, { once: true, passive: true });
  layout();
  window.addEventListener('resize', layout);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) cancelAnimationFrame(raf);
    else raf = requestAnimationFrame(draw);
  });
  raf = requestAnimationFrame(draw);
}
