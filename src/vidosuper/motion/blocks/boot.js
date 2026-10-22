// The render page: the block named in the values, on the page's stage.
(function () {
  const V = JSON.parse(document.getElementById('vars').textContent || '{}');
  const root = document.getElementById('root');
  const stage = document.getElementById('stage');
  window.FlicelloMotion.blocks[V.block](window.FlicelloMotion.make(stage, V, +root.dataset.width, +root.dataset.height));
}());
