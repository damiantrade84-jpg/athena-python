import { useState, useCallback } from 'react';
import { useStore } from '@/hooks/useStore';
import { useApiPoll, useApiPost } from '@/hooks/useApiData';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { Separator } from '@/components/ui/separator';
import { ErrorBanner } from '@/components/shared';
import { Settings, Save, Check, X } from 'lucide-react';

interface ScanSettings {
  bt_min: Record<string, number>;
  features: Record<string, boolean>;
  execution: {
    min_score: number;
    risk_per_trade: number;
    max_positions: number;
  };
}

interface AdvisoryThreshold {
  id: string;
  asset_class: string;
  current: number;
  recommended: number;
  reason: string;
  timestamp: string;
}

export default function ScanConfigPanel() {
  const { showToast } = useStore();
  const [localBtMin, setLocalBtMin] = useState<Record<string, number>>({});
  const [localFeatures, setLocalFeatures] = useState<Record<string, boolean>>({});
  const [localExecution, setLocalExecution] = useState({ min_score: 0, risk_per_trade: 0, max_positions: 0 });

  const { data: scanSettings, loading: settingsLoading, error: settingsError, refresh: refreshSettings } = useApiPoll<ScanSettings>('/api/scan-settings', 0);
  const { data: btMin, loading: btMinLoading, error: btMinError, refresh: refreshBtMin } = useApiPoll<{ thresholds: Record<string, number> }>('/api/bt-min', 0);
  const { data: features, loading: featuresLoading, error: featuresError, refresh: refreshFeatures } = useApiPoll<Record<string, boolean>>('/api/feature-toggles', 0);
  const { data: advisories, loading: advisoryLoading, error: advisoryError, refresh: refreshAdvisories } = useApiPoll<AdvisoryThreshold[]>('/api/advisory-thresholds', 0);
  const { data: executionConfig, loading: execLoading, error: execError, refresh: refreshExec } = useApiPoll<{ min_score: number; risk_per_trade: number; max_positions: number }>('/api/execution-config', 0);

  const { post: postBtMin } = useApiPost<void>();
  const { post: postFeatures } = useApiPost<void>();
  const { post: postAdvisory } = useApiPost<void>();
  const { post: postExec } = useApiPost<void>();

  const handleSaveBtMin = useCallback(async () => {
    await postBtMin('/api/bt-min', { thresholds: localBtMin });
    showToast('BT_MIN thresholds saved', 'success');
    refreshBtMin();
  }, [localBtMin, postBtMin, showToast, refreshBtMin]);

  const handleSaveFeatures = useCallback(async () => {
    await postFeatures('/api/feature-toggles', localFeatures);
    showToast('Feature toggles saved', 'success');
    refreshFeatures();
  }, [localFeatures, postFeatures, showToast, refreshFeatures]);

  const handleSaveExecution = useCallback(async () => {
    await postExec('/api/execution-config', localExecution);
    showToast('Execution config saved', 'success');
    refreshExec();
  }, [localExecution, postExec, showToast, refreshExec]);

  const handleAdvisory = useCallback(async (id: string, action: 'approve' | 'reject') => {
    await postAdvisory(`/api/advisory-thresholds/${id}/${action}`);
    showToast(`Advisory ${action}d`, 'success');
    refreshAdvisories();
  }, [postAdvisory, showToast, refreshAdvisories]);

  // Initialize local state when data loads
  useState(() => {
    if (btMin?.thresholds) setLocalBtMin(btMin.thresholds);
    if (features) setLocalFeatures(features);
    if (executionConfig) setLocalExecution(executionConfig);
  });

  return (
    <div className="space-y-5">
      {(settingsError || btMinError || featuresError || advisoryError || execError) && (
        <ErrorBanner
          message={[settingsError, btMinError, featuresError, advisoryError, execError].filter(Boolean).join(' | ')}
          onRetry={() => { refreshSettings(); refreshBtMin(); refreshFeatures(); refreshAdvisories(); refreshExec(); }}
        />
      )}

      <div className="grid grid-cols-2 gap-5">
        {/* BT_MIN Thresholds */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Settings className="w-4 h-4 text-primary" />
              BT_MIN Thresholds
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {btMinLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : btMin?.thresholds ? (
              <>
                {Object.entries(btMin.thresholds).map(([cls, val]) => (
                  <div key={cls} className="flex items-center justify-between">
                    <span className="text-xs capitalize">{cls}</span>
                    <Input
                      type="number"
                      className="w-24 h-8 text-xs font-mono"
                      defaultValue={val}
                      onChange={e => setLocalBtMin(prev => ({ ...prev, [cls]: parseFloat(e.target.value) }))}
                    />
                  </div>
                ))}
                <Button size="sm" className="w-full gap-1 text-xs" onClick={handleSaveBtMin}>
                  <Save className="w-3 h-3" /> Save Thresholds
                </Button>
              </>
            ) : (
              <div className="text-xs text-muted-foreground">No threshold data</div>
            )}
          </CardContent>
        </Card>

        {/* Feature Toggles */}
        <Card className="border-border/60 bg-card/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Settings className="w-4 h-4 text-primary" />
              Feature Toggles
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {featuresLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : features ? (
              <>
                {Object.entries(features).map(([key, val]) => (
                  <div key={key} className="flex items-center justify-between">
                    <span className="text-xs capitalize">{key.replace(/_/g, ' ')}</span>
                    <Switch
                      checked={val}
                      onCheckedChange={checked => setLocalFeatures(prev => ({ ...prev, [key]: checked }))}
                    />
                  </div>
                ))}
                <Button size="sm" className="w-full gap-1 text-xs" onClick={handleSaveFeatures}>
                  <Save className="w-3 h-3" /> Save Features
                </Button>
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
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <Settings className="w-4 h-4 text-primary" />
            Execution Config
          </CardTitle>
        </CardHeader>
        <CardContent>
          {execLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : executionConfig ? (
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="text-[10px] uppercase text-muted-foreground">Min Score</label>
                <Input
                  type="number"
                  className="h-8 text-xs font-mono mt-1"
                  defaultValue={executionConfig.min_score}
                  onChange={e => setLocalExecution(prev => ({ ...prev, min_score: parseFloat(e.target.value) }))}
                />
              </div>
              <div>
                <label className="text-[10px] uppercase text-muted-foreground">Risk per Trade (%)</label>
                <Input
                  type="number"
                  className="h-8 text-xs font-mono mt-1"
                  defaultValue={executionConfig.risk_per_trade}
                  onChange={e => setLocalExecution(prev => ({ ...prev, risk_per_trade: parseFloat(e.target.value) }))}
                />
              </div>
              <div>
                <label className="text-[10px] uppercase text-muted-foreground">Max Positions</label>
                <Input
                  type="number"
                  className="h-8 text-xs font-mono mt-1"
                  defaultValue={executionConfig.max_positions}
                  onChange={e => setLocalExecution(prev => ({ ...prev, max_positions: parseInt(e.target.value) }))}
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

      {/* Advisory Thresholds */}
      <Card className="border-border/60 bg-card/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <Settings className="w-4 h-4 text-primary" />
            Advisory Threshold Recommendations
          </CardTitle>
        </CardHeader>
        <CardContent>
          {advisoryLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : advisories && advisories.length > 0 ? (
            <div className="space-y-2">
              {advisories.map(adv => (
                <div key={adv.id} className="flex items-center justify-between p-3 rounded-md bg-muted/30">
                  <div>
                    <span className="text-xs font-mono font-bold">{adv.asset_class}</span>
                    <p className="text-[10px] text-muted-foreground">Current: {adv.current} → Recommended: {adv.recommended}</p>
                    <p className="text-[10px] text-muted-foreground">{adv.reason}</p>
                  </div>
                  <div className="flex items-center gap-2">
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
