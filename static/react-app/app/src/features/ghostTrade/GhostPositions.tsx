import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { GhostPosition } from './types';

export function GhostPositions({ positions, onClose }: { positions: GhostPosition[]; onClose: (id: string) => void }) {
  return (
    <Card><CardHeader><CardTitle className="text-sm">Open Ghost positions</CardTitle></CardHeader><CardContent>
      <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="text-left text-muted-foreground"><th>Symbol</th><th>Mode</th><th>Side</th><th>Entry</th><th>Stop</th><th>Target</th><th>Risk</th><th /></tr></thead><tbody>
        {positions.map((position) => <tr key={position.positionId} className="border-t"><td>{position.canonicalSymbol}</td><td>{position.mode}</td><td>{position.direction}</td><td>{position.entry}</td><td>{position.stop}</td><td>{position.target}</td><td>{position.initialRisk}</td><td>{position.mode === 'DEMO' && <Button size="sm" variant="outline" onClick={() => onClose(position.positionId)}>Close demo</Button>}</td></tr>)}
      </tbody></table></div>
      {positions.length === 0 && <div className="text-xs text-muted-foreground">No open Ghost positions.</div>}
    </CardContent></Card>
  );
}
