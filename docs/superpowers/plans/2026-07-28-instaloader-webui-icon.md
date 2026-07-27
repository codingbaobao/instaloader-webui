# Instaloader WebUI Icon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the approved Gallery Download × Archive Blue preview into durable favicon, PWA, desktop-ready, and in-product brand assets for Instaloader WebUI.

**Architecture:** Keep the approved SVG geometry in committed public vector files and use a deterministic Node script to derive all PNG and ICO assets. Small React brand components reference the committed full-detail SVG so login and navigation placements share one source. Focused Vitest coverage validates component accessibility, asset dimensions, manifest metadata, and document integration before the UI changes are made.

**Tech Stack:** React 18, TypeScript 5.8, Vite 7, Vitest 4, Testing Library, Node.js ESM, Sharp 0.35.3, SVG, Web App Manifest

## Global Constraints

- The visible and accessible product name is exactly `Instaloader WebUI`; never expose `iw` as a product mark.
- Use the approved Gallery Download silhouette; do not introduce camera, heart, follower, or notification motifs.
- Primary colors are Archive Ink `#111827`, Download Blue `#22A2F2`, and White `#FFFFFF`.
- Existing interface links and the `WebUI` suffix use `#0077C8`; the application background remains `#FAFAFA`.
- Browser and manifest `theme_color` are `#111827`; manifest `background_color` is `#FAFAFA`.
- The 16-pixel mark omits the circular media detail and uses the approved heavier small-size geometry.
- Exported artwork contains no gradients, embedded scripts, remote fonts, external resources, filters, or shadows.
- Maskable icon foreground geometry stays within the central 80% safe region.
- Do not redesign navigation symbols, page layouts, upstream Instaloader assets, or unrelated components.
- Preserve at least 80% frontend line, function, branch, and statement coverage.
- Follow RED → GREEN → REFACTOR for every task and commit only after its focused and regression checks pass.

---

## File Map

### New source and test files

- `frontend/src/brand/Brand.tsx` — reusable `BrandMark` and `BrandLockup` React components.
- `frontend/src/brand/Brand.test.tsx` — accessibility and naming tests for the reusable components.
- `frontend/src/brand/brandAssets.test.ts` — manifest, SVG safety, PNG dimensions, and ICO structure tests.
- `frontend/src/brand/documentBranding.test.ts` — document-head favicon, manifest, title, and theme tests.
- `frontend/scripts/generate-brand-assets.mjs` — deterministic SVG-to-PNG and PNG-to-ICO generator.

### New committed vector and generated asset files

- `frontend/public/brand/instaloader-webui.svg` — full-detail color vector master.
- `frontend/public/brand/instaloader-webui-small.svg` — optically adjusted small-size vector master.
- `frontend/public/brand/instaloader-webui-monochrome.svg` — one-color vector variant.
- `frontend/public/brand/instaloader-webui-maskable.svg` — full-canvas Archive Ink maskable source.
- `frontend/public/favicon.svg` — browser-facing copy of the small-size vector.
- `frontend/public/favicon-16.png` — 16-pixel source embedded in the ICO.
- `frontend/public/favicon-32.png` — 32-pixel modern browser fallback.
- `frontend/public/favicon.ico` — ICO containing 16- and 32-pixel PNG images.
- `frontend/public/icons/icon-192.png` — regular PWA icon.
- `frontend/public/icons/icon-512.png` — regular PWA icon.
- `frontend/public/icons/icon-maskable-192.png` — maskable PWA icon.
- `frontend/public/icons/icon-maskable-512.png` — maskable PWA icon.
- `frontend/public/icons/desktop-icon-512.png` — desktop-packaging source.
- `frontend/public/site.webmanifest` — PWA naming, colors, and icon declarations.

### Existing files to modify

