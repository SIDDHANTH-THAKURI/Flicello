// Decides, before the page paints, whether the 5-second intro plays: once per browser session, never for people who
// prefer reduced motion. (Kept as its own tiny blocking script because the strict CSP forbids inline scripts.)
(function () {
  "use strict";
  var root = document.documentElement;
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var seen = false;
  try { seen = sessionStorage.getItem("flicello-intro") === "1"; } catch (e) { /* private mode: play it */ }
  if (!reduced && !seen) root.classList.add("intro-on");
})();
