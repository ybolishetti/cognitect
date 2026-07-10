# public/

Static assets referenced by `app/layout.tsx` metadata (`icons`, `openGraph`, `twitter`).

All PNG/ICO files here are generated, not hand-drawn — see
`../scripts/generate-assets.mjs`. It renders React-element templates through
Next's bundled `next/og` (`ImageResponse`) using the brand accent `#2563EB`,
and packs the favicon sizes into `favicon.ico` by hand (no ICO-packing
dependency — the maintained options pull in an abandoned `jimp`/`request`
chain with critical-severity advisories).

Regenerate after changing the mark or OG copy:

```sh
npm run generate:assets
```

| File | Size | Used for |
| --- | --- | --- |
| `favicon.ico` | 16/32/48 | Browser tab (legacy `shortcut` icon) |
| `favicon-16x16.png` / `favicon-32x32.png` | 16×16 / 32×32 | Browser tab (`icons.icon`) |
| `apple-touch-icon.png` | 180×180 | iOS home screen / Safari (`icons.apple`) |
| `og-image.png` | 1200×630 | Open Graph + Twitter `summary_large_image` card |
| `og-image-square.png` | 1200×1200 | Spare square variant for surfaces that want a 1:1 crop |
