import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { Buffer } from "node:buffer";
import { URL, fileURLToPath } from "node:url";

import sharp from "sharp";

const frontendRoot = fileURLToPath(new URL("../", import.meta.url));
const publicRoot = `${frontendRoot}public`;
const brandRoot = `${publicRoot}/brand`;
const iconRoot = `${publicRoot}/icons`;

async function renderPng(source, destination, size) {
  await sharp(source)
    .resize(size, size)
    .png({ compressionLevel: 9, palette: false })
    .toFile(destination);
  return readFile(destination);
}

function createIco(images) {
  const headerSize = 6;
  const entrySize = 16;
  let offset = headerSize + entrySize * images.length;
  const header = Buffer.alloc(offset);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);

  images.forEach(({ size, png }, index) => {
    const entry = headerSize + index * entrySize;
    header.writeUInt8(size === 256 ? 0 : size, entry);
    header.writeUInt8(size === 256 ? 0 : size, entry + 1);
    header.writeUInt8(0, entry + 2);
    header.writeUInt8(0, entry + 3);
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(png.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += png.length;
  });

  return Buffer.concat([header, ...images.map(({ png }) => png)]);
}

await mkdir(iconRoot, { recursive: true });
await copyFile(
  `${brandRoot}/instaloader-webui-small.svg`,
  `${publicRoot}/favicon.svg`,
);

const favicon16 = await renderPng(
  `${brandRoot}/instaloader-webui-small.svg`,
  `${publicRoot}/favicon-16.png`,
  16,
);
const favicon32 = await renderPng(
  `${brandRoot}/instaloader-webui.svg`,
  `${publicRoot}/favicon-32.png`,
  32,
);
await writeFile(
  `${publicRoot}/favicon.ico`,
  createIco([
    { size: 16, png: favicon16 },
    { size: 32, png: favicon32 },
  ]),
);

await Promise.all([
  renderPng(
    `${brandRoot}/instaloader-webui.svg`,
    `${iconRoot}/icon-192.png`,
    192,
  ),
  renderPng(
    `${brandRoot}/instaloader-webui.svg`,
    `${iconRoot}/icon-512.png`,
    512,
  ),
  renderPng(
    `${brandRoot}/instaloader-webui-maskable.svg`,
    `${iconRoot}/icon-maskable-192.png`,
    192,
  ),
  renderPng(
    `${brandRoot}/instaloader-webui-maskable.svg`,
    `${iconRoot}/icon-maskable-512.png`,
    512,
  ),
  renderPng(
    `${brandRoot}/instaloader-webui.svg`,
    `${iconRoot}/desktop-icon-512.png`,
    512,
  ),
]);
