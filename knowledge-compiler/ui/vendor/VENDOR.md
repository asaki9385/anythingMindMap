# Vendored map engine

These files are committed prebuilt artifacts so the app keeps its no-runtime-build
contract and works from `file://` (ES modules are blocked by CORS there).

## mind-elixir

- Upstream: https://github.com/SSShooter/mind-elixir-core
- Version: 6.0.0 (npm `mind-elixir@6.0.0`)
- License: MIT (see `LICENSE`)
- Files:
  - `mind-elixir.iife.js` — IIFE bundle exposing `window.KTME`
  - `MindElixir.css` — upstream compiled stylesheet
  - `Outliner.css` — upstream compiled outliner stylesheet
- Build (one-time, outside this repo):

```bash
mkdir -p /tmp/kt-vendor-build /tmp/kt-vendor-src
cd /tmp/kt-vendor-build
npm init -y
npm install --no-audit --no-fund mind-elixir@6.0.0 @mind-elixir/export-mindmap@0.1.11

cat > /tmp/kt-vendor-src/entry.js <<'EOF'
import MindElixir, {
  Outliner, THEME, DARK_THEME, LEFT, RIGHT, SIDE, DOWN, generateUUID,
} from 'mind-elixir';
import { zh_CN } from 'mind-elixir/i18n';
window.KTME = {
  MindElixir, Outliner, THEME, DARK_THEME,
  LEFT, RIGHT, SIDE, DOWN, generateUUID, zh_CN,
};
EOF

NODE_PATH=/tmp/kt-vendor-build/node_modules npx esbuild \
  /tmp/kt-vendor-src/entry.js \
  --bundle --format=iife --minify --target=es2020 \
  --legal-comments=inline \
  --outfile=/tmp/kt-vendor-src/mind-elixir.iife.js
```

## @mind-elixir/export-mindmap

- Upstream: https://github.com/mind-elixir/plugins (packages/export-mindmap)
- Version: 0.1.11
- License: MIT (declared in upstream `package.json`; no standalone LICENSE file
  is shipped in the npm tarball)
- File: `export-mindmap.umd.js` (upstream `dist/index.umd.cjs`, global
  `ExportMindmap`)

## Checksums (SHA-256)

```
411052e21d6c8161a4aa15d17941c69a900440b545f7e6aa5c2eb416ca0ae239  mind-elixir/mind-elixir.iife.js
bc64bb0237f3d76eb85a82d49d37126d80f50c4cbafab02a1b506a80dedbb155  mind-elixir/MindElixir.css
2346f77979dd25e874c31670a7af0747cb0d046e335a07fda49b358ea98086bf  mind-elixir/Outliner.css
7d3cb43b9cbfe6811670adab80e09a307c172d4ffabe9a8ec0f4ee56aa730674  export-mindmap/export-mindmap.umd.js
```

When updating the engine, rebuild both artifacts, update the version/checksums,
and run `test_map_adapter.js` + `test_ui_mindmap.js`.
