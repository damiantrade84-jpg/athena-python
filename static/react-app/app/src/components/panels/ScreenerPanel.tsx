import { useState, useCallback } from 'react';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Progress } from '@/components/ui/progress';
import { ErrorBanner } from '@/components/shared';
import { Filter, Search, Activity } from 'lucide-react';
import { fmtNum } from '@/lib/utils';
import type { ScreenerCandidate, ScreenerScanResponse } from '@/types';

const DEFAULT_MIN_MARKET_CAP = 50_000_000_000;
const DEFAULT_LIMIT = 50;

interface MicroHealth {
  pair: string;
  quality: number;
  last_update: string;
  status: string;
}

function fmtCompact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
}

function fmtPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return fmtNum(value, value >= 100 ? 2 : 4);
}

function CandidateTable({ rows, emptyMessage }: { rows: ScreenerCandidate[]; emptyMessage: string }) {
  if (rows.length === 0) {
    return <div className="text-center text-muted-foreground py-8 text-sm">{emptyMessage}</div>;
  }

  return (
    <table className="w-full text-left">
      <thead>
        <tr className="border-b border-border/40">
          <th className="text-[10px] uppercase py-2 text-muted-foreground">Symbol</th>
          <th className="text-[10px] uppercase py-2 text-muted-foreground">Name</th>
          <th className="text-[10px] uppercase py-2 text-muted-foreground">Exchange</th>
          <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Price</th>
          <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">Market Cap</th>
          <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">52w High</th>
          <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">52w Low</th>
          <th className="text-[10px] uppercase py-2 text-muted-foreground text-right">200d New Hi</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(row => (
          <tr key={`${row.symbol}-${row.exchange}`} className="border-b border-border/20 hover:bg-muted/30 transition-colors">
            <td className="py-2 text-xs font-mono font-bold">{row.symbol || '—'}</td>
            <td className="py-2 text-xs max-w-[180px] truncate" title={row.name}>{row.name || '—'}</td>
            <td className="py-2 text-xs font-mono">{row.exchange || '—'}</td>
            <td className="py-2 text-xs font-mono text-right">{fmtPrice(row.price)}</td>
            <td className="py-2 text-xs font-mono text-right">{fmtCompact(row.marketCap)}</td>
            <td className="py-2 text-xs font-mono text-right">{fmtPrice(row['52wHigh'])}</td>
            <td className="py-2 text-xs font-mono text-right">{fmtPrice(row['52wLow'])}</td>
            <td className="py-2 text-xs font-mono text-right">{fmtNum(row['200dNewHi'], 2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ScreenerPanel() {
  const [minMarketCap, setMinMarketCap] = useState(DEFAULT_MIN_MARKET_CAP);
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [scanData, setScanData] = useState<ScreenerScanResponse | null>(null);
  const [scanning, setScanning] = useState(false);

  const { post: postScan, error: scanError } = useApiPost<ScreenerScanResponse>();
  const { data: microHealth, loading: microLoading, error: microError } = useApiPoll<MicroHealth[]>('/api/microstructure-health', 0);

  const newCandidates = scanData?.newCandidates ?? [];
  const alreadyTracked = scanData?.alreadyTracked ?? [];
  const hasScanResults = newCandidates.length > 0 || alreadyTracked.length > 0;

  const handleScan = useCallback(async () => {
    setScanning(true);
    setScanData(null);
    const res = await postScan('/api/screener-scan', {
      minMarketCap,
      limit: Math.min(Math.max(limit, 1), 100),
    });
    if (res?.success) {
      setScanData({
        success: true,
        newCandidates: res.newCandidates ?? [],
        alreadyTracked: res.alreadyTracked ?? [],
        totalScanned: res.totalScanned ?? 0,
        scannedAt: res.scannedAt ?? '',
      });
    }
    setScanning(false);
  }, [minMarketCap, limit, postScan]);

  return (
    <div className="space-y-4">
      <Tabs defaultValue="screener">
        <TabsList>
          <TabsTrigger value="screener" className="text-xs">Screener</TabsTrigger>
          <TabsTrigger value="micro" className="text-xs">Microstructure Health</TabsTrigger>
        </TabsList>

        <TabsContent value="screener" className="mt-2 space-y-4">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="panel-title flex items-center gap-2">
                <Filter className="w-4 h-4 text-primary" />
                Screener Filters
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-[10px] text-muted-foreground">
                EODHD momentum screener — discovers high-cap stocks near 52-week highs
              </p>
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground">Min Market Cap</span>
                  <Input
                    type="number"
                    value={minMarketCap}
                    onChange={e => setMinMarketCap(parseInt(e.target.value, 10) || DEFAULT_MIN_MARKET_CAP)}
                    className="w-36 h-8 text-xs font-mono"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground">Limit</span>
                  <Input
                    type="number"
                    min={1}
                    max={100}
                    value={limit}
                    onChange={e => setLimit(parseInt(e.target.value, 10) || DEFAULT_LIMIT)}
                    className="w-20 h-8 text-xs font-mono"
                  />
                </div>
                <Button size="sm" className="h-8 text-xs gap-1" onClick={handleScan} disabled={scanning}>
                  <Search className="w-3 h-3" />
                  {scanning ? 'Scanning...' : 'Scan'}
                </Button>
              </div>
            </CardContent>
          </Card>

          {scanError && <ErrorBanner message={scanError} />}

          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="panel-title flex items-center gap-2">Results</CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[500px]">
                {scanning ? (
                  <div className="space-y-2">
                    {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
                  </div>
                ) : !scanData ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">Run a scan to see results</div>
                ) : !hasScanResults ? (
                  <div className="text-center text-muted-foreground py-12 text-sm">No candidates found</div>
                ) : (
                  <div className="space-y-6">
                    <div className="flex items-center gap-4 text-[10px] text-muted-foreground font-mono">
                      <span>Scanned: {scanData.totalScanned}</span>
                      {scanData.scannedAt && <span>At: {scanData.scannedAt}</span>}
                    </div>

                    <div>
                      <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
                        New Candidates ({newCandidates.length})
                      </h3>
                      <CandidateTable rows={newCandidates} emptyMessage="No new candidates" />
                    </div>

                    <div>
                      <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
                        Already Tracked ({alreadyTracked.length})
                      </h3>
                      <CandidateTable rows={alreadyTracked} emptyMessage="No already-tracked matches" />
                    </div>
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="micro" className="mt-2">
          <Card className="border-border/60 bg-card/50">
            <CardHeader className="pb-2">
              <CardTitle className="panel-title flex items-center gap-2">
                <Activity className="w-4 h-4 text-primary" />
                Microstructure Health
              </CardTitle>
            </CardHeader>
            <CardContent>
              {microError && <ErrorBanner message={microError} />}
              {microLoading ? (
                <Skeleton className="h-20 w-full" />
              ) : microHealth && microHealth.length > 0 ? (
                <div className="space-y-2">
                  {microHealth.map(m => (
                    <div key={m.pair} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                      <span className="text-xs font-mono font-bold">{m.pair}</span>
                      <div className="flex items-center gap-4 flex-1 mx-4">
                        <Progress value={m.quality} className="h-1.5 flex-1" />
                        <span className="text-xs font-mono">{m.quality.toFixed(0)}%</span>
                      </div>
                      <Badge variant="outline" className="text-[10px]">{m.status}</Badge>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center text-muted-foreground py-12 text-sm">No microstructure data</div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
