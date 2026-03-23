// Core state compatibility layer.
(function initCoreState() {
  if (!window.AppState) {
    window.AppState = {
      latestPrices: {},
      allSignals: [],
      ecResults: null,
    };
  }
})();

