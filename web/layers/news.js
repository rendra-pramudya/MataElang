import { makeCircleLayer } from './_circle.js';

// Pale slate: news is context, not alarm. Low-confidence (country-centroid) geocodes render
// hollow — see _circle.js and docs/phase-1-layers.md §4.2.
export default makeCircleLayer({
  type: 'news',
  paint: { color: '#8FA3B0', minRadius: 3, maxRadius: 13 },
});
