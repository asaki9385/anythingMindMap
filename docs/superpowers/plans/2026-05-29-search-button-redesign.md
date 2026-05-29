# Search Button Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace emoji search trigger button with compact circular SVG magnifying glass button across both mindmap pages.

**Architecture:** CSS style replacement + inline SVG swap in two HTML files. No JS changes needed. The SVG uses `currentColor` for automatic theme adaptation.

**Tech Stack:** HTML, CSS, inline SVG

---

## File Structure

| File | Change |
|------|--------|
| `knowledge-compiler/ui/tree_mindmap.html` | Lines 515-543: CSS replacement. Line 806: HTML swap |
| `knowledge-compiler/ui/upload_mindmap.html` | Lines 753-783: CSS replacement. Line 1156: HTML swap |

---

### Task 1: Update tree_mindmap.html CSS

**Files:**
- Modify: `knowledge-compiler/ui/tree_mindmap.html:515-543`

- [ ] **Step 1: Replace search-toggle-btn CSS block**

Replace lines 515-543 (the entire `.search-toggle-btn` section) with:

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

- [ ] **Step 2: Verify CSS syntax**

Open `tree_mindmap.html` in a browser. The search button should not be visible yet (data must load first), but the page should render without CSS errors.

- [ ] **Step 3: Commit**

```bash
git add knowledge-compiler/ui/tree_mindmap.html
git commit -m "fix: update search button CSS to compact circular shape in tree_mindmap"
```

---

### Task 2: Update tree_mindmap.html HTML

**Files:**
- Modify: `knowledge-compiler/ui/tree_mindmap.html:806`

- [ ] **Step 1: Replace emoji with inline SVG**

Replace line 806:

```html
<button class="search-toggle-btn" id="searchToggleBtn" onclick="toggleSearch()" title="Search (Ctrl+F)">🔍</button>
```

With:

```html
<button class="search-toggle-btn" id="searchToggleBtn" onclick="toggleSearch()" title="Search (Ctrl+F)">
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="11" cy="11" r="8"/>
    <path d="M21 21l-4.35-4.35"/>
  </svg>
</button>
```

- [ ] **Step 2: Verify in browser**

Open `tree_mindmap.html`, load a tree JSON file. The search button should appear as a 40px circular SVG magnifying glass on the left edge. Hover should highlight in amber. Click should open the search panel and hide the button.

- [ ] **Step 3: Commit**

```bash
git add knowledge-compiler/ui/tree_mindmap.html
git commit -m "feat: replace emoji with SVG magnifying glass in tree_mindmap"
```

---

### Task 3: Update upload_mindmap.html CSS

**Files:**
- Modify: `knowledge-compiler/ui/upload_mindmap.html:753-783`

- [ ] **Step 1: Replace search-toggle-btn CSS block**

Replace lines 753-783 (the entire `.search-toggle-btn` section) with:

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
  display: none;
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

Note: `display: none` is preserved — this button is shown via JS after data loads.

- [ ] **Step 2: Commit**

```bash
git add knowledge-compiler/ui/upload_mindmap.html
git commit -m "fix: update search button CSS to compact circular shape in upload_mindmap"
```

---

### Task 4: Update upload_mindmap.html HTML

**Files:**
- Modify: `knowledge-compiler/ui/upload_mindmap.html:1156`

- [ ] **Step 1: Replace emoji with inline SVG**

Replace line 1156:

```html
<button class="search-toggle-btn" id="searchToggleBtn" onclick="toggleSearch()" title="Search (Ctrl+F)">🔍</button>
```

With:

```html
<button class="search-toggle-btn" id="searchToggleBtn" onclick="toggleSearch()" title="Search (Ctrl+F)">
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="11" cy="11" r="8"/>
    <path d="M21 21l-4.35-4.35"/>
  </svg>
</button>
```

- [ ] **Step 2: Verify in browser**

Open `upload_mindmap.html`, upload a PDF. After tree renders, the search button should appear as a 40px circular SVG magnifying glass on the left edge. Hover highlights amber. Click opens search panel.

- [ ] **Step 3: Commit**

```bash
git add knowledge-compiler/ui/upload_mindmap.html
git commit -m "feat: replace emoji with SVG magnifying glass in upload_mindmap"
```

---

### Task 5: Final Verification

- [ ] **Step 1: Test both pages**

1. Open `tree_mindmap.html` → load a tree → verify button appearance, hover, click, panel open/close, Ctrl+F shortcut
2. Open `upload_mindmap.html` → upload a PDF → verify same behaviors
3. Toggle light/dark mode on both pages → verify SVG color adapts

- [ ] **Step 2: Commit cleanup**

```bash
git add -A
git commit -m "chore: search button redesign complete"
```
