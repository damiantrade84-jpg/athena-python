import { useCallback, useEffect, useState } from 'react';
import apiClient from '@/lib/apiClient';
import { useStore } from '@/hooks/useStore';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

interface ExitModeConfig {
  globalDefault: string;
  byScoreGroup: Record<string, string>;
  advisablePipByScoreGroup: Record<string, { min_pip?: number; max_pip?: number }>;
  knownScoreGroups: string[];
  validModes: string[];
}

const GROUP_SENTINEL = 'default'; // per-group "use global" row choice

export default function ExitStrategyPanel() {
  const { showToast } = useStore();
  const [cfg, setCfg] = useState<ExitModeConfig | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setCfg(await apiClient.get<ExitModeConfig>('/api/exit-mode-config'));
    } catch (err) {
      showToast(`Failed to load exit config: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
    }
  }, [showToast]);

  useEffect(() => { void load(); }, [load]);

  const setGroupMode = (group: string, mode: string) => {
    setCfg((c) => {
      if (!c) return c;
      const next = { ...c.byScoreGroup };
      if (mode === GROUP_SENTINEL) delete next[group];
      else next[group] = mode;
      return { ...c, byScoreGroup: next };
    });
  };

  const setGroupPip = (group: string, bound: 'min_pip' | 'max_pip', raw: string) => {
    setCfg((c) => {
      if (!c) return c;
      const next = { ...c.advisablePipByScoreGroup };
      const entry = { ...(next[group] || {}) };
      const v = parseFloat(raw);
      if (!raw || !Number.isFinite(v) || v <= 0) delete entry[bound];
      else entry[bound] = v;
      if (Object.keys(entry).length === 0) delete next[group];
      else next[group] = entry;
      return { ...c, advisablePipByScoreGroup: next };
    });
  };

  const save = useCallback(async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      const res = await apiClient.post<{ success?: boolean; errors?: string[] }>(
        '/api/exit-mode-config',
        {
          globalDefault: cfg.globalDefault,
          byScoreGroup: cfg.byScoreGroup,
          advisablePipByScoreGroup: cfg.advisablePipByScoreGroup,
        },
      );
      if (res.success) {
        showToast('Exit strategy saved', 'success');
        await load();
      } else {
        showToast(`Save rejected: ${(res.errors || ['unknown']).join('; ')}`, 'error');
      }
    } catch (err) {
      showToast(`Save failed: ${err instanceof Error ? err.message : 'unknown'}`, 'error');
    } finally {
      setSaving(false);
    }
  }, [cfg, load, showToast]);

  if (!cfg) {
    return <div className="p-6 text-sm text-muted-foreground">Loading exit strategy…</div>;
  }

  return (
    <div className="p-4 space-y-4 max-w-4xl">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Engine A — Exit Strategy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Label className="text-xs w-40">Global default mode</Label>
            <Select
              value={cfg.globalDefault}
              onValueChange={(v) => setCfg((c) => (c ? { ...c, globalDefault: v } : c))}
            >
              <SelectTrigger className="h-8 w-[200px] text-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                {cfg.validModes.map((m) => (
                  <SelectItem key={m} value={m} className="text-xs">{m}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="border-t border-border/40 pt-3">
            <div className="grid grid-cols-[1fr_180px_90px_90px] gap-2 text-[10px] uppercase text-muted-foreground pb-1">
              <span>Score group</span><span>Default mode</span><span>Min pip</span><span>Max pip</span>
            </div>
            <div className="space-y-1 max-h-[60vh] overflow-auto">
              {cfg.knownScoreGroups.map((g) => {
                const band = cfg.advisablePipByScoreGroup[g] || {};
                return (
                  <div key={g} className="grid grid-cols-[1fr_180px_90px_90px] gap-2 items-center">
                    <span className="text-xs font-mono">{g}</span>
                    <Select
                      value={cfg.byScoreGroup[g] ?? GROUP_SENTINEL}
                      onValueChange={(v) => setGroupMode(g, v)}
                    >
                      <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value={GROUP_SENTINEL} className="text-xs">(use global)</SelectItem>
                        {cfg.validModes.map((m) => (
                          <SelectItem key={m} value={m} className="text-xs">{m}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Input
                      type="number" className="h-7 text-xs" placeholder="—"
                      value={band.min_pip ?? ''}
                      onChange={(e) => setGroupPip(g, 'min_pip', e.target.value)}
                    />
                    <Input
                      type="number" className="h-7 text-xs" placeholder="—"
                      value={band.max_pip ?? ''}
                      onChange={(e) => setGroupPip(g, 'max_pip', e.target.value)}
                    />
                  </div>
                );
              })}
            </div>
          </div>

          <div className="flex justify-end">
            <Button size="sm" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save exit strategy'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
