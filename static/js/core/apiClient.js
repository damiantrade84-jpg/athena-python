// Core API client with compatibility globals.
(function initApiClient() {
  async function requestJson(url, options) {
    const resp = await fetch(url, options || {});
    const data = await (window.safeJson ? window.safeJson(resp) : resp.json());
    if (!resp.ok || (data && data.error)) {
      const msg = (data && (data.error || data.reason)) || `HTTP ${resp.status}`;
      throw new Error(msg);
    }
    return data;
  }

  window.apiClient = {
    getJson(url) {
      return requestJson(url, { method: "GET" });
    },
    postJson(url, payload) {
      return requestJson(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
    },
  };
})();

