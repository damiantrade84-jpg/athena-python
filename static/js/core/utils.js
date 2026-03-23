// Core utility helpers for modular migration.
(function initCoreUtils() {
  window.safeJson = async function safeJson(resp) {
    if (!resp) return {};
    try {
      return await resp.json();
    } catch (e) {
      return {};
    }
  };
})();

