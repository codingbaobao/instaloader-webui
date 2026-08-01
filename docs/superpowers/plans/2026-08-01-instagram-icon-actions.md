# Instagram Icon Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the profile and media Instagram text links plus the downloaded-media delete text button with the approved accessible icon-only actions.

**Architecture:** Add two dependency-free decorative SVG components and keep interaction semantics on the existing anchor and button elements. Both pages share one neutral `icon-action` CSS contract, while media deletion adds a destructive modifier and continues to open the existing confirmation dialog.

**Tech Stack:** React 18, TypeScript 5.8, CSS, Testing Library, Vitest 4, Node 22

## Global Constraints

- Use a 38-by-38-pixel rounded-square pointer target.
- Instagram actions use a black glyph on `#efefef`; delete uses a red glyph on `#fff1f2`.
- Do not render visible text inside the three icon actions.
- Preserve complete accessible names, keyboard focus, hover/focus tooltips, safe new-tab attributes, and media-delete confirmation.
- Do not add dependencies or change APIs, routes, persistence, or download behavior.
- Execute frontend verification under Node 22, matching `.github/workflows/ci.yml`.

---

## File Structure

- `frontend/src/library/ActionIcons.tsx` — dependency-free decorative Instagram and trash SVG glyphs.
- `frontend/src/library/ProfilePage.tsx` — canonical profile Instagram icon link.
- `frontend/src/library/ProfilePage.test.tsx` — profile link semantics and icon-only regression coverage.
- `frontend/src/library/MediaViewerPage.tsx` — grouped Instagram and delete icon actions.
- `frontend/src/library/MediaViewerPage.test.tsx` — media link, delete control, and confirmation coverage.
- `frontend/src/styles/global.css` — shared neutral icon action, destructive modifier, tooltip, and viewer action-row styling.

### Task 1: Implement Accessible Instagram and Delete Icon Actions

**Files:**
- Create: `frontend/src/library/ActionIcons.tsx`
- Modify: `frontend/src/library/ProfilePage.tsx:7-15,195-209`
- Modify: `frontend/src/library/MediaViewerPage.tsx:6-16,135-141`
- Modify: `frontend/src/styles/global.css:104-125,289-305,341-345`
- Test: `frontend/src/library/ProfilePage.test.tsx:44-76`
- Test: `frontend/src/library/MediaViewerPage.test.tsx:1-4,131-152`

**Interfaces:**
- Produces: `InstagramIcon(): JSX.Element`
- Produces: `TrashIcon(): JSX.Element`
- Produces: CSS classes `icon-action`, `icon-action-danger`, and `action-icon`
- Preserves: profile and media URL sources, safe external-link attributes, and `ConfirmDialog` deletion flow

- [ ] **Step 1: Write failing profile and media action tests**

Extend the profile link assertions with icon-only and tooltip requirements:

```tsx
expect(instagramLink).toHaveAttribute(
  "data-tooltip",
  "Open @katerina.soria on Instagram",
);
expect(instagramLink).not.toHaveTextContent("Instagram");
expect(instagramLink.querySelector("svg")).toHaveAttribute(
  "aria-hidden",
  "true",
);
expect(instagramLink.querySelector("svg")).toHaveAttribute(
  "focusable",
  "false",
);
```

Add `within` and `userEvent`, then add a media action test:

```tsx
it("groups icon-only media actions and preserves delete confirmation", async () => {
  const user = userEvent.setup();
  renderViewer(reelFixture);

  expect(
    await screen.findByRole("heading", { name: "Reel" }),
  ).toBeVisible();
  const actions = screen.getByRole("group", { name: "Media actions" });
  const instagramLink = within(actions).getByRole("link", {
    name: "Open original on Instagram",
  });
  expect(instagramLink).toHaveAttribute("href", reelFixture.original_url);
  expect(instagramLink).toHaveAttribute("target", "_blank");
  expect(instagramLink).toHaveAttribute("rel", "noopener noreferrer");
  expect(instagramLink).not.toHaveTextContent("Open original on Instagram");

  const deleteButton = within(actions).getByRole("button", {
    name: "Delete downloaded media",
  });
  expect(deleteButton).not.toHaveTextContent("Delete downloaded media");
  expect(deleteButton).toHaveAttribute(
    "data-tooltip",
    "Delete downloaded media",
  );

  await user.click(deleteButton);
  expect(
    screen.getByRole("dialog", { name: "Delete this media item?" }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Delete media" }),
  ).toBeVisible();
});
```

- [ ] **Step 2: Run the focused tests and verify the RED state**

Run from `frontend/`:

