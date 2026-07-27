# Instaloader WebUI Icon and Brand Mark Design

Date: 2026-07-28
Status: Design approved; written specification awaiting review

## 1. Purpose

Create one coherent visual identity for Instaloader WebUI across:

- Browser favicons at 16 and 32 pixels.
- PWA and desktop application icons at 192 and 512 pixels.
- Maskable PWA icons.
- The login screen, desktop sidebar, mobile header, and other in-product brand placements.

The identity should communicate a private media download and archive tool. It
should not resemble a social network or an unofficial Instagram client.

## 2. Approved Direction

The approved concept is **Gallery Download × Archive Blue**.

The symbol combines three ideas:

- A rounded media frame represents saved photos and videos.
- A centered downward arrow represents downloading.
- The frame's lower edge doubles as an archive tray, representing durable local
  storage.

A small circular media detail appears in larger versions. The 16-pixel favicon
removes this detail to preserve a clear silhouette.

The symbol deliberately avoids camera, heart, notification, follower, and other
social-network motifs. It also avoids reproducing Instagram's camera glyph.

## 3. Product Name and Lockups

The public product name is always **Instaloader WebUI**.

- `iw` is not a public product name or visible brand mark.
- The standalone icon contains no letters.
- Narrow placements use the icon alone.
- Placements with sufficient horizontal room use the icon followed by the full
  `Instaloader WebUI` name.
- Internal identifiers may continue using existing technical abbreviations when
  they are not user-visible.

The full horizontal lockup retains the interface's existing typographic
relationship:

- `Instaloader` uses the existing Georgia-style serif wordmark.
- `WebUI` uses a compact uppercase sans-serif label.
- The complete accessible name remains “Instaloader WebUI,” even where the two
  visual parts use different typographic treatments.

## 4. Color System

The approved color palette is:

| Token | Value | Use |
| --- | --- | --- |
| Archive Ink | `#111827` | Primary tile and dark structural color |
| Download Blue | `#22A2F2` | Download arrow and primary brand accent |
| White | `#FFFFFF` | Media frame and high-contrast details |
| Interface Link Blue | `#0077C8` | Existing WebUI links and the `WebUI` wordmark label |
| Application Background | `#FAFAFA` | Existing application and PWA launch background |

The primary icon uses a solid Archive Ink tile, a White media frame, and a
Download Blue arrow. It does not use a gradient.

A one-color variant must also be provided for contexts that cannot reproduce the
three-color mark. The one-color variant preserves the same geometry and uses
only the surrounding text or surface color.

## 5. Master Geometry

The master artwork uses a `128 × 128` view box.

### 5.1 Full-detail mark

- Outer tile: `x=6`, `y=6`, `width=116`, `height=116`, `rx=31`.
- Media frame: `x=27`, `y=31`, `width=74`, `height=67`, `rx=20`, White,
  9-unit stroke.
- Arrow stem: centered at `x=64`, from approximately `y=42` to `y=77`.
- Arrow head: approximately `(50,63) → (64,78) → (78,63)`, Download Blue,
  10-unit rounded stroke.
- Archive line: approximately `(43,94) → (85,94)`, White, 9-unit rounded
  stroke.
- Media detail: circle centered near `(43,48)` with a radius of approximately
  `5.5`, White.

The media frame, arrow, and archive line use rounded caps and joins. Optical
adjustments are allowed during final vector preparation when they improve
small-size rasterization without changing the approved silhouette.

### 5.2 Small-size mark

The 16-pixel favicon:

- Removes the circular media detail.
- Slightly increases the effective frame, arrow, and archive-line weight.
- Preserves the same outer tile, media frame, centered arrow, and archive-tray
  silhouette.
- Is manually inspected at native size rather than accepted solely from an
  automatic downscale.

The 32-pixel favicon retains the circular media detail when it remains distinct
after rasterization.

## 6. Asset Variants

The implementation should produce one canonical vector package with two
deliberate SVG masters: the full-detail mark and the optically adjusted
small-size mark. Both masters share the approved proportions, colors, and core
silhouette; the small-size master contains only the exceptions listed in
Section 5.2.

| Asset | Required form |
| --- | --- |
| Full-detail master | SVG using the approved geometry and palette |
| Small-size master | SVG with the approved 16-pixel optical adjustments |
| Standalone interface mark | SVG, scalable and accessible through its consuming component |
| Monochrome mark | SVG using one foreground color |
| Browser favicon | ICO containing 16- and 32-pixel images |
| Modern browser icon | 32-pixel PNG and SVG favicon where supported |
| PWA regular icons | 192×192 and 512×512 PNG |
| PWA maskable icons | 192×192 and 512×512 PNG |
| Desktop-ready source | 512×512 PNG suitable for downstream platform packaging |

Regular application icons use the approved rounded Archive Ink tile. Maskable
icons extend Archive Ink to the full canvas and keep all essential foreground
geometry inside the central 80% safe region so platform masks do not crop it.

## 7. Product Integration

Implementation should apply the identity consistently:

- Replace the login screen's `iw` gradient block with the new icon.
- Add the icon beside the existing desktop and mobile wordmark where space
  permits.
- Preserve an icon-only form for constrained placements.
- Add favicon declarations to the document head.
- Add a web app manifest with the regular and maskable PWA assets.
- Set the browser theme color and manifest `theme_color` to Archive Ink
  `#111827`.
- Set the manifest `background_color` to the existing Application Background
  `#FAFAFA`.
- Use “Instaloader WebUI” for document, manifest, and accessible product names.

The implementation should not redesign navigation icons, page layouts, or
unrelated interface components.

## 8. Accessibility and Rendering

- Decorative instances use empty alternative text or `aria-hidden="true"`.
- Standalone meaningful instances expose the accessible name “Instaloader
  WebUI.”
- The product name is never conveyed only through the artwork.
- The mark must remain distinguishable on white, near-white, and Archive Ink
  surfaces.
- SVGs must not load remote fonts, scripts, raster images, or external
  resources.
- Raster exports should use a color-managed sRGB workflow and retain crisp
  edges at native size.

## 9. Usage Rules

- Do not place `iw` inside or beside the public brand mark.
- Do not recolor the primary mark with an Instagram-like gradient.
- Do not add camera, heart, follower, or notification imagery.
- Do not stretch, rotate, outline, bevel, or add a drop shadow inside exported
  artwork.
- UI mockups may apply surface shadows around an application tile, but the
  exported icon itself remains shadow-free.
- Keep clear space around the standalone mark equal to at least one quarter of
  its tile width when layout permits.

## 10. Validation and Acceptance Criteria

The implementation is accepted when:

- The same approved silhouette appears in the favicon, PWA, desktop-ready, and
  interface assets.
- The 16-pixel favicon is visually recognizable at 100% scale and has no
  collapsed strokes.
- Regular and maskable PWA icons pass manifest validation and retain all
  essential geometry under common masks.
- The login screen and interface no longer display `iw` as the product mark.
- All visible and accessible product naming uses “Instaloader WebUI.”
- The primary colors match the specified hex values.
- Light- and dark-surface checks show sufficient separation without adding an
  unapproved outline.
- Existing frontend tests pass, and focused tests cover the new document
  metadata and brand-mark rendering where practical.

## 11. Out of Scope

- Renaming the product from Instaloader WebUI.
- Redesigning the overall application interface.
- Replacing all navigation symbols.
- Modifying the upstream Instaloader logo or documentation assets.
- Creating social-media campaign graphics, splash screens, or animated logo
  treatments.