- `frontend/package.json` — add `brand:generate` and Sharp 0.35.3.
- `frontend/package-lock.json` — lock the asset-generation dependency.
- `frontend/src/auth/LoginPage.tsx` — replace the `iw` block with the full brand lockup.
- `frontend/src/auth/LoginPage.test.tsx` — verify the public product name and removal of `iw`.
- `frontend/src/app/App.tsx` — use the full brand lockup in desktop and mobile home links.
- `frontend/src/app/App.test.tsx` — verify both branded home links expose the full accessible name.
- `frontend/src/styles/global.css` — style the approved icon and full-name lockups.
- `frontend/index.html` — declare the favicon set, manifest, application name, and exact theme color.

---

### Task 1: Reusable Brand Components

**Files:**
- Create: `frontend/src/brand/Brand.tsx`
- Create: `frontend/src/brand/Brand.test.tsx`

**Interfaces:**
- Produces: `PRODUCT_NAME: "Instaloader WebUI"`.
- Produces: `BrandMark({ className?, label? }: Readonly<{ className?: string; label?: string }>): JSX.Element`.
- Produces: `BrandLockup({ className? }: Readonly<{ className?: string }>): JSX.Element`.
- `BrandMark` is decorative when `label` is omitted and meaningful when `label` is provided.
- `BrandLockup` exposes one accessible name, `Instaloader WebUI`, while retaining separately styled visible `Instaloader` and `WebUI` spans.

- [ ] **Step 1: Write failing component tests**

Create `frontend/src/brand/Brand.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BrandLockup, BrandMark, PRODUCT_NAME } from "./Brand";

describe("BrandMark", () => {
  it("is decorative by default and uses the approved vector", () => {
    const { container } = render(<BrandMark />);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("alt", "");
    expect(container.querySelector("img")).toHaveAttribute(
      "src",
      "/brand/instaloader-webui.svg",
    );
  });

  it("can expose the full product name when meaningful", () => {
    render(<BrandMark label={PRODUCT_NAME} />);

    expect(screen.getByRole("img", { name: PRODUCT_NAME })).toBeVisible();
  });
});

describe("BrandLockup", () => {
  it("uses the full public product name without the iw abbreviation", () => {
    render(<BrandLockup />);

    expect(screen.getByLabelText(PRODUCT_NAME)).toBeVisible();
    expect(screen.getByText("Instaloader")).toBeVisible();
    expect(screen.getByText("WebUI")).toBeVisible();
    expect(screen.queryByText(/^iw$/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
cd frontend
npx vitest run src/brand/Brand.test.tsx
```

Expected: FAIL because `src/brand/Brand.tsx` does not exist.

- [ ] **Step 3: Implement the minimal reusable components**

Create `frontend/src/brand/Brand.tsx`:

```tsx
export const PRODUCT_NAME = "Instaloader WebUI";

type BrandMarkProps = Readonly<{
  className?: string;
  label?: string;
}>;

type BrandLockupProps = Readonly<{
  className?: string;
}>;

export function BrandMark({ className, label }: BrandMarkProps) {
  return (
    <img
      alt={label ?? ""}
      aria-hidden={label === undefined ? true : undefined}
      className={className}
      src="/brand/instaloader-webui.svg"
    />
  );
}

export function BrandLockup({ className }: BrandLockupProps) {
  const classes = ["brand-lockup", className].filter(Boolean).join(" ");

  return (
    <span aria-label={PRODUCT_NAME} className={classes}>
      <BrandMark className="brand-lockup-mark" />
      <span aria-hidden="true" className="brand-lockup-name">
        Instaloader
      </span>
      <span aria-hidden="true" className="brand-lockup-suffix">
        WebUI
      </span>
    </span>
  );
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
cd frontend
npx vitest run src/brand/Brand.test.tsx
```

Expected: all three brand component tests PASS.

- [ ] **Step 5: Refactor and run lint for the new files**

Run:

```powershell
cd frontend
npx eslint src/brand/Brand.tsx src/brand/Brand.test.tsx
```

Expected: PASS with no lint errors.

- [ ] **Step 6: Commit the reusable component**

```powershell
git add frontend/src/brand/Brand.tsx frontend/src/brand/Brand.test.tsx
git commit -m "feat: add reusable brand components"
```

