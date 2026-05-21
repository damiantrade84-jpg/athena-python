// One-off smoke for downscaleToCap aspect-ratio math.
// Mirrors the formula inside aiChartReview.ts. Pure math, no DOM.
function expectedDims(w, h, maxW = 1280, maxH = 720) {
  if (w <= maxW && h <= maxH) return { w, h };
  const scale = Math.min(maxW / w, maxH / h);
  return {
    w: Math.max(1, Math.round(w * scale)),
    h: Math.max(1, Math.round(h * scale)),
  };
}

const cases = [
  { in: [800, 600], out: { w: 800, h: 600 } },
  { in: [1280, 720], out: { w: 1280, h: 720 } },
  { in: [2560, 1440], out: { w: 1280, h: 720 } },
  { in: [3200, 1800], out: { w: 1280, h: 720 } },
  { in: [4000, 1500], out: { w: 1280, h: 480 } },
];

let failed = 0;
for (const c of cases) {
  const got = expectedDims(...c.in);
  const ok = got.w === c.out.w && got.h === c.out.h;
  console.log(ok ? 'PASS' : 'FAIL', c.in, '->', got, 'expected', c.out);
  if (!ok) failed += 1;
}
process.exit(failed === 0 ? 0 : 1);
