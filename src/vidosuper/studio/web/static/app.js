// VidoSuper Studio: resumable uploads, live progress, small form helpers. No inline scripts (strict CSP).
(function () {
  "use strict";
  const csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  const body = document.body;
  const pid = body.dataset.project;
  let uploading = 0;

  async function api(method, url, data, isBlob) {
    const opts = { method, headers: { "X-CSRF": csrf }, credentials: "same-origin" };
    if (data !== undefined) {
      if (isBlob) { opts.body = data; opts.headers["Content-Type"] = "application/octet-stream"; }
      else { opts.body = JSON.stringify(data); opts.headers["Content-Type"] = "application/json"; }
    }
    const res = await fetch(url, opts);
    let json = {};
    try { json = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) { const err = new Error(json.error || ("Error " + res.status)); err.status = res.status; throw err; }
    return json;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function row(list, name) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.className = "name";
    label.textContent = name;
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("span");
    bar.appendChild(fill);
    const pct = document.createElement("span");
    pct.className = "muted small";
    pct.textContent = "waiting";
    li.append(label, bar, pct);
    list.appendChild(li);
    return {
      set(f, text) { fill.style.width = Math.round(f * 100) + "%"; pct.textContent = text || Math.round(f * 100) + "%"; },
      fail(msg) { li.classList.add("off"); pct.textContent = ""; label.textContent = name + " · " + msg; label.classList.add("danger-text"); },
    };
  }

  async function uploadFile(file, kind, ui) {
    const start = await api("POST", `/api/p/${pid}/uploads`, { kind, name: file.name, size: file.size });
    const chunk = start.chunk;
    let received = start.received;
    let failures = 0;
    while (received < file.size) {
      const piece = file.slice(received, Math.min(file.size, received + chunk));
      try {
        const r = await api("PUT", `/api/uploads/${start.id}?offset=${received}`, piece, true);
        received = r.received;
        failures = 0;
        ui.set(received / file.size);
      } catch (err) {
        if (err.status && err.status >= 400 && err.status < 500) throw err; // the server said no: stop
        failures += 1;
        if (failures > 8) throw new Error("connection lost; try again");
        ui.set(received / file.size, "reconnecting…");
        await sleep(Math.min(30000, 1000 * 2 ** failures));
        try { received = (await api("GET", `/api/uploads/${start.id}`)).received; } catch (e) { /* retry */ }
      }
    }
    ui.set(1, "checking…");
    return api("POST", `/api/uploads/${start.id}/done`);
  }

  async function uploadAll(files, kind, list) {
    uploading += 1;
    let ok = 0;
    for (const file of files) {
      const ui = row(list, file.name);
      try { await uploadFile(file, kind, ui); ui.set(1, "✓"); ok += 1; }
      catch (err) { ui.fail(err.message); }
    }
    uploading -= 1;
    if (ok && !uploading) setTimeout(() => location.reload(), 600);
  }

  const clipInput = document.getElementById("clip-input");
  const songInput = document.getElementById("song-input");
  const uploadList = document.getElementById("upload-list");
  if (clipInput) {
    clipInput.addEventListener("change", () => { uploadAll([...clipInput.files], "clip", uploadList); clipInput.value = ""; });
    const drop = document.getElementById("drop");
    ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
    drop.addEventListener("drop", (ev) => {
      ev.preventDefault();
      const files = [...ev.dataTransfer.files].filter((f) => f.type.startsWith("video/") || /\.(mov|mp4|m4v|mkv|webm)$/i.test(f.name));
      if (files.length) uploadAll(files, "clip", uploadList);
    });
  }
  if (songInput) {
    songInput.addEventListener("change", () => {
      if (songInput.files[0]) uploadAll([songInput.files[0]], "song", document.getElementById("song-list"));
      songInput.value = "";
    });
  }
  window.addEventListener("beforeunload", (ev) => { if (uploading) { ev.preventDefault(); ev.returnValue = ""; } });

  document.querySelectorAll("[data-delete]").forEach((btn) => btn.addEventListener("click", async () => {
    btn.disabled = true;
    try { await api("POST", `/api/p/${pid}/clips/${btn.dataset.delete}/delete`); btn.closest("li").remove(); location.reload(); }
    catch (err) { btn.disabled = false; alert(err.message); }
  }));

  // progress while a job runs; reload when the stage moves on
  const bar = document.getElementById("bar");
  if (bar && bar.dataset.progress) bar.style.width = Math.round(parseFloat(bar.dataset.progress) * 100) + "%";
  if (pid && body.dataset.busy === "1") {
    const stage = body.dataset.stage;
    const msg = document.getElementById("job-msg");
    let misses = 0;
    const poll = async () => {
      try {
        const st = await api("GET", `/api/p/${pid}/status`);
        misses = 0;
        if (st.stage !== stage || !st.busy) { location.reload(); return; }
        if (bar && st.progress !== null) bar.style.width = Math.max(3, Math.round(st.progress * 100)) + "%";
        if (msg) msg.textContent = st.queued ? "Waiting for its turn…" : (st.message || "Working…");
      } catch (err) {
        misses += 1;
        if (err.status === 401) { location.href = "/login"; return; }
      }
      setTimeout(poll, misses ? Math.min(20000, 2500 * misses) : 2500);
    };
    setTimeout(poll, 1500);
  }

  const liked = document.getElementById("liked");
  const out = document.getElementById("liked-out");
  if (liked && out) liked.addEventListener("input", () => { out.textContent = liked.value; });

  const link = document.getElementById("invite-link");
  if (link) {
    const full = location.origin + link.dataset.path;
    link.textContent = full + "  (tap to copy)";
    link.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(full); link.textContent = full + "  ✓ copied"; } catch (e) { /* select by hand */ }
    });
  }
})();
