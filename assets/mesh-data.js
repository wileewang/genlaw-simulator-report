// Decode the display transport without altering vertices or triangle indices.
export async function meshBuffer(response) {
  const limit = 64 * 1024 * 1024;
  const encoded = await response.arrayBuffer();
  if (encoded.byteLength > limit) throw Error('Encoded geometry exceeds byte budget');
  const magic = new Uint8Array(encoded, 0, Math.min(2, encoded.byteLength));
  if (magic[0] !== 0x1f || magic[1] !== 0x8b) return encoded;
  if (typeof DecompressionStream !== 'function') throw Error('Browser decompression unavailable');
  const reader = new Blob([encoded]).stream().pipeThrough(new DecompressionStream('gzip')).getReader();
  const chunks = []; let size = 0;
  try {
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) throw Error('Decoded geometry exceeds byte budget');
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes.buffer;
}
