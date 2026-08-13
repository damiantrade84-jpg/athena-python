import { describe, expect, it } from 'vitest';
import { layoutTradeLabelRows, tradeBands, type TradeLevel } from '../engineATradePrimitive';

describe('layoutTradeLabelRows', () => {
  it('leaves a tag on its own level when nothing overlaps', () => {
    const rows = [
      { id: 'tp2', y: 60 },
      { id: 'tp1', y: 100 },
      { id: 'entry', y: 170 },
      { id: 'sl', y: 250 },
    ];
    for (const row of layoutTradeLabelRows(rows)) {
      expect(row.layoutY).toBe(row.y);
    }
  });

  it('pushes only far enough to stop touching, unlike the fixed-gap chip stacker', () => {
    // 5px apart with a 13px tag: the lower tag must move, but only to 13px.
    const placed = layoutTradeLabelRows([
      { id: 'tp2', y: 100 },
      { id: 'tp1', y: 105 },
    ]);
    const tp2 = placed.find((row) => row.id === 'tp2')!;
    const tp1 = placed.find((row) => row.id === 'tp1')!;
    expect(tp2.layoutY).toBe(100);
    expect(tp1.layoutY).toBe(113);
    expect(tp1.layoutY - tp2.layoutY).toBe(13);
  });

  it('never clamps to a pane edge, so an off-pane level is not faked onto it', () => {
    const placed = layoutTradeLabelRows([{ id: 'sl', y: 940 }]);
    expect(placed[0].layoutY).toBe(940);
  });

  it('keeps the true y alongside the drawn y so a leader can be drawn', () => {
    const placed = layoutTradeLabelRows([
      { id: 'a', y: 100 },
      { id: 'b', y: 102 },
    ]);
    const b = placed.find((row) => row.id === 'b')!;
    expect(b.y).toBe(102);
    expect(b.layoutY).toBeGreaterThan(b.y);
  });
});

describe('tradeBands', () => {
  const levels: TradeLevel[] = [
    { role: 'entry', label: 'Entry', price: 3053.3 },
    { role: 'stop', label: 'SL', price: 3046.9 },
    { role: 'target', label: 'TP1', price: 3059.7 },
    { role: 'target', label: 'TP2', price: 3062.9 },
  ];

  it('spans entry to stop and entry to the furthest target', () => {
    const bands = tradeBands(levels);
    expect(bands).toEqual([
      { role: 'stop', from: 3053.3, to: 3046.9 },
      { role: 'target', from: 3053.3, to: 3062.9 },
    ]);
  });

  it('measures the furthest target by distance, so a short works too', () => {
    const short: TradeLevel[] = [
      { role: 'entry', label: 'Entry', price: 3053.3 },
      { role: 'target', label: 'TP1', price: 3046.0 },
      { role: 'target', label: 'TP2', price: 3040.0 },
    ];
    expect(tradeBands(short)).toEqual([{ role: 'target', from: 3053.3, to: 3040.0 }]);
  });

  it('draws nothing without an entry to anchor the bands', () => {
    expect(tradeBands([{ role: 'target', label: 'TP1', price: 3059.7 }])).toEqual([]);
  });

  it('omits the risk band when the signal carries no stop', () => {
    const noStop = levels.filter((level) => level.role !== 'stop');
    expect(tradeBands(noStop).map((band) => band.role)).toEqual(['target']);
  });
});