---

### Task 2: Canonical SVGs, Generated Assets, and Manifest

**Files:**
- Create: `frontend/src/brand/brandAssets.test.ts`
- Create: `frontend/scripts/generate-brand-assets.mjs`
- Create: `frontend/public/brand/instaloader-webui.svg`
- Create: `frontend/public/brand/instaloader-webui-small.svg`
- Create: `frontend/public/brand/instaloader-webui-monochrome.svg`
- Create: `frontend/public/brand/instaloader-webui-maskable.svg`
- Create: `frontend/public/favicon.svg`
- Create: `frontend/public/favicon-16.png`
- Create: `frontend/public/favicon-32.png`
- Create: `frontend/public/favicon.ico`
- Create: `frontend/public/icons/icon-192.png`
- Create: `frontend/public/icons/icon-512.png`
- Create: `frontend/public/icons/icon-maskable-192.png`
- Create: `frontend/public/icons/icon-maskable-512.png`
- Create: `frontend/public/icons/desktop-icon-512.png`
- Create: `frontend/public/site.webmanifest`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Consumes: `BrandMark`'s fixed source path `/brand/instaloader-webui.svg`.
- Produces: deterministic public assets at the exact URLs declared by `site.webmanifest`.
- Produces: `npm run brand:generate`, which regenerates every PNG, ICO, and browser SVG from committed vector masters.
- The regular source retains the large-size media dot; the small source omits it and uses heavier strokes.

- [ ] **Step 1: Write failing asset-contract tests**

Create `frontend/src/brand/brandAssets.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const publicRoot = fileURLToPath(new URL("../../public/", import.meta.url));

function readPublic(relativePath: string): Buffer {
  return readFileSync(`${publicRoot}${relativePath}`);
}

function pngDimensions(relativePath: string): readonly [number, number] {
  const png = readPublic(relativePath);
  expect(png.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  return [png.readUInt32BE(16), png.readUInt32BE(20)];
}

describe("brand assets", () => {
  it("declares exact PWA naming, colors, and regular and maskable icons", () => {
    const manifest = JSON.parse(
      readPublic("site.webmanifest").toString("utf8"),
    ) as {
      name: string;
      short_name: string;
      theme_color: string;
      background_color: string;
      icons: Array<{ src: string; sizes: string; purpose?: string }>;
    };

    expect(manifest).toMatchObject({
      name: "Instaloader WebUI",
      short_name: "Instaloader WebUI",
      theme_color: "#111827",
      background_color: "#FAFAFA",
    });
    expect(manifest.icons).toEqual(
      expect.arrayContaining([
        { src: "/icons/icon-192.png", sizes: "192x192", purpose: "any" },
        { src: "/icons/icon-512.png", sizes: "512x512", purpose: "any" },
        {
          src: "/icons/icon-maskable-192.png",
          sizes: "192x192",
          purpose: "maskable",
        },
        {
          src: "/icons/icon-maskable-512.png",
          sizes: "512x512",
          purpose: "maskable",
        },
      ]),
    );
  });

  it("keeps SVG assets self-contained and uses the approved palette", () => {
    for (const filename of [
      "instaloader-webui.svg",
      "instaloader-webui-small.svg",
      "instaloader-webui-maskable.svg",
    ]) {
      const svg = readPublic(`brand/${filename}`).toString("utf8");
      expect(svg).toContain("#111827");
      expect(svg).toContain("#22A2F2");
      expect(svg).not.toMatch(/<script|<image|https?:|filter=|linearGradient/i);
    }

    const fullDetail = readPublic(
      "brand/instaloader-webui.svg",
    ).toString("utf8");
    const smallSize = readPublic(
      "brand/instaloader-webui-small.svg",
    ).toString("utf8");
    const maskable = readPublic(
      "brand/instaloader-webui-maskable.svg",
    ).toString("utf8");
    expect(fullDetail).toContain('<circle cx="43" cy="48"');
    expect(smallSize).not.toContain("<circle");
    expect(maskable).toContain(
      '<rect width="128" height="128" fill="#111827"/>',
    );

    const monochrome = readPublic(
      "brand/instaloader-webui-monochrome.svg",
    ).toString("utf8");
    expect(monochrome).toContain("#111827");
    expect(monochrome).not.toMatch(/#22A2F2|#FFFFFF/i);
  });

  it("exports every raster at its declared native size", () => {
    expect(pngDimensions("favicon-16.png")).toEqual([16, 16]);
    expect(pngDimensions("favicon-32.png")).toEqual([32, 32]);
    expect(pngDimensions("icons/icon-192.png")).toEqual([192, 192]);
    expect(pngDimensions("icons/icon-512.png")).toEqual([512, 512]);
    expect(pngDimensions("icons/icon-maskable-192.png")).toEqual([192, 192]);
    expect(pngDimensions("icons/icon-maskable-512.png")).toEqual([512, 512]);
    expect(pngDimensions("icons/desktop-icon-512.png")).toEqual([512, 512]);
  });

  it("packages 16- and 32-pixel images in the ICO", () => {
    const ico = readPublic("favicon.ico");

    expect(ico.readUInt16LE(0)).toBe(0);
    expect(ico.readUInt16LE(2)).toBe(1);
    expect(ico.readUInt16LE(4)).toBe(2);
    expect([ico.readUInt8(6), ico.readUInt8(22)]).toEqual([16, 32]);
  });
});
```

