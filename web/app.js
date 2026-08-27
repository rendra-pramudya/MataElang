// MataElang client. Vanilla ESM, no build step.
// Responsibilities: boot MapLibre on local PMTiles, hold the WS connection, route
// {snapshot,upsert,expire,status} ops to layer modules by event.type.

import maplibregl from '/vendor/maplibre-gl.mjs';
import { Protocol } from '/vendor/pmtiles.mjs';
import quake from '/layers/quake.js';
import conflict from '/layers/conflict.js';
import weather from '/layers/weather.js';
import news from '/layers/news.js';
import heat from '/layers/heat.js';

// Marker layers, routed to by event.type. `heat` is not here — it is a pseudo-layer fed by
// /api/heat rather than by the WebSocket.
const LAYERS = [quake, conflict, weather, news];
const registry = Object.fromEntries(LAYERS.map((l) => [l.type, l]));

const LABELS = { quake: 'Quakes', conflict: 'Conflict', weather: 'Weather', news: 'News' };

const connDot = document.getElementById('conn');
const connLabel = document.getElementById('conn-label');
const statusEl = document.getElementById('status');
const togglesEl = document.getElementById('toggles');

// ---------------------------------------------------------------------------
// Map
// ---------------------------------------------------------------------------

maplibregl.addProtocol('pmtiles', new Protocol().tile);

const map = new maplibregl.Map({
  container: 'map',
  style: '/map/style.json',
  center: [106.85, -6.2], // Jakarta — acceptance test §7.1
  zoom: 3.5,
  minZoom: 1,
  maxZoom: 16,
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

const mapReady = new Promise((resolve) => map.on('load', resolve));

mapReady.then(async () => {
  await addBoundaryOverrides();
  // Heat first, so every marker layer is added above it and stays clickable through it.
  heat.init(map);
  for (const layer of LAYERS) {
    layer.init(map);
    map.on('click', layer.layerId, (e) => showPopup(e));
    map.on('mouseenter', layer.layerId, () => (map.getCanvas().style.cursor = 'pointer'));
    map.on('mouseleave', layer.layerId, () => (map.getCanvas().style.cursor = ''));
  }
  renderToggles();
  connect();
});

// ---------------------------------------------------------------------------
// Layer toggles — five layers is past the point where "all on" is usable.
// ---------------------------------------------------------------------------

function renderToggles() {
  const entries = [
    ...LAYERS.map((l) => ({ key: l.type, label: LABELS[l.type] || l.type, layer: l, on: true })),
    { key: 'heat', label: 'Conflict density', layer: heat, on: false, hint: 'reported density, not ground truth' },
  ];
  togglesEl.replaceChildren(
    ...entries.map(({ key, label, layer, on, hint }) => {
      const el = document.createElement('label');
      el.className = 'toggle';
      if (hint) el.title = hint;
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = on;
      box.addEventListener('change', () => layer.setVisible(box.checked));
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = layer.paint?.color || 'linear-gradient(90deg,#00B4CC,#F58220)';
      el.append(box, swatch, document.createTextNode(label));
      return el;
    })
  );
}

// Editorial border overrides in data/boundaries/*.geojson draw above OSM boundaries.
async function addBoundaryOverrides() {
  let files = [];
  try {
    const r = await fetch('/api/boundaries');
    files = (await r.json()).files || [];
  } catch (err) {
    console.warn('boundary list unavailable', err);
    return;
  }
  for (const f of files) {
    const id = `override-${f.replace(/\W/g, '_')}`;
    map.addSource(id, { type: 'geojson', data: `/boundaries/${f}` });
    map.addLayer({
      id,
      type: 'line',
      source: id,
      paint: { 'line-color': '#5a626c', 'line-width': 1.2 },
    });
  }
}

// ---------------------------------------------------------------------------
// Popup
// ---------------------------------------------------------------------------

let popup = null;

function showPopup(e) {
  const f = e.features?.[0];
  if (!f) return;
  const p = f.properties;
  const ago = timeAgo(new Date(p.ts));
  const link = p.url && p.url !== 'null' ? `<a href="${escapeHtml(p.url)}" target="_blank" rel="noopener">source ↗</a>` : '';

  // An inferred location is never presented as a fact — CLAUDE.md rule 5.
  let payload = {};
  try { payload = JSON.parse(p.payload || '{}'); } catch { /* opaque to shared code */ }
  const geo = payload.geocode;
  const inferred = geo
    ? `<div class="inferred">location inferred from headline — matched “${escapeHtml(geo.match)}”${geo.confidence === 'low' ? ' (country centroid, low confidence)' : ''}</div>`
    : '';

  const html = `
    <div class="popup">
      <div class="title">${escapeHtml(p.title)}</div>
      <div class="meta">${escapeHtml(p.source)} · <span class="sev">S${p.severity}</span> · ${ago}${link ? ' · ' + link : ''}</div>
      ${inferred}
    </div>`;
  if (popup) popup.remove();
  popup = new maplibregl.Popup({ closeButton: true, maxWidth: '320px' })
    .setLngLat(f.geometry.coordinates)
    .setHTML(html)
    .addTo(map);
}

function timeAgo(d) {
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

let ws = null;
let backoff = 1000;
let lastTs = null; // newest event ts seen → sent as `since` on reconnect
let fixtureMode = false;

function setConn(state, label) {
  connDot.dataset.state = state;
  connDot.title = label;
  connLabel.textContent = label;
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.addEventListener('open', () => {
    backoff = 1000;
    ws.send(JSON.stringify({ op: 'hello', since: lastTs }));
    setConn(fixtureMode ? 'fixture' : 'live', fixtureMode ? 'fixture' : 'live');
  });

  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handle(msg);
  });

  ws.addEventListener('close', () => {
    setConn('reconnecting', `reconnecting ${Math.round(backoff / 1000)}s`);
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 30000);
  });

  ws.addEventListener('error', () => ws.close());
}

