import { makeCircleLayer } from './_circle.js';

// Amber-yellow: distinct from quake orange at a glance, still reads as "caution".
export default makeCircleLayer({
  type: 'weather',
  paint: { color: '#E8C547', minRadius: 5, maxRadius: 20 },
});
