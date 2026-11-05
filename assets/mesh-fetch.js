import { meshBuffer } from './mesh-data.js';

const byteLimit = 64 * 1024 * 1024;

async function receive(url, headerMs, idleMs) {
  const controller = new AbortController();
  let timer, reader;
  const deadline = milliseconds => {
    clearTimeout(timer);
    timer = setTimeout(() => controller.abort(new DOMException('Geometry download stalled', 'TimeoutError')), milliseconds);
  };
  try {
    deadline(headerMs);
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw Error('Geometry unavailable: HTTP ' + response.status);
    if (Number(response.headers.get('Content-Length')) > byteLimit) throw RangeError('Encoded geometry exceeds byte budget');
    if (!response.body) throw Error('Geometry response has no body');
    reader = response.body.getReader();
    const chunks = []; let size = 0;
    for (;;) {
      // This is an idle deadline, not a total download deadline. A slow but
      // progressing connection may finish without discarding its response.
      deadline(idleMs);
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > byteLimit) throw RangeError('Encoded geometry exceeds byte budget');
      chunks.push(value);
    }
    clearTimeout(timer);
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes.buffer;
  } finally {
    clearTimeout(timer);
    if (reader) {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
    controller.abort();
  }
}

export async function fetchMeshBuffer(url, onRetry = () => {}, { headerMs = 8000, idleMs = 5000, retryURL = url } = {}) {
  let encoded;
  for (let attempt = 0; attempt < 2; attempt++) {
    try { encoded = await receive(attempt ? retryURL : url, headerMs, idleMs); break; }
    catch (error) {
      const network = error instanceof TypeError || ['TimeoutError', 'AbortError'].includes(error?.name);
      if (attempt || !network) throw error;
      onRetry();
      await new Promise(resolve => setTimeout(resolve, 120));
    }
  }
  // A failed response is discarded in full. Decode and integrity failures are
  // outside the retry loop; callers still check exact size, hash and geometry.
  return meshBuffer({ arrayBuffer: async () => encoded });
}
