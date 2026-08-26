// Shared helper: a circle-marker layer driven by a GeoJSON source keyed on MEvent.id.
// Each event-type module calls makeCircleLayer() so app.js only sees the layer interface.

export function makeCircleLayer({ type, paint }) {
  const sourceId = `ev-${type}`;
  const layerId = `ev-${type}-circles`;
  const store = new Map(); // id -> MEvent

  function toFeature(e) {
    return {
      type: 'Feature',
      id: e.id,
      geometry: { type: 'Point', coordinates: [e.lon, e.lat] },
      properties: { ...e, payload: JSON.stringify(e.payload || {}) },
    };
  }

  function flush(map) {
    const src = map.getSource(sourceId);
    if (src) src.setData({ type: 'FeatureCollection', features: [...store.values()].map(toFeature) });
  }

  return {
    type,
    paint,
    sourceId,
    layerId,
    init(map) {
      this.map = map;
      map.addSource(sourceId, { type: 'geojson', data: { type: 'FeatureCollection', features: [] }, promoteId: 'id' });
      map.addLayer({
        id: layerId,
        type: 'circle',
        source: sourceId,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['get', 'severity'], 0, paint.minRadius, 5, paint.maxRadius],
          'circle-color': paint.color,
          'circle-opacity': 0.85,
          'circle-stroke-color': paint.color,
          'circle-stroke-width': 1.5,
          'circle-stroke-opacity': 0.35,
          'circle-blur': 0.15,
        },
      });
    },
    upsert(events) {
      for (const e of events) store.set(e.id, e);
      flush(this.map);
    },
    expire(ids) {
      let changed = false;
      for (const id of ids) changed = store.delete(id) || changed;
      if (changed) flush(this.map);
    },
    count() { return store.size; },
    ids() { return [...store.keys()]; },
    // Client-side safety net: the spec says the client drops after ts + ttl regardless of
    // whether it saw the server's expire op (it may have been disconnected at the time).
    expiredIds(now = Date.now()) {
      const out = [];
      for (const e of store.values()) if (Date.parse(e.ts) + e.ttl * 1000 <= now) out.push(e.id);
      return out;
    },
  };
}
