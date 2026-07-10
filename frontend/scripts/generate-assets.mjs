// Generates frontend/public/{favicon*, apple-touch-icon, og-image*} from React-element
// templates via next/og's ImageResponse (Satori + resvg, bundled with Next — no extra
// runtime deps). Re-run with `npm run generate:assets` whenever the brand mark or OG
// copy changes; the extensionless "next/og" specifier only resolves through Next's own
// bundler, so this plain Node script imports the shipped file directly as "next/og.js".
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import React from "react";
import { ImageResponse } from "next/og.js";

const BLUE = "#2563EB";
const __dirname = dirname(fileURLToPath(import.meta.url));
const publicDir = join(__dirname, "..", "public");
mkdirSync(publicDir, { recursive: true });

async function toPngBuffer(element, width, height) {
  const res = new ImageResponse(element, { width, height });
  return Buffer.from(await res.arrayBuffer());
}

// A minimal, legible mark at every size: a solid blue square with a bold white "C".
// Left un-rounded on purpose — iOS applies its own corner mask to apple-touch-icon,
// and browsers render small favicons fine as plain squares.
function markElement(size) {
  return React.createElement(
    "div",
    {
      style: {
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: BLUE,
      },
    },
    React.createElement(
      "span",
      {
        style: {
          color: "white",
          fontSize: Math.round(size * 0.62),
          fontWeight: 700,
        },
      },
      "C"
    )
  );
}

// Thin, low-opacity lines evoking blueprint grid paper. Built from plain positioned
// divs (rather than a CSS gradient) so the render doesn't depend on Satori's gradient
// parsing — these pixels get committed to git, so a guaranteed-safe render matters
// more than cleverness here.
function gridBackground() {
  const verticals = [12.5, 25, 37.5, 50, 62.5, 75, 87.5];
  const horizontals = [16.6, 33.3, 50, 66.6, 83.3];
  const lines = [
    ...verticals.map((pct, i) =>
      React.createElement("div", {
        key: `v${i}`,
        style: {
          position: "absolute",
          top: 0,
          bottom: 0,
          left: `${pct}%`,
          width: 1,
          background: "rgba(255,255,255,0.14)",
        },
      })
    ),
    ...horizontals.map((pct, i) =>
      React.createElement("div", {
        key: `h${i}`,
        style: {
          position: "absolute",
          left: 0,
          right: 0,
          top: `${pct}%`,
          height: 1,
          background: "rgba(255,255,255,0.14)",
        },
      })
    ),
  ];
  return React.createElement(
    "div",
    {
      style: {
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        display: "flex",
      },
    },
    ...lines
  );
}

function ogElement({ width, height, align }) {
  return React.createElement(
    "div",
    {
      style: {
        width,
        height,
        display: "flex",
        position: "relative",
        background: BLUE,
        fontFamily: "sans-serif",
      },
    },
    gridBackground(),
    React.createElement(
      "div",
      {
        style: {
          position: "relative",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: align,
          textAlign: align === "center" ? "center" : "left",
          width: "100%",
          height: "100%",
          padding: 96,
          color: "white",
        },
      },
      React.createElement(
        "div",
        { style: { fontSize: 88, fontWeight: 700, letterSpacing: -2 } },
        "Cognitect"
      ),
      React.createElement(
        "div",
        {
          style: {
            marginTop: 20,
            fontSize: 44,
            fontWeight: 500,
            color: "rgba(255,255,255,0.92)",
            maxWidth: align === "center" ? 900 : 760,
          },
        },
        "Floor Plans from Natural Language"
      )
    )
  );
}

const targets = [
  { name: "favicon-16x16.png", el: markElement(16), width: 16, height: 16 },
  { name: "favicon-32x32.png", el: markElement(32), width: 32, height: 32 },
  { name: "apple-touch-icon.png", el: markElement(180), width: 180, height: 180 },
  {
    name: "og-image.png",
    el: ogElement({ width: 1200, height: 630, align: "flex-start" }),
    width: 1200,
    height: 630,
  },
  {
    name: "og-image-square.png",
    el: ogElement({ width: 1200, height: 1200, align: "center" }),
    width: 1200,
    height: 1200,
  },
];

// favicon.ico needs 16/32/48 frames packed into one ICO container.
const icoSizes = [16, 32, 48];

async function main() {
  for (const t of targets) {
    const buf = await toPngBuffer(t.el, t.width, t.height);
    writeFileSync(join(publicDir, t.name), buf);
    console.log(`wrote ${t.name} (${buf.length} bytes)`);
  }

  const icoFrames = await Promise.all(
    icoSizes.map((size) => toPngBuffer(markElement(size), size, size))
  );
  const icoBuf = buildIco(icoFrames, icoSizes);
  writeFileSync(join(publicDir, "favicon.ico"), icoBuf);
  console.log(`wrote favicon.ico (${icoBuf.length} bytes)`);
}

// Hand-rolled ICO container: a 6-byte header, one 16-byte directory entry per frame,
// then the raw PNG bytes back to back. Modern ICO readers (Windows, all browsers)
// accept PNG-encoded frames directly — no BMP re-encoding needed. Written by hand
// instead of pulling in an ICO-packing library, since the only maintained option
// (`to-ico`) drags in an abandoned `jimp@0.2` -> `request`/`mkdirp` chain full of
// critical-severity advisories for a handful of lines of binary packing.
function buildIco(pngBuffers, sizes) {
  const numImages = pngBuffers.length;
  const headerSize = 6 + 16 * numImages;
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: 1 = icon
  header.writeUInt16LE(numImages, 4);

  let offset = headerSize;
  const dirEntries = [];
  for (let i = 0; i < numImages; i++) {
    const size = sizes[i];
    const png = pngBuffers[i];
    const entry = Buffer.alloc(16);
    entry.writeUInt8(size >= 256 ? 0 : size, 0); // width (0 means 256)
    entry.writeUInt8(size >= 256 ? 0 : size, 1); // height
    entry.writeUInt8(0, 2); // color palette
    entry.writeUInt8(0, 3); // reserved
    entry.writeUInt16LE(1, 4); // color planes
    entry.writeUInt16LE(32, 6); // bits per pixel
    entry.writeUInt32LE(png.length, 8); // size of image data
    entry.writeUInt32LE(offset, 12); // offset of image data
    dirEntries.push(entry);
    offset += png.length;
  }

  return Buffer.concat([header, ...dirEntries, ...pngBuffers]);
}

main();