function handle(msg) {
  switch (msg.op) {
    case 'snapshot':
      // A fresh snapshot with no `since` is authoritative: clear everything first.
      if (!lastTs) for (const l of LAYERS) l.expire(l.ids());
      routeEvents(msg.events);
      break;
    case 'upsert':
      routeEvents(msg.events);
      break;
    case 'expire':
      for (const l of LAYERS) l.expire(msg.ids);
      break;
    case 'status':
      fixtureMode = !!msg.fixture_mode;
      if (ws?.readyState === WebSocket.OPEN) setConn(fixtureMode ? 'fixture' : 'live', fixtureMode ? 'fixture' : 'live');
      renderStatus(msg.sources);
      break;
    case 'ping':
      ws.send(JSON.stringify({ op: 'pong' }));
      break;
    default:
      console.warn('unknown op', msg.op);
  }
}

function routeEvents(events) {
  const byType = {};
  for (const e of events) {
    if (!registry[e.type]) { console.warn('unknown event type', e.type); continue; }
    (byType[e.type] ||= []).push(e);
    if (!lastTs || e.ts > lastTs) lastTs = e.ts;
  }
  for (const [t, evs] of Object.entries(byType)) registry[t].upsert(evs);
}

// Local expiry sweep, independent of the server's. The same tick advances time-decay
// (phase-1 §6) — without it, opacity would only move when new events happened to arrive.
setInterval(() => {
  for (const l of LAYERS) {
    const dead = l.expiredIds();
    if (dead.length) l.expire(dead);
    else l.refresh();
  }
}, 30000);

// ---------------------------------------------------------------------------
// Status strip
// ---------------------------------------------------------------------------

function renderStatus(sources) {
  statusEl.replaceChildren(
    ...Object.entries(sources).map(([name, s]) => {
      const el = document.createElement('div');
      el.className = 'src';
      el.dataset.ok = s.last_error ? 'false' : (s.last_ok ? 'true' : 'none');
      const when = s.last_ok ? timeAgo(new Date(s.last_ok)) : 'no data yet';
      el.innerHTML = `<b>${escapeHtml(name.toUpperCase())}</b><span class="n">${s.count}</span><span>${escapeHtml(when)}</span>`
        + (s.last_error ? `<span class="err" title="${escapeHtml(s.last_error)}">${escapeHtml(s.last_error)}</span>` : '');
      return el;
    })
  );
}

// Expose for console poking during acceptance tests.
window.mataelang = {
  map,
  layers: registry,
  heat,
  counts: () => Object.fromEntries([...LAYERS, heat].map((l) => [l.type, l.count()])),
};
