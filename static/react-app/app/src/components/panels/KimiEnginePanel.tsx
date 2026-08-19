/**
 * KIMI Engine panel — embeds the KIMI intraday quant terminal served by the
 * Flask bridge at /kimi/ (tools/kimi_bridge.py → kimi_engine/).
 *
 * The terminal runs its own scan loop against Bybit-first crypto quotes,
 * Binance crypto candles/fallback quotes, and MT5 broker data. It
 * ships as a self-contained page (SSE-driven), so an iframe keeps the panel
 * isolated from the SPA lifecycle: no duplicated state, no re-render storms.
 */
export default function KimiEnginePanel() {
  return (
    <div className="flex h-[calc(100vh-120px)] flex-col gap-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold tracking-wide">KIMI Engine</h2>
          <p className="text-[11px] text-muted-foreground">
            Intraday quant engine · proprietary KIMI Score · paper execution by default ·
            broker execution arms only with env keys + explicit enable
          </p>
        </div>
        <a
          href="/kimi/"
          target="_blank"
          rel="noreferrer"
          className="rounded-md border border-border/70 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
        >
          Open full screen ↗
        </a>
      </div>
      <div className="surface-inset min-h-0 flex-1 overflow-hidden rounded-lg">
        <iframe
          title="KIMI Engine terminal"
          src="/kimi/"
          className="h-full w-full border-0"
          style={{ background: '#0a0e14' }}
        />
      </div>
    </div>
  );
}
