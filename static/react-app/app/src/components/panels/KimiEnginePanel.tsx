/**
 * KIMI Engine panel — frames the KIMI intraday quant terminal served by the
 * Flask bridge at /kimi/ (tools/kimi_bridge.py → kimi_engine/).
 *
 * The terminal runs its own scan loop against Bybit-first crypto quotes,
 * Binance crypto candles/fallback quotes, and MT5 broker data. It ships as a
 * self-contained SSE-driven page, so an iframe keeps the panel isolated from
 * the SPA lifecycle: no duplicated state, no re-render storms. This wrapper
 * deliberately does NOT poll /kimi/api/state — that snapshot is ~200KB per
 * tick and the embedded page already streams it.
 *
 * The terminal carries its own copy of the Aurora palette so the embedded
 * frame and the host shell read as one product.
 */
import { useCallback, useState } from 'react';
import { ExternalLink, RefreshCw } from 'lucide-react';

/** Matches --background in index.css, so the frame never flashes a lighter slab. */
const TERMINAL_BG = 'hsl(224 30% 4%)';

export default function KimiEnginePanel() {
  const [reloadKey, setReloadKey] = useState(0);
  const [loaded, setLoaded] = useState(false);

  const reload = useCallback(() => {
    setLoaded(false);
    setReloadKey((k) => k + 1);
  }, []);

  return (
    <div className="flex h-[calc(100vh-120px)] min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2 className="panel-title">KIMI Engine</h2>
          <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">
            Intraday quant engine · proprietary KIMI Score · paper execution by default ·
            broker execution arms only with env keys + explicit enable
          </p>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={reload}
            title="Reload the terminal frame"
            className="inline-flex items-center gap-1.5 rounded-md border border-border/70 px-2.5 py-1
                       text-[11px] font-medium text-muted-foreground transition-colors
                       hover:border-primary/50 hover:text-foreground"
          >
            <RefreshCw className="h-3 w-3" aria-hidden />
            Reload
          </button>
          <a
            href="/kimi/"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-md border border-border/70 px-2.5 py-1
                       text-[11px] font-medium text-muted-foreground transition-colors
                       hover:border-primary/50 hover:text-foreground"
          >
            Full screen
            <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        </div>
      </header>

      <div
        className="surface-raised relative min-h-0 flex-1 overflow-hidden"
        style={{ background: TERMINAL_BG }}
      >
        {!loaded && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <span className="flex items-center gap-2 text-[11px] tracking-wide text-muted-foreground">
              <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden />
              Connecting to the KIMI terminal…
            </span>
          </div>
        )}
        <iframe
          key={reloadKey}
          title="KIMI Engine terminal"
          src="/kimi/"
          onLoad={() => setLoaded(true)}
          className="h-full w-full border-0"
          style={{ background: TERMINAL_BG, opacity: loaded ? 1 : 0, transition: 'opacity .2s' }}
        />
      </div>
    </div>
  );
}
