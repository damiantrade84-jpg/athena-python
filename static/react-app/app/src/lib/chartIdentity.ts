/**
 * Resolve the browser chart identity from a cross-panel chart intent.
 *
 * Catalog `symbol` fields may still hold a Yahoo feed id (GC=F, ^AXJO).
 * That id is a label only: non-crypto candles, structure TF, and execution
 * use the Athena display / MT5 series (XAU/USD, ASX 200). Prefer display
 * whenever it is a real chart symbol, and map known Yahoo ids when display
 * is missing or is itself a feed id.
 */

const YAHOO_FEED_DISPLAY: Record<string, string> = {
  'GC=F': 'XAU/USD',
  'SI=F': 'XAG/USD',
  'CL=F': 'WTI Oil',
  'BZ=F': 'Brent Oil',
  'PL=F': 'XPT/USD',
  'PA=F': 'XPD/USD',
  '^GSPC': 'S&P 500',
  '^DJI': 'Dow Jones',
  '^GDAXI': 'DAX 40',
  '^FTSE': 'UK100',
  '^AXJO': 'ASX 200',
  '^N225': 'Nikkei 225',
  '^HSI': 'Hang Seng',
};

/** Compact-key aliases so Yahoo / display / broker identities compare as one instrument. */
const COMPACT_CHART_ALIASES: Record<string, string> = {
  AXJO: 'ASX200',
  AUS200: 'ASX200',
  ASX200: 'ASX200',
  GSPC: 'SP500',
  US500: 'SP500',
  SP500: 'SP500',
  DJI: 'DOWJONES',
  US30: 'DOWJONES',
  DOWJONES: 'DOWJONES',
  GDAXI: 'DAX40',
  DAX: 'DAX40',
  DAX40: 'DAX40',
  GER40: 'DAX40',
  FTSE: 'UK100',
  FTSE100: 'UK100',
  UK100: 'UK100',
  N225: 'NIKKEI225',
  JP225: 'NIKKEI225',
  JPN225: 'NIKKEI225',
  NIKKEI225: 'NIKKEI225',
  HSI: 'HANGSENG',
  HK50: 'HANGSENG',
  HANGSENG: 'HANGSENG',
};

function isYahooFeedProxy(symbol: string): boolean {
  return symbol.startsWith('^') || symbol.endsWith('=F') || symbol.endsWith('=X');
}

export function compactChartSymbolKey(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const upper = value.trim().toUpperCase();
  if (!upper) return null;
  const withoutProvider = upper.includes(':') ? upper.split(':').pop() || upper : upper;
  const withoutYahooFxSuffix = withoutProvider.replace(/=X$/, '').replace(/\.US$/, '');
  const withoutBrokerSuffix = withoutYahooFxSuffix.endsWith('.S')
    ? withoutYahooFxSuffix.slice(0, -2)
    : withoutYahooFxSuffix;
  const key = withoutBrokerSuffix.replace(/[^A-Z0-9]/g, '');
  if (!key) return null;
  return COMPACT_CHART_ALIASES[key] || key;
}

export function resolveChartIntentSymbol(intent: { symbol?: unknown; display?: unknown }): string {
  const symbolRaw = typeof intent.symbol === 'string' ? intent.symbol.trim() : '';
  const displayRaw = typeof intent.display === 'string' ? intent.display.trim() : '';
  const symbol = symbolRaw.toUpperCase();
  const display = displayRaw.toUpperCase();
  if (display && !isYahooFeedProxy(display)) return display;
  if (symbol && YAHOO_FEED_DISPLAY[symbol]) return YAHOO_FEED_DISPLAY[symbol];
  if (!symbol) return display;
  return symbol;
}
