// Shared playback checks. The player clock, never the tab audio clock, identifies a clip.
globalThis.pcvMedia = (() => {
  const IDLE_MS = 1200;

  function platform(url) {
    const u = new URL(url);
    if (u.protocol !== 'https:') return null;
    for (const name of ['douyin', 'bilibili', 'youtube']) {
      const domain = name + '.com';
      if (u.hostname === domain || u.hostname.endsWith('.' + domain)) return name;
    }
    return null;
  }

  function valid(beat) {
    if (!beat || typeof beat.platform_content_id !== 'string' ||
        !beat.platform_content_id.length || beat.platform_content_id.length > 100) return false;
    const {position_seconds, rate, viewport, rect} = beat;
    return Number.isFinite(position_seconds) && position_seconds >= 0 &&
      Number.isFinite(rate) && rate >= 0.5 && rate <= 2 && typeof beat.audible === 'boolean' &&
      Number.isFinite(viewport?.width) && viewport.width > 0 &&
      Number.isFinite(viewport?.height) && viewport.height > 0 &&
      rect && [rect.left, rect.top, rect.right, rect.bottom].every(Number.isFinite) &&
      rect.left >= 0 && rect.top >= 0 && rect.right <= 1 && rect.bottom <= 1 &&
      rect.right > rect.left && rect.bottom > rect.top;
  }

  function continues(previous, next) {
    if (!valid(previous) || !valid(next) || previous.platform !== next.platform ||
        previous.platform_content_id !== next.platform_content_id || previous.rate !== next.rate ||
        previous.audible !== next.audible) return false;
    const gap = next.at - previous.at;
    const advance = next.position_seconds - previous.position_seconds;
    return gap > 0 && gap <= IDLE_MS && advance > 0 &&
      Math.abs(advance - gap / 1000 * next.rate) <= 0.30;
  }

  function crop(beat, width, height) {
    const {viewport, rect} = beat;
    if (!width || !height || Math.abs((width / height) / (viewport.width / viewport.height) - 1) > 0.03) return null;
    const x = Math.ceil(rect.left * width);
    const y = Math.ceil(rect.top * height);
    const w = Math.floor(rect.right * width) - x;
    const h = Math.floor(rect.bottom * height) - y;
    return w >= 64 && h >= 64 ? {x, y, w, h} : null;
  }

  function wav16(chunks, inputRate, maxSeconds) {
    const size = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const rate = Math.min(16000, inputRate);
    const step = inputRate / rate;
    const count = Math.min(Math.floor(size / step), Math.floor(maxSeconds * rate));
    if (count < rate / 4) return null;
    const samples = new Float32Array(size);
    let offset = 0;
    for (const chunk of chunks) { samples.set(chunk, offset); offset += chunk.length; }
    const bytes = new Uint8Array(44 + count * 2);
    const data = new DataView(bytes.buffer);
    function label(index, name) { bytes.set(Array.from(name, c => c.charCodeAt(0)), index); }
    label(0, 'RIFF');
    data.setUint32(4, 36 + count * 2, true);
    label(8, 'WAVEfmt ');
    data.setUint32(16, 16, true);
    data.setUint16(20, 1, true);
    data.setUint16(22, 1, true);
    data.setUint32(24, rate, true);
    data.setUint32(28, rate * 2, true);
    data.setUint16(32, 2, true);
    data.setUint16(34, 16, true);
    label(36, 'data');
    data.setUint32(40, count * 2, true);
    for (let i = 0; i < count; i++) {
      const left = Math.floor(i * step);
      const right = Math.floor((i + 1) * step);
      let sum = 0;
      for (let j = left; j < right; j++) sum += samples[j];
      const value = Math.max(-1, Math.min(1, sum / (right - left)));
      data.setInt16(44 + i * 2, value < 0 ? value * 32768 : value * 32767, true);
    }
    return bytes;
  }

  return {platform, valid, continues, crop, wav16, IDLE_MS};
})();
