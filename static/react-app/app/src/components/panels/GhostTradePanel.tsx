import { useMemo, useState } from 'react';
import { Ghost, RefreshCw, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import ErrorBanner from '@/components/shared/ErrorBanner';
import { confirmAutoToggle, groupSignals, groupLabels } from '@/features/ghostTrade/display';
import { GhostGroupSection } from '@/features/ghostTrade/GhostGroupSection';
import { GhostPerformancePanel } from '@/features/ghostTrade/GhostPerformance';
import { GhostPositions } from '@/features/ghostTrade/GhostPositions';
import { GhostSafetyBanner } from '@/features/ghostTrade/GhostSafetyBanner';
import { GhostSignalDrawer } from '@/features/ghostTrade/GhostSignalDrawer';
import { useGhostTrade } from '@/features/ghostTrade/useGhostTrade';
import type { GhostDirection, GhostGroup, GhostHealth, GhostPerformance, GhostPosition, GhostScan, GhostSignal } from '@/features/ghostTrade/types';

interface ViewProps {
  health: GhostHealth;
  groups: GhostGroup[];
  signals: GhostSignal[];
  positions: GhostPosition[];
  performance: GhostPerformance;
  loading: boolean;
  error: string | null;
  selectedSignal: GhostSignal | null;
  currentScan?: GhostScan | null;
  onSelectSignal: (signal: GhostSignal | null) => void;
  onScan: () => void;
  onExecute: (id: string) => void;
  onDismiss: (id: string) => void;
  onClosePosition: (id: string) => void;
  onToggleAuto: (enabled: boolean) => void;
}

export function GhostTradeView(props: ViewProps) {
  const [search, setSearch] = useState('');
  const [direction, setDirection] = useState<GhostDirection | 'ALL'>('ALL');
  const [sort, setSort] = useState<'score' | 'symbol' | 'age'>('score');
  const [view, setView] = useState<'card' | 'list'>('card');
  const visibleSignals = useMemo(() => props.signals.filter((signal) => {
    const matchesSearch = !search || `${signal.instrument.canonicalSymbol} ${signal.instrument.brokerSymbol}`.toLowerCase().includes(search.toLowerCase());
    const matchesDirection = direction === 'ALL' || signal.direction === direction;
    return matchesSearch && matchesDirection;
  }), [props.signals, search, direction]);
  const grouped = useMemo(() => groupSignals(visibleSignals), [visibleSignals]);
  const orderedGroups = props.groups.length ? props.groups : (Object.keys(groupLabels) as Array<keyof typeof groupLabels>).map((assetGroup) => ({ assetGroup, instrumentsDiscovered: 0, instrumentsScored: 0, signals: 0, bullish: 0, bearish: 0, neutral: 0, executableDemo: 0, averageScore: null, highestScore: null }));
  const scored = props.currentScan?.scoredCount
    ?? props.groups.reduce((sum, group) => sum + group.instrumentsScored, 0);
  const scanDuration = props.currentScan?.durationMs == null
    ? null
    : `${(props.currentScan.durationMs / 1000).toFixed(1)}s`;
  const handleAutoToggle = (enabled: boolean) => {
    if (confirmAutoToggle(enabled, (message) => window.confirm(message))) {
      props.onToggleAuto(enabled);
    }
  };
  return (
    <div className="space-y-4 p-4">
      <header className="flex flex-wrap items-center gap-3">
        <Ghost className="h-6 w-6 text-violet-300" />
        <div><h2 className="text-lg font-semibold">Ghost Trade</h2><div className="text-xs text-muted-foreground">Independent structural engine · {props.health.mode}</div></div>
        <div className="ml-auto flex flex-wrap items-center gap-3 text-xs">
          <span>Scored {scored}</span><span>Scan {scanDuration || (props.health.scanRunning ? 'running' : 'not run')}</span><span>Eligible {props.signals.filter((signal) => signal.canExecute).length}</span><span>Open {props.positions.length}</span><span>Risk {props.positions.reduce((sum, position) => sum + position.initialRisk, 0).toFixed(4)}</span>
          <Button size="sm" variant="outline" onClick={props.onScan} disabled={props.loading || props.health.scanRunning}><RefreshCw className="mr-1 h-4 w-4" />Scan Now</Button>
          <label className="flex items-center gap-2"><span>Demo auto</span><Switch checked={props.health.demoAutoEnabled} disabled={props.health.mode !== 'DEMO_AUTO' || props.health.executionStatus !== 'DEMO VERIFIED'} onCheckedChange={handleAutoToggle} /></label>
        </div>
      </header>
      <GhostSafetyBanner health={props.health} />
      <div className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-xs">
        <span>MT5: {props.health.mt5Status || props.health.executionStatus}</span>
        <span>Bybit: {props.health.bybitStatus || props.health.executionStatus}</span>
        <span>Last scan: {props.currentScan?.completedAt || 'never'}</span>
        <input aria-label="Search Ghost symbols" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search symbol" className="ml-auto h-8 rounded border bg-background px-2" />
        <select aria-label="Ghost direction filter" value={direction} onChange={(event) => setDirection(event.target.value as GhostDirection | 'ALL')} className="h-8 rounded border bg-background px-2"><option value="ALL">All directions</option><option value="LONG">Long</option><option value="SHORT">Short</option><option value="NEUTRAL">Neutral</option></select>
        <select aria-label="Ghost sort" value={sort} onChange={(event) => setSort(event.target.value as 'score' | 'symbol' | 'age')} className="h-8 rounded border bg-background px-2"><option value="score">Score</option><option value="symbol">Symbol</option><option value="age">Age</option></select>
        <Button size="sm" variant="outline" onClick={() => setView(view === 'card' ? 'list' : 'card')}>{view === 'card' ? 'List view' : 'Card view'}</Button>
      </div>
      {props.error && <ErrorBanner message={props.error} />}
      {props.loading && <div className="flex items-center gap-2 text-sm text-muted-foreground"><RefreshCw className="h-4 w-4 animate-spin" />Loading Ghost Trade…</div>}
      {!props.health.enabled && <div className="flex items-center gap-2 rounded-md border p-3 text-sm"><ShieldAlert className="h-4 w-4" />Ghost Trade is disabled by configuration.</div>}
      <div className="space-y-3">{orderedGroups.map((group) => <GhostGroupSection key={group.assetGroup} group={group} signals={grouped[group.assetGroup]} onSelect={(signal) => props.onSelectSignal(signal)} sort={sort} view={view} />)}</div>
      <GhostPositions positions={props.positions} onClose={props.onClosePosition} />
      <GhostPerformancePanel performance={props.performance} />
      <GhostSignalDrawer health={props.health} signal={props.selectedSignal} onClose={() => props.onSelectSignal(null)} onExecute={props.onExecute} onDismiss={props.onDismiss} />
    </div>
  );
}

export default function GhostTradePanel() {
  const ghost = useGhostTrade();
  if (!ghost.health) {
    return <div className="p-4">{ghost.error ? <ErrorBanner message={ghost.error} /> : 'Loading Ghost Trade…'}</div>;
  }
  return <GhostTradeView health={ghost.health} groups={ghost.groups} signals={ghost.signals} positions={ghost.positions} performance={ghost.performance} currentScan={ghost.currentScan} loading={ghost.loading || ghost.mutating} error={ghost.error} selectedSignal={ghost.selectedSignal} onSelectSignal={ghost.setSelectedSignal} onScan={ghost.scan} onExecute={ghost.execute} onDismiss={ghost.dismiss} onClosePosition={ghost.closePosition} onToggleAuto={ghost.toggleAuto} />;
}
