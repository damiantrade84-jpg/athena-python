import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bell, ChevronRight, ListChecks, X } from 'lucide-react';
import { useStore } from '@/hooks/useStore';
import { apiClient } from '@/lib/apiClient';
import {
  isScalpSuggestedWatch,
  readySuggestedWatches,
  suggestedWatchId,
  suggestedWatchLabel,
} from '@/lib/suggestedWatchDisplay';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import type {
  ScalpWorkbenchIntent,
  SuggestedTradesListResponse,
  SuggestedTradeWatch,
  TvChartIntent,
} from '@/types/athena';

const POLL_MS = 5_000;
const EVAL_MS = 8_000;
const TITLE_FLASH_MS = 1_200;

function watchEntryTf(watch: SuggestedTradeWatch): string | undefined {
  return watch.entry_tf || watch.entryTf || watch.suggested_trade_plan?.entryTf;
}

function watchContextTf(watch: SuggestedTradeWatch): string | undefined {
  return watch.context_tf || watch.contextTf || watch.suggested_trade_plan?.contextTf;
}

function needsFrontendEval(payload: SuggestedTradesListResponse | null): boolean {
  const runner = payload?.runner;
  return !runner?.active
    || runner?.mode === 'frontend_poll'
    || runner?.mode === 'manual_only';
}

/**
 * App-wide suggested-trade attention. The dedicated panel lives far down the
 * sidebar and is unmounted when another view is open, so ready watches used
 * to fire only a toast nobody saw. This watcher stays mounted in MainLayout.
 */
