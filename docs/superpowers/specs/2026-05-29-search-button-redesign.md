# Search Button Redesign Spec

## Overview

Replace the current emoji-based search trigger button with a compact circular SVG magnifying glass button that matches the "Warm Academic" design system.

## Problem

The current search toggle button uses an emoji (🔍) and a narrow vertical tab shape (28×56px) that visually diverges from the page's standard control style.

## Solution

Replace with a 40×40px circular button featuring an inline SVG magnifying glass icon. The button stays pinned to the left edge, vertically centered.

## Design Details

### Button Shape

- Size: 40×40px
- Border radius: `0 50% 50% 0` (left-side flat, right-side fully rounded)
- Position: fixed, `top: 50%; left: 0; transform: translateY(-50%)`
- No left border, blends with page edge

### SVG Icon

```html
<svg width="20" height="20" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round">
  <circle cx="11" cy="11" r="8"/>
  <path d="M21 21l-4.35-4.35"/>
</svg>
```

- Uses `currentColor` so stroke inherits from the button's `color` property
- 20×20px rendered size within the 40px button

### States

| State | Background | Icon Color | Notes |
|-------|-----------|------------|-------|
| Default | `var(--surface)` | `var(--ink-secondary)` | Subtle, blends with background |
| Hover | `var(--surface-raised)` | `var(--accent-hover)` | Amber highlight on hover |
| Panel open | `var(--surface)` | — | `opacity: 0; pointer-events: none` |

### CSS Changes

Remove the old `.search-toggle-btn` styles and replace with:

```css
.search-toggle-btn {
  position: fixed;
  top: 50%;
  left: 0;
  transform: translateY(-50%);
  z-index: 501;
  width: 40px;
  height: 40px;
  border-radius: 0 var(--radius-full) var(--radius-full) 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: none;
  box-shadow: 2px 0 12px var(--shadow-color);
  color: var(--ink-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition: opacity 0.3s ease, background 0.2s, color 0.2s;
}

.search-toggle-btn:hover {
  background: var(--surface-raised);
  color: var(--accent-hover);
}

.search-toggle-btn.shifted {
  opacity: 0;
  pointer-events: none;
}
```

Removed properties from old design:
- `writing-mode: vertical-lr`
- `letter-spacing: 2px`
- `font-size: 12px`
- Fixed `width: 28px; height: 56px`

### HTML Changes

Replace emoji content with inline SVG in both `tree_mindmap.html` and `upload_mindmap.html`:

```html
<!-- Before -->
<button class="search-toggle-btn" id="searchToggleBtn" onclick="toggleSearch()" title="Search (Ctrl+F)">🔍</button>

<!-- After -->
<button class="search-toggle-btn" id="searchToggleBtn" onclick="toggleSearch()" title="Search (Ctrl+F)">
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="11" cy="11" r="8"/>
    <path d="M21 21l-4.35-4.35"/>
  </svg>
</button>
```

### Theme Adaptation

The SVG automatically adapts to light/dark mode via `currentColor`:
- Dark mode: stroke inherits `--ink-secondary` (#a89888)
- Light mode: stroke inherits `--ink-secondary` (#6b5b4e)

## Files to Modify

1. `knowledge-compiler/ui/tree_mindmap.html` — CSS (lines 515-543) + HTML (line 806)
2. `knowledge-compiler/ui/upload_mindmap.html` — CSS (lines 753-783) + HTML (line 1156)

## Scope

- No changes to search panel behavior or layout
- No changes to `toggleSearch()` JavaScript function
- No changes to keyboard shortcut (Ctrl+F)
- CSS-only changes to button appearance + HTML content swap
