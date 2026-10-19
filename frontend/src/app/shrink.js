// Clips made smaller on the person's own device before they are sent.
//
// Home connections upload far slower than they download (the owner's: 92 Mbit/s down, 19 up), so a phone's 4K clip
// can take minutes to send while most of its pixels are never used: a film is 1080x1920 at most. Where the browser
// can do it (WebCodecs, through Mediabunny), a clip is re-encoded to the size the film needs (plus headroom for
// punch-ins and reframing) at its own frame rate (slow motion needs 50/60 fps), and streamed into the resumable upload
// while it is being made, so memory stays small. Anything unusual (HDR, a codec the browser can't read or write, a
// sound track it would drop, not much to gain) and the original file is sent as before.

const BPP = 0.12; // bits per pixel per frame: H.264 at a quality where the re-encode can't be told from the source
const MARGIN = 1.15; // headroom over the film's own size, for punch-ins and reframing
const OUT = { '9:16': [1080, 1920], '16:9': [1920, 1080] };
const MIN_BYTES = 25e6; // smaller files go as they are
const GAIN = 0.6; // only when the result should be at most 60% of the original

let lib = null;
const load = async () => {
  lib = lib || (await import('mediabunny'));
  return lib;
};

export const canShrink = () => typeof window !== 'undefined' && 'VideoEncoder' in window && 'VideoDecoder' in window;

const even = (n) => Math.max(2, Math.round(n / 2) * 2);

// The size, frame rate and bitrate to send this clip at, or null to send the original. `shapes`: the project's
// ('9:16', '16:9' or 'both'); `kind`: a talking video keeps its own shape.
export async function plan(file, shapes, kind) {
  if (!canShrink() || file.size < MIN_BYTES) return null;
  const mb = await load();
  const input = new mb.Input({ formats: mb.ALL_FORMATS, source: new mb.BlobSource(file) });
  try {
    const track = await input.getPrimaryVideoTrack();
    if (!track) return null;
    if (await track.hasHighDynamicRange()) return null; // HDR: the browser can't bring it to normal colours faithfully
    if (!(await track.canDecode())) return null;
    const w = await track.getDisplayWidth();
    const h = await track.getDisplayHeight();
    const seconds = await input.computeDuration();
    if (!w || !h || !seconds) return null;
    const rate = await track.computeFrameRateMetrics();
    const fps = Math.max(1, Math.min(60, Math.round(rate.underlyingFrameRate || rate.bestGuessFrameRate || 30)));
    const want = kind === 'talk' ? [h > w ? '9:16' : '16:9'] : shapes === 'both' ? ['9:16', '16:9'] : [shapes || '9:16'];
    let scale = 0; // the smallest size whose crop still fills every shape of the film pixel for pixel
    for (const s of want) {
      const [ow, oh] = OUT[s] || OUT['9:16'];
      scale = Math.max(scale, ow / w, oh / h);
    }
    scale = Math.min(1, scale * MARGIN);
    const width = even(w * scale);
    const height = even(h * scale);
    const bitrate = Math.round(width * height * fps * BPP);
    const estimate = ((bitrate + 192e3) * seconds) / 8;
    if (estimate > file.size * GAIN) return null; // not worth it: the original is already lean
    const ok = await mb.canEncodeVideo('avc', { width, height, frameRate: fps, quality: new mb.Quality({ bitrate }) });
    if (!ok) return null;
    return { input, width, height, fps, bitrate, seconds, estimate, from: { w, h } };
  } catch {
    return null;
  }
}