- [ ] **Step 2: Run the focused asset test and verify RED**

Run:

```powershell
cd frontend
npx vitest run src/brand/brandAssets.test.ts
```

Expected: FAIL with missing `frontend/public/site.webmanifest` and brand assets.

- [ ] **Step 3: Add the exact approved full-detail vector master**

Create `frontend/public/brand/instaloader-webui.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect x="6" y="6" width="116" height="116" rx="31" fill="#111827"/>
  <rect x="27" y="31" width="74" height="67" rx="20" fill="none" stroke="#FFFFFF" stroke-width="9"/>
  <path d="M64 42v35M50 63l14 15 14-15" fill="none" stroke="#22A2F2" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M43 94h42" fill="none" stroke="#FFFFFF" stroke-width="9" stroke-linecap="round"/>
  <circle cx="43" cy="48" r="5.5" fill="#FFFFFF"/>
</svg>
```

- [ ] **Step 4: Add the exact approved small-size vector master**

Create `frontend/public/brand/instaloader-webui-small.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect x="4" y="4" width="120" height="120" rx="31" fill="#111827"/>
  <rect x="26" y="30" width="76" height="69" rx="20" fill="none" stroke="#FFFFFF" stroke-width="12"/>
  <path d="M64 38v43M46 61l18 21 18-21" fill="none" stroke="#22A2F2" stroke-width="14" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M41 96h46" fill="none" stroke="#FFFFFF" stroke-width="11" stroke-linecap="round"/>
</svg>
```

- [ ] **Step 5: Add the monochrome and maskable vector masters**

Create `frontend/public/brand/instaloader-webui-monochrome.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect x="6" y="6" width="116" height="116" rx="31" fill="none" stroke="#111827" stroke-width="9"/>
  <rect x="27" y="31" width="74" height="67" rx="20" fill="none" stroke="#111827" stroke-width="9"/>
  <path d="M64 42v35M50 63l14 15 14-15" fill="none" stroke="#111827" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M43 94h42" fill="none" stroke="#111827" stroke-width="9" stroke-linecap="round"/>
  <circle cx="43" cy="48" r="5.5" fill="#111827"/>
</svg>
```

Create `frontend/public/brand/instaloader-webui-maskable.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect width="128" height="128" fill="#111827"/>
  <rect x="27" y="31" width="74" height="67" rx="20" fill="none" stroke="#FFFFFF" stroke-width="9"/>
  <path d="M64 42v35M50 63l14 15 14-15" fill="none" stroke="#22A2F2" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M43 94h42" fill="none" stroke="#FFFFFF" stroke-width="9" stroke-linecap="round"/>
  <circle cx="43" cy="48" r="5.5" fill="#FFFFFF"/>
</svg>
```