```bash
npx --yes node@22 ./node_modules/vitest/vitest.mjs run \
  src/library/ProfilePage.test.tsx \
  src/library/MediaViewerPage.test.tsx
```

Expected: FAIL because the profile link still contains visible `Instagram`
text, the media action group does not exist, and the delete control has no
icon-only accessible form.

- [ ] **Step 3: Add the decorative SVG icon components**

Create `ActionIcons.tsx` with fixed decorative SVGs:

```tsx
export function InstagramIcon() {
  return (
    <svg
      aria-hidden="true"
      className="action-icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <rect height="18" rx="5" stroke="currentColor" strokeWidth="2" width="18" x="3" y="3" />
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="2" />
      <circle cx="17.5" cy="6.5" fill="currentColor" r="1" />
    </svg>
  );
}

export function TrashIcon() {
  return (
    <svg
      aria-hidden="true"
      className="action-icon"
      fill="none"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <path
        d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}
```

- [ ] **Step 4: Replace the profile and media text actions**

Import the matching icons into each page. The profile anchor becomes:

```tsx
<a
  aria-label={`Open @${profile.username} on Instagram`}
  className="icon-action"
  data-tooltip={`Open @${profile.username} on Instagram`}
  href={`https://www.instagram.com/${encodeURIComponent(profile.username)}/`}
  rel="noopener noreferrer"
  target="_blank"
>
  <InstagramIcon />
</a>
```

Move both media controls into one labelled group:

```tsx
<div aria-label="Media actions" className="viewer-actions" role="group">
  <a
    aria-label="Open original on Instagram"
    className="icon-action"
    data-tooltip="Open original on Instagram"
    href={media.original_url}
    rel="noopener noreferrer"
    target="_blank"
  >
    <InstagramIcon />
  </a>
  <button
    aria-label="Delete downloaded media"
    className="icon-action icon-action-danger"
    data-tooltip="Delete downloaded media"
    type="button"
    onClick={() => setDeleteOpen(true)}
  >
    <TrashIcon />
  </button>
</div>
```

- [ ] **Step 5: Add the approved icon-action styling**

Replace the profile text-link rules and specialize the media action row:

```css
.icon-action {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  padding: 0;
  border: 0;
  border-radius: 10px;
  color: #262626;
  background: #efefef;
  text-decoration: none;
}

.icon-action:hover { background: #dbdbdb; }
.icon-action-danger { color: #d92d20; background: #fff1f2; }
.icon-action-danger:hover { background: #ffe4e6; }
.action-icon { width: 19px; height: 19px; }

.icon-action::after {
  position: absolute;
  z-index: 10;
  bottom: calc(100% + 8px);
  left: 50%;
  width: max-content;
  max-width: 220px;
  padding: 6px 9px;
  border-radius: 6px;
  color: #fff;
  background: #262626;
  content: attr(data-tooltip);
  font-size: 11px;
  font-weight: 700;
  line-height: 1.3;
  opacity: 0;
  pointer-events: none;
  transform: translate(-50%, 3px);
  transition: opacity .15s ease, transform .15s ease;
}

.icon-action:hover::after,
.icon-action:focus-visible::after {
  opacity: 1;
  transform: translate(-50%, 0);
}

.viewer-actions {
  justify-content: flex-end;
  margin-top: 26px;
  padding-top: 15px;
  border-top: 1px solid #efefef;
}
```

Keep the existing global `:focus-visible` outline so icon actions receive the
same three-pixel keyboard focus treatment as the rest of the application.

- [ ] **Step 6: Run the focused tests and verify the GREEN state**

Run the Step 2 command again.

Expected: 2 test files pass with all profile and media action assertions green.

- [ ] **Step 7: Run frontend verification**

Run from `frontend/`:

```bash
npx --yes node@22 ./node_modules/vitest/vitest.mjs run
npx --yes node@22 ./node_modules/eslint/bin/eslint.js .
npx --yes node@22 ./node_modules/typescript/bin/tsc -b
npx --yes node@22 ./node_modules/vite/bin/vite.js build
```

Expected: 10 test files pass, ESLint exits zero, TypeScript exits zero, and
Vite produces the production build without errors.

- [ ] **Step 8: Commit the implementation**

```bash
git add \
  frontend/src/library/ActionIcons.tsx \
  frontend/src/library/ProfilePage.tsx \
  frontend/src/library/ProfilePage.test.tsx \
  frontend/src/library/MediaViewerPage.tsx \
  frontend/src/library/MediaViewerPage.test.tsx \
  frontend/src/styles/global.css
git commit -m "feat: use icon actions for instagram links"
```
