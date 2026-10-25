// Builds the front end into the studio's static folder. The studio's CSP allows only 'self': no inline scripts,
// no data: fonts (assetsInlineLimit 0), no modulepreload polyfill (it would be an inline script).
import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  base: '/static/dist/',
  publicDir: false,
  build: {
    outDir: here('../src/vidosuper/studio/web/static/dist'),
    emptyOutDir: true,
    manifest: 'manifest.json',
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
    target: 'es2020',
    cssCodeSplit: true,
    rollupOptions: {
      input: {
        app: here('src/app/main.js'),
        cello: here('src/cello/main.js'),
        editor: here('src/editor/main.js'),
      },
    },
  },
});
