import { useEffect, useState, useCallback } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorBanner } from '@/components/shared';
import { Settings, Save, Check, X } from 'lucide-react';
import { fmtNum } from '@/lib/utils';

interface ScanSettingsResponse {
  settings?: Record<string, unknown>;
  unverified_scan_keys?: string[];
}

interface BtMinResponse {
  bt_min?: Record<string, number>;
  live_class?: Record<string, number>;
  backtest_use_bt_min_thresholds?: boolean;
  research_mode?: boolean;
  backtest_uses_bt_min_chain?: boolean;
}

interface FeatureToggles {
  intermarket?: boolean;
  news_sentiment?: boolean;
  sentiment_gate?: boolean;
  event_risk?: boolean;
}

interface ExecutionConfig {
  executionEnabled?: boolean;
  autoExecute?: boolean;
  autoExecuteMinScore?: number;
  autoExecuteMinGrade?: string;
  maxPortfolioHeat?: number;
  maxOpenPositions?: number;
  riskPct?: number;
}

interface AdvisoryRecommendation {
  id: string;
  scope_type?: string;
  scope_key?: string;
  current_value?: number;
  proposed_value?: number;
  delta?: number;
  rationale?: string;
  evidence?: Record<string, unknown>;
  generated_at?: string;
}

interface AdvisorySnapshot {
  summary?: { pending?: number; approved?: number; rejected?: number; last_recomputed?: string };
  current_policies?: Record<string, unknown>;
  pending?: AdvisoryRecommendation[];
  approved?: AdvisoryRecommendation[];
  rejected?: AdvisoryRecommendation[];
}

const ASSET_CLASSES = ['crypto', 'forex', 'commodity', 'stock', 'index'] as const;