export default function SuggestedTradeAlertWatcher() {
  const {
    activePanel,
    setActivePanel,
    setTvChartIntent,
    setScalpWorkbenchIntent,
    showToast,
  } = useStore();
  const [payload, setPayload] = useState<SuggestedTradesListResponse | null>(null);
  const [queue, setQueue] = useState<SuggestedTradeWatch[]>([]);
  const [bannerHiddenKey, setBannerHiddenKey] = useState<string | null>(null);
  const seenReadyIdsRef = useRef<Set<string> | null>(null);
  const titleBaseRef = useRef<string | null>(null);

  const applyPayload = useCallback((res: SuggestedTradesListResponse) => {
    const ready = readySuggestedWatches(res?.watches);
    const readyIds = new Set(ready.map(suggestedWatchId));
    const seen = seenReadyIdsRef.current;
    seenReadyIdsRef.current = readyIds;
    if (seen !== null) {
      const fresh = ready.filter((watch) => !seen.has(suggestedWatchId(watch)));
      if (fresh.length > 0) {
        showToast(
          `Suggested trade ready: ${fresh.map(suggestedWatchLabel).join(', ')} — review now`,
          'success',
        );
        setQueue((current) => {
          const known = new Set(current.map(suggestedWatchId));
          return [...current, ...fresh.filter((watch) => !known.has(suggestedWatchId(watch)))];
        });
      }
    }
    setPayload(res);
  }, [showToast]);

  const refresh = useCallback(async () => {
    try {
      const res = await apiClient.getJson('/api/suggested-trades') as SuggestedTradesListResponse;
      applyPayload(res);
    } catch {
      // Advisory surface — never block the rest of the shell on a poll miss.
    }
  }, [applyPayload]);

  const evaluateNow = useCallback(async () => {
    try {
      const res = await apiClient.postJson('/api/suggested-trades/evaluate-now', {}) as SuggestedTradesListResponse;
      applyPayload(res);
    } catch {
      // Same fail-open as the panel: a missed tick is retried on the next interval.
    }
  }, [applyPayload]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const shouldEval = needsFrontendEval(payload) && activePanel !== 'suggestedTrades';
  useEffect(() => {
    if (!shouldEval) return undefined;
    void evaluateNow();
    const timer = window.setInterval(() => {
      void evaluateNow();
    }, EVAL_MS);
    return () => window.clearInterval(timer);
  }, [shouldEval, evaluateNow]);

  const readyWatches = useMemo(
    () => readySuggestedWatches(payload?.watches),
    [payload],
  );
  const readyKey = readyWatches.map(suggestedWatchId).sort().join('|');
  const showBanner = readyWatches.length > 0 && bannerHiddenKey !== readyKey;
  const active = queue[0];

  useEffect(() => {
    if (typeof document === 'undefined') return undefined;
    if (titleBaseRef.current === null) titleBaseRef.current = document.title;
    const base = titleBaseRef.current || 'Sentinel Pro';
    if (readyWatches.length === 0) {
      document.title = base;
      return undefined;
    }
    const labels = readyWatches.map(suggestedWatchLabel).slice(0, 2).join(', ');
    const alertTitle = `● READY · ${labels} — Sentinel`;
    document.title = alertTitle;
    const timer = window.setInterval(() => {
      document.title = document.title === alertTitle ? base : alertTitle;
    }, TITLE_FLASH_MS);
    return () => {
      window.clearInterval(timer);
      document.title = base;
    };
  }, [readyWatches]);

  const openWatch = useCallback((watch: SuggestedTradeWatch) => {
    const symbol = String(watch.symbol || '').toUpperCase();
    if (!symbol) {
      showToast('Watch has no symbol', 'error');
      return;
    }
    if (isScalpSuggestedWatch(watch)) {
      const intent: ScalpWorkbenchIntent = {
        id: `scalp-watch-${watch.watch_id || symbol}-${Date.now()}`,
        source: 'manual',
        symbol,
        signal: watch.signal,
        preferredTf: 'M5',
        autoReview: false,
        createdAt: new Date().toISOString(),
      };
      setScalpWorkbenchIntent(intent);
      setActivePanel('scalpWorkbench');
      return;
    }
    const intent: TvChartIntent = {
      id: `tv-watch-${watch.watch_id || symbol}-${Date.now()}`,
      source: 'manual',
      symbol,
      display: watch.display || symbol,
      signal: watch.signal,
      watchId: watch.watch_id,
      preferredTf: watchEntryTf(watch) || watchContextTf(watch) || 'H4',
      autoReview: false,
      createdAt: new Date().toISOString(),
    };
    setTvChartIntent(intent);
    setActivePanel('tvChart');
  }, [setActivePanel, setScalpWorkbenchIntent, setTvChartIntent, showToast]);

  const dismissQueueHead = useCallback(() => {
    setQueue((current) => current.slice(1));
  }, []);

  const openList = useCallback(() => {
    setActivePanel('suggestedTrades');
  }, [setActivePanel]);

  const hideBanner = useCallback(() => {
    setBannerHiddenKey(readyKey || null);
  }, [readyKey]);

  return (
    <>
      {showBanner && (
        <div
          className="relative z-40 flex shrink-0 items-center gap-3 border-b border-long/40 bg-long/15 px-5 py-2 text-sm text-foreground"
          data-suggested-trade-alert="ready"
          role="status"
          aria-live="polite"
        >
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-long/20 text-long">
            <Bell className="h-3.5 w-3.5 animate-pulse" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="font-semibold tracking-tight">
              {readyWatches.length === 1
                ? `Suggested trade ready — ${suggestedWatchLabel(readyWatches[0])}`
                : `${readyWatches.length} suggested trades ready for review`}
            </div>
            <div className="truncate text-[11px] text-muted-foreground">
              {readyWatches.slice(0, 3).map((watch) => (
                `${suggestedWatchLabel(watch)} ${String(watch.direction || '').toUpperCase()}`
              )).join(' · ')}
              {readyWatches.length > 3 ? ` · +${readyWatches.length - 3} more` : ''}
              {' — advisory watch only, not an execution permission'}
            </div>
          </div>
          <Button
            size="sm"
            className="h-7 shrink-0 bg-long text-primary-foreground hover:bg-long/90"
            onClick={() => {
              if (readyWatches[0]) openWatch(readyWatches[0]);
            }}
          >
            Review now
            <ChevronRight className="ml-1 h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 shrink-0 border-long/40"
            onClick={openList}
          >
            <ListChecks className="mr-1 h-3.5 w-3.5" />
            All watches
          </Button>
          <button
            type="button"
            className="rounded-md p-1 text-muted-foreground hover:bg-background/40 hover:text-foreground"
            aria-label="Dismiss suggested trade banner"
            onClick={hideBanner}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <AlertDialog open={!!active} onOpenChange={(open) => { if (!open) dismissQueueHead(); }}>
        <AlertDialogContent data-suggested-trade-alert="dialog">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Bell className="h-4 w-4 text-long" />
              Suggested trade level reached
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm">
                <div className="font-semibold text-foreground">
                  {active ? `${suggestedWatchLabel(active)} · ${String(active.direction || '').toUpperCase() || '—'}` : ''}
                </div>
                <div className="text-xs text-muted-foreground">
                  The watched level or zone has triggered. This is advisory only —
                  open the chart and run the normal review / execution gates.
                </div>
                {queue.length > 1 && (
                  <div className="text-[11px] text-muted-foreground">
                    {queue.length - 1} more ready watch{queue.length > 2 ? 'es' : ''} queued.
                  </div>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button variant="outline" size="sm" onClick={openList}>
              Open suggested trades
            </Button>
            <AlertDialogAction
              onClick={() => {
                if (active) openWatch(active);
                dismissQueueHead();
              }}
            >
              Review on chart
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