- [ ] **Step 6: Add Sharp and the generation command**

Run:

```powershell
cd frontend
npm install --save-dev sharp@0.35.3
```

Add this script to `frontend/package.json`:

```json
"brand:generate": "node scripts/generate-brand-assets.mjs"
```

Expected: `frontend/package.json` and `frontend/package-lock.json` record Sharp
0.35.3; production runtime dependencies remain unchanged.

- [ ] **Step 7: Implement deterministic PNG and ICO generation**

Create `frontend/scripts/generate-brand-assets.mjs`:

```js
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

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
```

Also add `frontend/public/favicon-16.png` to the Task 2 commit even though it is
consumed through the ICO rather than linked directly.

- [ ] **Step 8: Add the exact web app manifest**

Create `frontend/public/site.webmanifest`:

```json
{
  "name": "Instaloader WebUI",
  "short_name": "Instaloader WebUI",
  "description": "A personal library for downloading and archiving public Instagram media.",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "theme_color": "#111827",
  "background_color": "#FAFAFA",
  "icons": [
    {
      "src": "/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/icons/icon-maskable-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "maskable"
    },
    {
      "src": "/icons/icon-maskable-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    }
  ]
}
```

- [ ] **Step 9: Generate assets and verify GREEN**

Run:

```powershell
cd frontend
npm run brand:generate
npx vitest run src/brand/brandAssets.test.ts
```

Expected: asset generation succeeds and all four asset-contract tests PASS.

- [ ] **Step 10: Inspect native-size output and generator reproducibility**

Inspect these images with the workspace image viewer:

- `frontend/public/favicon-16.png` at original resolution.
- `frontend/public/favicon-32.png` at original resolution.
- `frontend/public/icons/icon-192.png`.
- `frontend/public/icons/icon-maskable-192.png`.

Confirm:

- The 16-pixel icon has no media dot and no collapsed stroke.
- The 32-pixel icon retains a distinct media dot.
- The maskable source has Archive Ink to every canvas edge.
- No exported PNG contains a shadow or gradient.

Then run:

```powershell
cd frontend
$before = Get-ChildItem public -Recurse -File | ForEach-Object {
  "$($_.FullName):$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)"
}
npm run brand:generate
$after = Get-ChildItem public -Recurse -File | ForEach-Object {
  "$($_.FullName):$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)"
}
$changes = Compare-Object $before $after
if ($changes) {
  throw "Brand asset generation is not deterministic: $changes"
}
```

Expected: the second generation creates no differences.

- [ ] **Step 11: Commit canonical and generated assets**

```powershell
git add frontend/package.json frontend/package-lock.json frontend/scripts/generate-brand-assets.mjs frontend/public frontend/src/brand/brandAssets.test.ts
git commit -m "feat: add Instaloader WebUI icon assets"
```

---

### Task 3: Document Metadata and Product UI Integration

**Files:**
- Create: `frontend/src/brand/documentBranding.test.ts`
- Modify: `frontend/index.html`
- Modify: `frontend/src/auth/LoginPage.tsx`
- Modify: `frontend/src/auth/LoginPage.test.tsx`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/app/App.test.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Consumes: `BrandLockup` from `frontend/src/brand/Brand.tsx`.
- Consumes: `/favicon.svg`, `/favicon.ico`, `/favicon-32.png`, `/icons/icon-192.png`, and `/site.webmanifest`.
- Produces: desktop and mobile home links named `Instaloader WebUI home`.
- Produces: a visible `Instaloader WebUI` lockup on the login screen.
- Produces: exact browser metadata using Archive Ink and the complete product name.

- [ ] **Step 1: Write failing document metadata tests**

