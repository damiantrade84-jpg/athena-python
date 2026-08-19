
window.EventSource = class {
  constructor() {
    setTimeout(() => this.onmessage && this.onmessage({ data: JSON.stringify(window.SNAP.state) }), 400);
  }
};
const _fetch = window.fetch.bind(window);
window.fetch = (url) => {
  const u = String(url);
  if (u.startsWith('/api/state')) return Promise.resolve(new Response(JSON.stringify(window.SNAP.state)));
  if (u.startsWith('/api/chart')) return Promise.resolve(new Response(JSON.stringify(window.SNAP.chart)));
  if (u.startsWith('/api/')) return Promise.resolve(new Response(JSON.stringify({ ok: true })));
  return _fetch(url);
};