export default function ScanConfigPanel() {
  const { showToast } = useStore();
  const [localLiveClass, setLocalLiveClass] = useState<Record<string, number>>({});
  const [localExec, setLocalExec] = useState<ExecutionConfig>({});

  const { data: scanSettings, loading: settingsLoading, error: settingsError, refresh: refreshSettings }
    = useApiPoll<ScanSettingsResponse>('/api/scan-settings', 0);
  const { data: btMin, loading: btMinLoading, error: btMinError, refresh: refreshBtMin }
    = useApiPoll<BtMinResponse>('/api/bt-min', 0);
  const { data: features, loading: featuresLoading, error: featuresError, refresh: refreshFeatures }
    = useApiPoll<FeatureToggles>('/api/feature-toggles', 0);
  const { data: advisories, loading: advisoryLoading, error: advisoryError, refresh: refreshAdvisories }
    = useApiPoll<AdvisorySnapshot>('/api/advisory-thresholds', 0);
  const { data: executionConfig, loading: execLoading, error: execError, refresh: refreshExec }
    = useApiPoll<ExecutionConfig>('/api/execution-config', 0);

  const { post: postBtMin } = useApiPost<{ live_class: Record<string, number> }>();
  const { post: postFeatures } = useApiPost<FeatureToggles>();
  const { post: postExec } = useApiPost<ExecutionConfig>();
  const { post: postAdvisory } = useApiPost<{ saved?: boolean; error?: string }>();

  useEffect(() => {
    const live = btMin?.live_class;
    if (live) setLocalLiveClass(prev => ({ ...live, ...prev }));
  }, [btMin]);

  useEffect(() => {
    if (executionConfig) setLocalExec(prev => ({ ...executionConfig, ...prev }));
  }, [executionConfig]);

  const handleSaveLiveClass = useCallback(async () => {
    const body: Record<string, number> = {};
    for (const cls of ASSET_CLASSES) {
      if (typeof localLiveClass[cls] === 'number' && Number.isFinite(localLiveClass[cls])) {
        body[cls] = localLiveClass[cls];
      }
    }
    const res = await postBtMin('/api/bt-min', body);
    if (res && !(res as { error?: string }).error) {
      showToast('Live Engine A thresholds saved', 'success');
      refreshBtMin();
    } else {
      showToast('Failed to save live thresholds', 'error');
    }
  }, [localLiveClass, postBtMin, showToast, refreshBtMin]);

  const handleToggleFeature = useCallback(async (feature: keyof FeatureToggles, next: boolean) => {
    const res = await postFeatures('/api/feature-toggles', { feature, action: next ? 'enable' : 'disable' });
    if (res && !(res as { error?: string }).error) {
      showToast(`${feature.replace(/_/g, ' ')} ${next ? 'enabled' : 'disabled'}`, 'success');
      refreshFeatures();
    } else {
      showToast(`Failed to toggle ${feature.replace(/_/g, ' ')}`, 'error');
    }
  }, [postFeatures, showToast, refreshFeatures]);

  const handleSaveExecution = useCallback(async () => {
    const res = await postExec('/api/execution-config', localExec as unknown as Record<string, unknown>);
    if (res && !(res as { error?: string }).error) {
      showToast('Execution config saved', 'success');
      refreshExec();
    } else {
      showToast('Failed to save execution config', 'error');
    }
  }, [localExec, postExec, showToast, refreshExec]);

  const handleAdvisory = useCallback(async (id: string, action: 'approve' | 'reject') => {
    const res = await postAdvisory(`/api/advisory-thresholds/${id}/${action}`);
    if (res?.error) {
      showToast(`Advisory ${action} failed: ${res.error}`, 'error');
    } else {
      showToast(`Advisory ${action}d`, 'success');
    }
    refreshAdvisories();
    refreshBtMin();
  }, [postAdvisory, showToast, refreshAdvisories, refreshBtMin]);

  const errorMsg = [settingsError, btMinError, featuresError, advisoryError, execError].filter(Boolean).join(' | ');

  return (
    <div className="space-y-5">
      {errorMsg && (
        <ErrorBanner
          message={errorMsg}
          onRetry={() => { refreshSettings(); refreshBtMin(); refreshFeatures(); refreshAdvisories(); refreshExec(); }}
        />
      )}

      <div className="grid grid-cols-2 gap-5">
        {/* Live Engine A class thresholds */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
              <Settings className="w-4 h-4 text-primary" /> Live Engine A Thresholds
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {btMinLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <>
                <p className="text-[10px] text-muted-foreground">
                  Active runtime ENGINE_A_SCORE_GROUP_THRESHOLDS (Stage 4.2 — BT_MIN retired).
                  {btMin?.research_mode ? ' Research mode on.' : ''}
                </p>

                <div className="space-y-2">
                  <p className="text-[10px] uppercase text-muted-foreground">Per asset class</p>
                  {ASSET_CLASSES.map(cls => {
                    const live = localLiveClass[cls] ?? btMin?.live_class?.[cls];
                    return (
                      <div key={cls} className="flex items-center justify-between gap-2">
                        <span className="text-xs capitalize font-medium">{cls}</span>
                        <Input
                          type="number"
                          step={0.05}
                          className="w-24 h-8 text-xs font-mono"
                          value={live ?? ''}
                          onChange={e => {
                            const v = parseFloat(e.target.value);
                            setLocalLiveClass(prev => ({ ...prev, [cls]: Number.isFinite(v) ? v : 0 }));
                          }}
                        />
                      </div>
                    );
                  })}
                  <Button size="sm" className="w-full gap-1 text-xs" onClick={handleSaveLiveClass}>
                    <Save className="w-3 h-3" /> Save live thresholds
                  </Button>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Feature Toggles */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
              <Settings className="w-4 h-4 text-primary" /> Feature Toggles
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {featuresLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : features ? (
              <>
                {(Object.keys(features) as (keyof FeatureToggles)[]).map(key => (
                  <div key={key} className="flex items-center justify-between p-2 rounded-md bg-muted/30">
                    <div>
                      <p className="text-xs capitalize font-medium">{key.replace(/_/g, ' ')}</p>
                      <p className="text-[10px] text-muted-foreground">
                        {key === 'intermarket' && 'Intermarket confirmation overlay'}
                        {key === 'news_sentiment' && 'EODHD news → score blend'}
                        {key === 'sentiment_gate' && 'Block trades on bearish sentiment'}
                        {key === 'event_risk' && 'Block trades around macro events'}
                      </p>
                    </div>
                    <Switch
                      checked={!!features[key]}
                      onCheckedChange={(next) => handleToggleFeature(key, next)}
                    />
                  </div>
                ))}
              </>
            ) : (
              <div className="text-xs text-muted-foreground">No feature data</div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Execution Config */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
            <Settings className="w-4 h-4 text-primary" /> Execution Config
          </CardTitle>
        </CardHeader>
        <CardContent>
          {execLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : executionConfig ? (
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-1">
                <label className="text-[10px] uppercase text-muted-foreground">Execution enabled</label>
                <Switch
                  checked={!!localExec.executionEnabled}
                  onCheckedChange={v => setLocalExec(prev => ({ ...prev, executionEnabled: v }))}
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] uppercase text-muted-foreground">Auto-execute</label>
                <Switch
                  checked={!!localExec.autoExecute}
                  onCheckedChange={v => setLocalExec(prev => ({ ...prev, autoExecute: v }))}
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] uppercase text-muted-foreground">Auto Min Grade</label>
                <Input
                  className="h-8 text-xs font-mono"
                  value={localExec.autoExecuteMinGrade ?? ''}
                  onChange={e => setLocalExec(prev => ({ ...prev, autoExecuteMinGrade: e.target.value.toUpperCase() }))}
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] uppercase text-muted-foreground">Auto Min Score</label>
                <Input
                  type="number" step={0.05}
                  className="h-8 text-xs font-mono"
                  value={localExec.autoExecuteMinScore ?? ''}
                  onChange={e => setLocalExec(prev => ({ ...prev, autoExecuteMinScore: parseFloat(e.target.value) }))}
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] uppercase text-muted-foreground">Risk per trade</label>
                <Input
                  type="number" step={0.001}
                  className="h-8 text-xs font-mono"
                  value={localExec.riskPct ?? ''}
                  onChange={e => setLocalExec(prev => ({ ...prev, riskPct: parseFloat(e.target.value) }))}
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] uppercase text-muted-foreground">Max portfolio heat</label>
                <Input
                  type="number" step={0.01}
                  className="h-8 text-xs font-mono"
                  value={localExec.maxPortfolioHeat ?? ''}
                  onChange={e => setLocalExec(prev => ({ ...prev, maxPortfolioHeat: parseFloat(e.target.value) }))}
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] uppercase text-muted-foreground">Max open positions</label>
                <Input
                  type="number" step={1}
                  className="h-8 text-xs font-mono"
                  value={localExec.maxOpenPositions ?? ''}
                  onChange={e => setLocalExec(prev => ({ ...prev, maxOpenPositions: parseInt(e.target.value, 10) }))}
                />
              </div>
              <div className="col-span-3">
                <Button size="sm" className="gap-1 text-xs" onClick={handleSaveExecution}>
                  <Save className="w-3 h-3" /> Save Execution Config
                </Button>
              </div>
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">No execution config</div>
          )}
        </CardContent>
      </Card>

      {/* Scan Runtime Snapshot */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>Scan Runtime Snapshot</CardTitle>
        </CardHeader>
        <CardContent>
          {settingsLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : scanSettings?.settings ? (
            <div className="grid grid-cols-3 gap-3">
              {Object.entries(scanSettings.settings).map(([k, v]) => {
                const display = typeof v === 'object' && v !== null
                  ? JSON.stringify(v)
                  : String(v);
                return (
                  <div key={k} className="p-2 rounded-md bg-muted/30">
                    <p className="text-[10px] uppercase text-muted-foreground">{k}</p>
                    <p className="text-xs font-mono break-all">{display}</p>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">No scan settings</div>
          )}
        </CardContent>
      </Card>

      {/* Advisory Thresholds */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-xs font-semibold flex items-center gap-2 uppercase tracking-wider" style={{ fontFamily: "'Cinzel', serif", letterSpacing: '0.12em' }}>
            <Settings className="w-4 h-4 text-primary" /> Advisory Threshold Recommendations
          </CardTitle>
          {advisories?.summary && (
            <div className="flex gap-2 text-[10px]">
              <Badge variant="outline">Pending {advisories.summary.pending ?? 0}</Badge>
              <Badge variant="outline">Approved {advisories.summary.approved ?? 0}</Badge>
              <Badge variant="outline">Rejected {advisories.summary.rejected ?? 0}</Badge>
            </div>
          )}
        </CardHeader>
        <CardContent>
          {advisoryLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : advisories?.pending && advisories.pending.length > 0 ? (
            <div className="space-y-2">
              {advisories.pending.map(adv => (
                <div key={adv.id} className="flex items-start justify-between p-3 rounded-md bg-muted/30 gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-[10px]">{adv.scope_type}</Badge>
                      <span className="text-xs font-mono font-bold">{adv.scope_key}</span>
                    </div>
                    <p className="text-[10px] text-muted-foreground mt-1">
                      Current: <span className="font-mono">{fmtNum(adv.current_value, 3)}</span>
                      {' → '}
                      Proposed: <span className="font-mono">{fmtNum(adv.proposed_value, 3)}</span>
                      {adv.delta != null && (
                        <span className="ml-1">(Δ {fmtNum(adv.delta, 3)})</span>
                      )}
                    </p>
                    {adv.rationale && (
                      <p className="text-[10px] text-muted-foreground mt-1">{adv.rationale}</p>
                    )}
                  </div>
                  <div className="flex flex-col gap-1">
                    <Button size="sm" variant="outline" className="h-7 gap-1 text-xs" onClick={() => handleAdvisory(adv.id, 'approve')}>
                      <Check className="w-3 h-3" /> Approve
                    </Button>
                    <Button size="sm" variant="outline" className="h-7 gap-1 text-xs" onClick={() => handleAdvisory(adv.id, 'reject')}>
                      <X className="w-3 h-3" /> Reject
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground">No pending advisory recommendations</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
