import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';

const manifest = JSON.parse(await readFile(new URL('../src/download.json', import.meta.url), 'utf8'));
if (!/^[A-Za-z0-9.-]+\.zip$/.test(manifest.filename) || !/^[a-f0-9]{64}$/.test(manifest.sha256)) throw Error('Invalid download manifest');
const folder = new URL('../public/downloads/', import.meta.url);
await mkdir(folder, { recursive: true });
const file = new URL(manifest.filename, folder);
async function checksum(target) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(target)) hash.update(chunk);
  return hash.digest('hex');
}
let exists = false;
try { await stat(file); exists = true; } catch (error) { if (error.code !== 'ENOENT') throw error; }
if (!exists) {
  const response = await fetch(manifest.releaseUrl, { signal: AbortSignal.timeout(300000) });
  if (!response.ok) throw Error(`Download package is not published (HTTP ${response.status}). Upload the release asset described in DOWNLOAD-DEPLOY.md before deploying the site.`);
  const temporary = new URL(manifest.filename + '.part', folder);
  try {
    await pipeline(Readable.fromWeb(response.body), createWriteStream(temporary));
    if (await checksum(temporary) !== manifest.sha256) throw Error('Download package SHA-256 mismatch');
    await rename(temporary, file);
  } finally { await rm(temporary, { force: true }); }
}
if (await checksum(file) !== manifest.sha256 || (await stat(file)).size !== manifest.bytes) throw Error('Download package does not match the manifest');
await writeFile(new URL(manifest.filename + '.sha256', folder), `${manifest.sha256}  ${manifest.filename}\n`);
console.log(`Download package verified: ${manifest.filename} (${manifest.bytes} bytes)`);
