/**
 * Resolve the browser chart identity from a cross-panel chart intent.
 *
 * Commodity catalog symbols may retain a Yahoo continuous-futures proxy while
 * the chart and execution series are a spot CFD. The intent's display value is
 * the authoritative chart identity in that one case.
 */
export function resolveChartIntentSymbol(intent: { symbol?: unknown; display?: unknown }): string {
  const symbol = typeof intent.symbol === 'string' ? intent.symbol.trim().toUpperCase() : '';
  const display = typeof intent.display === 'string' ? intent.display.trim().toUpperCase() : '';
  if (!symbol) return display;
  return symbol.endsWith('=F') && display ? display : symbol;
}
