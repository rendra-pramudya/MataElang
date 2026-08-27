// Conflict-density heat map (docs/phase-1-layers.md §5.3).
//
// A pseudo-layer: it takes no WebSocket events. It polls /api/heat, which bins SQLite history
// into H3 cells, and renders one weighted point per cell. It therefore shows accumulated
// *reporting* density over the retention window, not live events — the legend must say
// "reported conflict density", per CLAUDE.md rule 5.
//
// Off by default. A heat map that is always on is background texture, not information.
// It sits below every marker layer so markers stay clickable through it.

const SOURCE_ID = 'heat-conflict';
const LAYER_ID = 'heat-conflict-heat';
const REFRESH_MS = 60000;

export default {
  type: 'heat',
  sourceId: SOURCE_ID,
  layerId: LAYER_ID,
  visible: false,
  cells: [],
  _timer: null,

  init(map, { beforeId } = {}) {
    this.map = map;
    map.addSource(SOURCE_ID, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer(
      {
        id: LAYER_ID,
        type: 'heatmap',
        source: SOURCE_ID,
        layout: { visibility: 'none' },
        paint: {
          // Weight by sum(1 + severity), normalised client-side against the heaviest cell,
          // so the ramp does not wash out when history accumulates.
          'heatmap-weight': ['interpolate', ['linear'], ['get', 'norm'], 0, 0.1, 1, 1],
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 0.6, 9, 2.2],
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 14, 4, 26, 9, 46],
          'heatmap-opacity': 0.55,
          // Teal → alert orange. Transparent at the bottom so empty map stays dark.
          'heatmap-color': [
            'interpolate', ['linear'], ['heatmap-density'],
            0, 'rgba(0,0,0,0)',
            0.2, 'rgba(0,80,102,0.5)',
            0.45, 'rgba(0,180,204,0.65)',
            0.7, 'rgba(226,164,60,0.75)',
            1, 'rgba(245,130,32,0.9)',
          ],
        },
      },
      beforeId,
    );
  },

  async load() {
    try {
      const r = await fetch('/api/heat?type=conflict');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      this.cells = body.cells || [];
      this.render();
    } catch (err) {
      console.warn('heat map unavailable', err);
    }
  },

  render() {
    const src = this.map?.getSource(SOURCE_ID);
    if (!src) return;
    const max = this.cells.reduce((m, c) => Math.max(m, c.weight), 0) || 1;
    src.setData({
      type: 'FeatureCollection',
      features: this.cells.map((c) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [c.lon, c.lat] },
        properties: { norm: c.weight / max, count: c.count, weight: c.weight },
      })),
    });
  },

  setVisible(on) {
    this.visible = on;
    this.map?.setLayoutProperty(LAYER_ID, 'visibility', on ? 'visible' : 'none');
    clearInterval(this._timer);
    this._timer = null;
    if (!on) return;
    this.load();
    this._timer = setInterval(() => this.load(), REFRESH_MS);
  },

  count() { return this.cells.length; },
};
