// Regenerate the PWA icons from public/logo.svg.
//   cd faster-notes && node scripts/gen-icons.mjs
// Requires `sharp` (dev-only). The logo carries its own dark background, so the
// square render works directly as a maskable icon.
import sharp from "sharp";
import { readFileSync } from "node:fs";
import path from "node:path";

const root = process.cwd(); // run from faster-notes/
// width/height="100%" has no intrinsic size; pin it so the rasterizer renders crisp.
const svgText = readFileSync(path.join(root, "public", "logo.svg"), "utf8")
  .replace('width="100%" height="100%"', 'width="2000" height="2000"');
const svg = Buffer.from(svgText);

for (const size of [192, 512]) {
  const out = path.join(root, "public", "icons", `icon-${size}.png`);
  await sharp(svg).resize(size, size).png().toFile(out);
  console.log("wrote", out);
}
