import { useState, useCallback } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { ErrorBanner, RefreshButton } from '@/components/shared';
import { Search, Play, Eye, ArrowUpRight, Zap, Filter } from 'lucide-react';
import { format } from 'date-fns';
import type { Signal } from '@/types';

interface ScanResult {
  signals: Signal[];
  scan_time: number;
  pairs_scanned: number;
}

interface ExecuteResult {
  success: boolean;
  ticket?: string;
  volume?: number;
  entryPrice?: number;
  error?: string;
  approval?: { approved: boolean; reason: string };
}

export default function SignalsPanel() {
  const { signals: storeSignals, executeSignal: storeExecute, showToast, isTestMode } = useStore();
  const [filter, setFilter] = useState('');
  const [directionFilter, setDirectionFilter] = useState<string>('all');
  const [assetClass, setAssetClass] = useState<string>('all');
  const [sortBy, setSortBy] = useState<string>('confidence');
  const [selectedSignal, setSelectedSignal] = useState<Signal | null>(null);
  const [confirmSignal, setConfirmSignal] = useState<Signal | null>(null);

  const { data: scanResult, loading: scanLoading, error: scanError, refresh: refreshScan } = useApiPoll<ScanResult>('/api/last-scan', 30000);
  const { data: autoTrade } = useApiPoll<{ enabled: boolean }>('/api/auto-trade', 30000);
  const { post: postScan, loading: scanning } = useApiPost<ScanResult>();
  const { post: postExecute, loading: executing } = useApiPost<ExecuteResult>();

  const allSignals = scanResult?.signals || storeSignals;

  const filtered = allSignals.filter(s => {
    if (filter && !s.pair.toLowerCase().includes(filter.toLowerCase())) return false;
    if (directionFilter !== 'all' && s.direction !== directionFilter) return false;
    return true;
  }).sort((a, b) => {
    if (sortBy === 'confidence') return b.confidence - a.confidence;
    if (sortBy === 'rr') return b.rRatio - a.rRatio;
    if (sortBy === 'time') return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
    return 0;
  });

  const handleScan = useCallback(async () => {
    const asset = assetClass === 'all' ? 'forex' : assetClass;
    const result = await postScan('/api/scan', { asset_class: asset, force: false });
    if (result) {
      showToast(`Scanned ${result.pairs_scanned} pairs in ${result.scan_time.toFixed(1)}s`, 'success');
    } else {
      showToast('Scan failed', 'error');
    }
  }, [assetClass, postScan, showToast]);

  const handleExecute = useCallback(async () => {
    if (!confirmSignal) return;
    const payload = {
      signal: {
        symbol: confirmSignal.pair,
        direction: confirmSignal.direction,
        entry: confirmSignal.entry,
        sl: confirmSignal.sl,
        tp: confirmSignal.tp,
        style: confirmSignal.style || 'swing',
        source: confirmSignal.engine,
        model: confirmSignal.model || confirmSignal.engine,
        tf: confirmSignal.timeframe,
      },
      force: false,
    };
    const result = await postExecute('/api/execute', payload);
    if (result) {
      if (result.approval && !result.approval.approved) {
        showToast(`Rejected: ${result.approval.reason}`, 'error');
      } else if (result.success) {
        showToast(`Executed ${confirmSignal.pair} — Ticket ${result.ticket}`, 'success');
        storeExecute(confirmSignal.id);
      } else {
        showToast(`Error: ${result.error || 'Unknown'}`, 'error');
      }
    } else {
      showToast('Execution failed', 'error');
    }
    setConfirmSignal(null);
  }, [confirmSignal, postExecute, showToast, storeExecute]);

  const isPaper = autoTrade?.enabled ?? false;
  const executeDisabled = isTestMode;
  const executeLabel = isTestMode ? 'TEST MODE' : isPaper ? 'PAPER MODE' : 'Execute';

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <Input
            placeholder="Filter by pair..."
            className="pl-8 h-8 text-xs"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          />
        </div>
        <Select value={assetClass} onValueChange={setAssetClass}>
          <SelectTrigger className="w-[130px] h-8 text-xs">
            <SelectValue placeholder="Asset Class" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="forex">Forex</SelectItem>
            <SelectItem value="crypto">Crypto</SelectItem>
            <SelectItem value="stocks">Stocks</SelectItem>
            <SelectItem value="indices">Indices</SelectItem>
            <SelectItem value="commodities">Commodities</SelectItem>
          </SelectContent>
        </Select>
        <Select value={directionFilter} onValueChange={setDirectionFilter}>
          <SelectTrigger className="w-[110px] h-8 text-xs">
            <SelectValue placeholder="Direction" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="LONG">Long</SelectItem>
            <SelectItem value="SHORT">Short</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sortBy} onValueChange={setSortBy}>
          <SelectTrigger className="w-[120px] h-8 text-xs">
            <SelectValue placeholder="Sort" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="confidence">Confidence</SelectItem>
            <SelectItem value="rr">R:R</SelectItem>
            <SelectItem value="time">Time</SelectItem>
          </SelectContent>
        </Select>
        <Button size="sm" className="h-8 gap-1 text-xs" onClick={handleScan} disabled={scanning}>
          <Zap className={scanning ? 'w-3 h-3 animate-pulse' : 'w-3 h-3'} />
          {scanning ? 'Scanning...' : 'Scan'}
        </Button>
        <RefreshButton onClick={refreshScan} loading={scanLoading} />
      </div>

      {scanError && <ErrorBanner message={scanError} onRetry={refreshScan} />}

      <div className="grid grid-cols-5 gap-4">
        {/* Signal Table */}
        <Card className="col-span-3 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center justify-between">
              <span>Signal Feed</span>
              <Badge variant="outline" className="text-[10px]">{filtered.length} signals</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[540px]">
              {scanLoading && !scanResult ? (
                <div className="space-y-2">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <Skeleton key={i} className="h-10 w-full" />
                  ))}
                </div>
              ) : filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-[200px] text-muted-foreground">
                  <Filter className="w-8 h-8 mb-3 opacity-40" />
                  <p className="text-sm">No signals match your filters</p>
                </div>
              ) : (
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border/40">
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground">Pair</th>
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground">Dir</th>
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground text-right">Entry</th>
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground text-right">SL</th>
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground text-right">TP</th>
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground">Conf</th>
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground">Engine</th>
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground">Time</th>
                      <th className="text-[10px] uppercase tracking-wider py-2 text-muted-foreground text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map(s => (
                      <tr
                        key={s.id}
                        className={`border-b border-border/20 cursor-pointer transition-colors hover:bg-muted/30 ${selectedSignal?.id === s.id ? 'bg-primary/10' : ''}`}
                        onClick={() => setSelectedSignal(s)}
                      >
                        <td className="py-2 text-xs font-mono font-medium">{s.pair}</td>
                        <td className="py-2">
                          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${s.direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                            {s.direction}
                          </span>
                        </td>
                        <td className="py-2 text-xs font-mono text-right">{s.entry.toFixed(5)}</td>
                        <td className="py-2 text-xs font-mono text-right text-short">{s.sl.toFixed(5)}</td>
                        <td className="py-2 text-xs font-mono text-right text-long">{s.tp.toFixed(5)}</td>
                        <td className="py-2">
                          <span className={`text-xs font-mono font-bold ${s.confidence >= 85 ? 'text-long' : s.confidence >= 70 ? 'text-warning' : 'text-muted-foreground'}`}>
                            {s.confidence}%
                          </span>
                        </td>
                        <td className="py-2 text-[10px] text-muted-foreground">{s.engine}</td>
                        <td className="py-2 text-[10px] font-mono text-muted-foreground">
                          {format(new Date(s.timestamp), 'HH:mm:ss')}
                        </td>
                        <td className="py-2 text-right">
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 w-6 p-0"
                            onClick={e => { e.stopPropagation(); setConfirmSignal(s); }}
                            disabled={executeDisabled}
                          >
                            <Play className="w-3 h-3 text-primary" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </ScrollArea>
          </CardContent>
        </Card>

        {/* Signal Detail */}
        <Card className="col-span-2 border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold">Signal Detail</CardTitle>
          </CardHeader>
          <CardContent>
            {selectedSignal ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className="text-lg font-mono font-bold">{selectedSignal.pair}</span>
                    <span className={`text-xs font-bold px-2 py-1 rounded ${selectedSignal.direction === 'LONG' ? 'bg-long/20 text-long' : 'bg-short/20 text-short'}`}>
                      {selectedSignal.direction}
                    </span>
                  </div>
                  <Badge variant="outline" className="text-[10px]">{selectedSignal.engine}</Badge>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="p-3 rounded-lg bg-muted/30">
                    <p className="text-[10px] text-muted-foreground uppercase">Entry Price</p>
                    <p className="text-sm font-mono font-bold mt-0.5">{selectedSignal.entry.toFixed(5)}</p>
                  </div>
                  <div className="p-3 rounded-lg bg-muted/30">
                    <p className="text-[10px] text-muted-foreground uppercase">Confidence</p>
                    <p className="text-sm font-mono font-bold mt-0.5 text-primary">{selectedSignal.confidence}%</p>
                  </div>
                  <div className="p-3 rounded-lg bg-short/10 border border-short/20">
                    <p className="text-[10px] text-short uppercase">Stop Loss</p>
                    <p className="text-sm font-mono font-bold mt-0.5 text-short">{selectedSignal.sl.toFixed(5)}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      Risk: {((Math.abs(selectedSignal.entry - selectedSignal.sl) / selectedSignal.entry) * 100).toFixed(2)}%
                    </p>
                  </div>
                  <div className="p-3 rounded-lg bg-long/10 border border-long/20">
                    <p className="text-[10px] text-long uppercase">Take Profit</p>
                    <p className="text-sm font-mono font-bold mt-0.5 text-long">{selectedSignal.tp.toFixed(5)}</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      R:R {selectedSignal.rRatio.toFixed(2)}
                    </p>
                  </div>
                </div>

                {selectedSignal.tp2 && (
                  <div className="flex items-center gap-2 p-2 rounded-lg bg-long/5">
                    <ArrowUpRight className="w-3.5 h-3.5 text-long" />
                    <span className="text-xs text-muted-foreground">TP2: </span>
                    <span className="text-xs font-mono font-medium">{selectedSignal.tp2.toFixed(5)}</span>
                  </div>
                )}
                {selectedSignal.tp3 && (
                  <div className="flex items-center gap-2 p-2 rounded-lg bg-long/5">
                    <ArrowUpRight className="w-3.5 h-3.5 text-long" />
                    <span className="text-xs text-muted-foreground">TP3: </span>
                    <span className="text-xs font-mono font-medium">{selectedSignal.tp3.toFixed(5)}</span>
                  </div>
                )}

                {selectedSignal.factors && selectedSignal.factors.length > 0 && (
                  <div>
                    <p className="text-[10px] text-muted-foreground uppercase mb-2">Confluence Factors</p>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedSignal.factors.map(f => (
                        <Badge key={f} variant="secondary" className="text-[10px]">{f}</Badge>
                      ))}
                    </div>
                  </div>
                )}

                {selectedSignal.notes && (
                  <div className="p-3 rounded-lg bg-muted/30">
                    <p className="text-[10px] text-muted-foreground uppercase mb-1">Notes</p>
                    <p className="text-xs">{selectedSignal.notes}</p>
                  </div>
                )}

                <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                  <span>Timeframe: {selectedSignal.timeframe}</span>
                  <span>Votes: {selectedSignal.votes || 0}/10</span>
                </div>

                <Button
                  className="w-full gap-2"
                  onClick={() => setConfirmSignal(selectedSignal)}
                  disabled={executeDisabled}
                >
                  <Play className="w-4 h-4" />
                  {executeLabel}
                </Button>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-[400px] text-muted-foreground">
                <Eye className="w-8 h-8 mb-3 opacity-40" />
                <p className="text-sm">Select a signal to view details</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Execute Confirm Dialog */}
      <AlertDialog open={!!confirmSignal} onOpenChange={() => setConfirmSignal(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Execution</AlertDialogTitle>
            <AlertDialogDescription>
              Execute {confirmSignal?.direction} {confirmSignal?.pair} at {confirmSignal?.entry.toFixed(5)}?
              <br />
              SL: {confirmSignal?.sl.toFixed(5)} | TP: {confirmSignal?.tp.toFixed(5)}
              {isPaper && <span className="block mt-2 text-warning">This will execute in PAPER mode.</span>}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleExecute} disabled={executing}>
              {executing ? 'Executing...' : 'Confirm'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