Create `frontend/src/brand/documentBranding.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const indexPath = fileURLToPath(new URL("../../index.html", import.meta.url));

describe("document branding", () => {
  it("declares the approved product metadata and complete icon set", () => {
    const html = readFileSync(indexPath, "utf8");
    const parsed = new DOMParser().parseFromString(html, "text/html");

    expect(parsed.title).toBe("Instaloader WebUI");
    expect(
      parsed.querySelector('meta[name="application-name"]')?.getAttribute("content"),
    ).toBe("Instaloader WebUI");
    expect(
      parsed.querySelector('meta[name="theme-color"]')?.getAttribute("content"),
    ).toBe("#111827");
    expect(
      parsed.querySelector('link[rel="manifest"]')?.getAttribute("href"),
    ).toBe("/site.webmanifest");
    expect(
      parsed.querySelector('link[rel="icon"][type="image/svg+xml"]')
        ?.getAttribute("href"),
    ).toBe("/favicon.svg");
    expect(
      parsed.querySelector('link[rel="icon"][type="image/x-icon"]')
        ?.getAttribute("href"),
    ).toBe("/favicon.ico");
    expect(
      parsed.querySelector('link[rel="apple-touch-icon"]')?.getAttribute("href"),
    ).toBe("/icons/icon-192.png");
  });
});
```

- [ ] **Step 2: Extend existing UI tests before changing the UI**

Add this test to `frontend/src/auth/LoginPage.test.tsx`:

```tsx
it("shows the complete product brand without the iw abbreviation", () => {
  render(<TestRouter initialPath="/login" />);

  expect(screen.getByLabelText("Instaloader WebUI")).toBeVisible();
  expect(screen.queryByText(/^iw$/i)).not.toBeInTheDocument();
});
```

Extend the first test in `frontend/src/app/App.test.tsx` with:

```tsx
expect(
  screen.getAllByRole("link", { name: "Instaloader WebUI home" }),
).toHaveLength(2);
expect(screen.queryByText(/^iw$/i)).not.toBeInTheDocument();
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
cd frontend
npx vitest run src/brand/documentBranding.test.ts src/auth/LoginPage.test.tsx src/app/App.test.tsx
```

Expected failures:

- `index.html` still uses theme color `#ffffff` and has no favicon or manifest links.
- The login page has no accessible `Instaloader WebUI` lockup and still renders `iw`.
- The mobile home link does not yet expose `Instaloader WebUI home`.

- [ ] **Step 4: Add exact document metadata**

Update the `<head>` in `frontend/index.html` to contain:

```html
<meta name="theme-color" content="#111827" />
<meta name="application-name" content="Instaloader WebUI" />
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png" />
<link rel="icon" type="image/x-icon" href="/favicon.ico" />
<link rel="apple-touch-icon" href="/icons/icon-192.png" />
<link rel="manifest" href="/site.webmanifest" />
<title>Instaloader WebUI</title>
```

- [ ] **Step 5: Replace login and navigation marks with `BrandLockup`**

In `frontend/src/auth/LoginPage.tsx`, import `BrandLockup`:

```tsx
import { BrandLockup } from "../brand/Brand";
```

Replace:

```tsx
<div className="brand-mark" aria-hidden="true">
  iw
</div>
```

with:

```tsx
<BrandLockup className="auth-brand" />
```

In `frontend/src/app/App.tsx`, import the component:

```tsx
import { BrandLockup } from "../brand/Brand";
```

Replace both desktop and mobile wordmark contents with:

```tsx
<NavLink
  className="wordmark"
  to="/"
  aria-label="Instaloader WebUI home"
>
  <BrandLockup />
</NavLink>
```

- [ ] **Step 6: Apply the approved lockup styling**

Remove the old `.brand-mark` declaration from
`frontend/src/styles/global.css` and add:

```css
.brand-lockup {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #161616;
  white-space: nowrap;
}

.brand-lockup-mark {
  display: block;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
}

.brand-lockup-name {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 23px;
  font-weight: 700;
  letter-spacing: -.025em;
}

.brand-lockup-suffix {
  margin-left: -3px;
  color: #0077c8;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: .09em;
  text-transform: uppercase;
}

.auth-brand {
  margin-bottom: 26px;
}

.auth-brand .brand-lockup-mark {
  width: 48px;
  height: 48px;
}

.auth-brand .brand-lockup-name {
  font-size: 24px;
}

.auth-brand .brand-lockup-suffix {
  font-size: 10px;
}
```

Replace the existing `.wordmark` declaration with:

```css
.wordmark {
  display: inline-flex;
  width: max-content;
  color: #161616;
  text-decoration: none;
}
```

The 8-pixel internal lockup gap plus surrounding existing layout spacing keeps
adjacent controls visually separate without altering page architecture.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run:

```powershell
cd frontend
npx vitest run src/brand/documentBranding.test.ts src/brand/Brand.test.tsx src/brand/brandAssets.test.ts src/auth/LoginPage.test.tsx src/app/App.test.tsx
```

Expected: all focused brand, asset, login, and application tests PASS.

- [ ] **Step 8: Run the full frontend verification loop**

Run:

```powershell
cd frontend
npm test
npm run lint
npm run build
```

Expected:

- Vitest passes with at least 80% lines, functions, branches, and statements.
- ESLint reports no errors.
- TypeScript and Vite build successfully.
- `dist/` contains `site.webmanifest`, favicon files, brand SVGs, and PWA icons.

- [ ] **Step 9: Verify built asset presence and immutable generation**

Run:

```powershell
cd frontend
$required = @(
  "dist/favicon.svg",
  "dist/favicon-16.png",
  "dist/favicon-32.png",
  "dist/favicon.ico",
  "dist/site.webmanifest",
  "dist/brand/instaloader-webui.svg",
  "dist/icons/icon-192.png",
  "dist/icons/icon-512.png",
  "dist/icons/icon-maskable-192.png",
  "dist/icons/icon-maskable-512.png",
  "dist/icons/desktop-icon-512.png"
)
$missing = $required | Where-Object { -not (Test-Path $_) }
if ($missing.Count -gt 0) {
  throw "Missing built brand assets: $($missing -join ', ')"
}
npm run brand:generate
git diff --exit-code -- public
```

Expected: no required asset is missing and regeneration produces no diff.

- [ ] **Step 10: Review the final diff and perform the security gate**

Run:

```powershell
git diff --check
git diff
cd frontend
npm audit --audit-level=high
```

Expected:

- No whitespace errors.
- The diff contains only approved brand assets, metadata, components, styles,
  tests, and the Sharp development dependency.
- No hardcoded credential, token, cookie, remote SVG resource, or executable SVG
  content is present.
- Any pre-existing audit advisory is recorded separately; no new critical or
  high advisory is introduced by Sharp.

- [ ] **Step 11: Commit product integration**

```powershell
git add frontend/index.html frontend/src/app/App.tsx frontend/src/app/App.test.tsx frontend/src/auth/LoginPage.tsx frontend/src/auth/LoginPage.test.tsx frontend/src/brand/documentBranding.test.ts frontend/src/styles/global.css
git commit -m "feat: apply Instaloader WebUI branding"
```

---

## Plan Self-Review

- Spec coverage: Tasks 1–3 cover full naming, two SVG masters, monochrome and
  maskable variants, ICO/PNG/PWA outputs, exact colors, manifest metadata,
  favicon declarations, login integration, desktop/mobile integration,
  accessibility, native-size inspection, and build verification.
- Scope: The plan does not modify navigation symbols, layouts, upstream assets,
  backend code, or unrelated components.
- Type consistency: `BrandMark`, `BrandLockup`, and `PRODUCT_NAME` are defined
  once in Task 1 and consumed under the same names in Task 3.
- Generator consistency: Manifest URLs, test paths, generator outputs, HTML
  links, and built-asset checks use identical filenames.
- Placeholder scan: The plan contains no deferred implementation marker or
  unspecified code step.
