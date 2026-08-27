// Shared helper: a circle-marker layer driven by a GeoJSON source keyed on MEvent.id.
// Each event-type module calls makeCircleLayer() so app.js only sees the layer interface.
//
// Two derived properties are computed per feature and drive paint expressions, so MapLibre
// interpolates them without a per-frame JS pass:
//
//   agefrac  (now - ts) / ttl, clamped 0..1 — time-decay, phase-1 §6. Opacity falls with
//            age; radius does NOT, because size encodes severity and must not encode two
//            things at once.
//   lowconf  1 when payload.geocode.confidence === 'low' — an inferred location (a country
//            centroid) renders hollow rather than solid, per CLAUDE.md rule 5. Always 0 for
//            sources that do not geocode, so every layer shares one code path.
//
// Both are refreshed by refresh(), which app.js calls on the same 30 s tick as the local
// expiry sweep.

export function makeCircleLayer({ type, paint }) {
  const sourceId = `ev-${type}`;
  const layerId = `ev-${type}-circles`;
  const store = new Map(); // id -> MEvent

  function ageFraction(e, now) {
    const ttlMs = (e.ttl || 1) * 1000;
    return Math.min(1, Math.max(0, (now - Date.parse(e.ts)) / ttlMs));
  }

  function isLowConfidence(e) {
    return e.payload?.geocode?.confidence === 'low' ? 1 : 0;
  }

  function toFeature(e, now) {
    return {
      type: 'Feature',
      id: e.id,
      geometry: { type: 'Point', coordinates: [e.lon, e.lat] },
      properties: {
        ...e,
        agefrac: ageFraction(e, now),
        lowconf: isLowConfidence(e),
        payload: JSON.stringify(e.payload || {}),
      },
    };
  }

  function flush(map) {
    const src = map.getSource(sourceId);
    if (!src) return;
    const now = Date.now();
    src.setData({ type: 'FeatureCollection', features: [...store.values()].map((e) => toFeature(e, now)) });
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
          // Fade with age, then all but hollow out an inferred location.
          'circle-opacity': [
            '*',
            ['interpolate', ['linear'], ['get', 'agefrac'], 0, 0.85, 1, 0.25],
            ['case', ['==', ['get', 'lowconf'], 1], 0.12, 1],
          ],
          'circle-stroke-color': paint.color,
          'circle-stroke-width': ['case', ['==', ['get', 'lowconf'], 1], 2, 1.5],
          // Hollow markers need a brighter ring to stay visible once the fill is gone.
          'circle-stroke-opacity': [
            '*',
            ['interpolate', ['linear'], ['get', 'agefrac'], 0, 0.35, 1, 0.08],
            ['case', ['==', ['get', 'lowconf'], 1], 2.4, 1],
          ],
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
    // Recompute agefrac so decay advances without new events arriving.
    refresh() {
      if (this.map && store.size) flush(this.map);
    },
    setVisible(on) {
      if (this.map) this.map.setLayoutProperty(layerId, 'visibility', on ? 'visible' : 'none');
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