// Make the smaller clip and send it at the same time. `put(bytes, offset)` sends one piece (retrying as it must) and
// returns the bytes the server now has; `progress(f)` gets 0..1. Resolves with the final size; throws if anything is
// off (the caller then sends the original).
export async function convertAndSend(p, chunkBytes, put, progress, originalBytes, hardware = 'no-preference') {
  const mb = await load();
  let offset = 0;
  let produced = 0; // bytes the encoder has made so far (some may still wait to be sent)
  let sentBytes = 0;
  let sendSeconds = 0; // time spent sending: how fast this connection uploads
  let stop = null; // why the lighter copy was given up, if it was
  // The encoder's output is gathered here and sent in pieces of exactly `chunkBytes` (the last one smaller), so the
  // count of bytes made is known at once (for the checks below) and memory stays at about one piece.
  let pending = [];
  let pendingBytes = 0;
  const send = async (bytes) => {
    const t = performance.now();
    const got = await put(bytes, offset);
    sendSeconds += (performance.now() - t) / 1000;
    sentBytes += bytes.byteLength;
    if (got !== offset + bytes.byteLength) throw new Error('the server has a different amount');
    offset = got;
  };
  const take = (n) => { // the first n pending bytes, as one array
    const out = new Uint8Array(n);
    let at = 0;
    while (at < n) {
      const head = pending[0];
      const k = Math.min(head.byteLength, n - at);
      out.set(head.subarray(0, k), at);
      at += k;
      if (k === head.byteLength) pending.shift(); else pending[0] = head.subarray(k);
    }
    pendingBytes -= n;
    return out;
  };
  let done = 0; // how far the conversion is, 0..1
  let conversion = null;
  // Not worth it on this device: heading for more than 80% of the original's size (some encoders ignore the bitrate
  // at 4K). Judged before each piece goes out, so a piece that is too big never crosses the slow connection.
  const tooBig = () => originalBytes && done >= 0.1 && produced / done > originalBytes * 0.8;
  const writable = new WritableStream({
    async write(chunk) {
      if (stop) throw new Error(stop);
      if (chunk.position !== produced) throw new Error('the file came out of order');
      pending.push(chunk.data.slice()); // a copy: the encoder may reuse its buffer
      pendingBytes += chunk.data.byteLength;
      produced += chunk.data.byteLength;
      if (tooBig()) stop = 'the lighter copy was coming out too big';
      if (stop) {
        conversion?.cancel().catch(() => {}); // stop the encoder now, not when it next reports progress
        throw new Error(stop);
      }
      while (pendingBytes >= chunkBytes) await send(take(chunkBytes));
    },
    async close() {
      if (pendingBytes) await send(take(pendingBytes));
      pending = [];
    },
  });
  const output = new mb.Output({
    format: new mb.Mp4OutputFormat({ fastStart: 'fragmented' }), // written strictly in order: it can stream out
    target: new mb.StreamTarget(writable),
  });
  conversion = await mb.Conversion.init({
    input: p.input,
    output,
    video: {
      // no frameRate: every frame keeps its own time (59.94 stays 59.94; forcing 60 shifted frames)
      width: p.width, height: p.height, fit: 'contain', codec: 'avc',
      quality: new mb.Quality({ bitrate: p.bitrate }), allowTransformationMetadata: false,
      hardwareAcceleration: hardware,
      keyFrameInterval: 2, // smaller, steadier pieces to judge and send (and quicker seeking later)
    },
    audio: { codec: 'aac' },
  });
  // the sound (or the picture) would be lost: send the original instead. Extra tracks a phone adds (spatial audio,
  // metadata) may go, as long as one sound track and the picture come through.
  const used = (type) => conversion.utilizedTracks.some((t) => t.type === type);
  const had = (type) => used(type) || conversion.discardedTracks.some((d) => d.track.type === type);
  if (!conversion.isValid || !used('video') || (had('audio') && !used('audio'))) {
    throw new Error('this clip can only be sent as it is');
  }
  // Give up early (and send the original at once) when the lighter copy isn't worth it on this device: it comes out
  // far bigger than planned (some encoders ignore the bitrate), or making it would take longer than sending the
  // original over this connection.
  const t0 = performance.now();
  conversion.onProgress = (f) => {
    done = f;
    try { progress(Math.min(0.99, f), produced); } catch (err) { // the person cancelled this upload: stop at once
      stop = (err && err.message) || 'cancelled';
      conversion.cancel().catch(() => {});
      return;
    }
    if (stop || f < 0.12) return;
    if (tooBig()) stop = 'the lighter copy was coming out too big';
    // what is left of making the lighter copy, against sending the original from the start over this connection
    // (near the end this never stops it: finishing is always the quicker way)
    const left = (((performance.now() - t0) / 1000) * (1 - f)) / f;
    if (originalBytes && sentBytes > 0 && sendSeconds > 0) {
      const originalSeconds = originalBytes / (sentBytes / sendSeconds);
      if (left > originalSeconds * 0.9) stop = 'making it lighter is slower than sending it';
    }
    if (stop) conversion.cancel().catch(() => {});
  };
  try {
    await conversion.execute();
  } catch (err) {
    throw new Error(stop || (err && err.message) || 'the lighter copy failed');
  }
  if (stop) throw new Error(stop);
  if (offset !== produced) throw new Error('not everything was sent');
  return offset;
}
