import { cn } from '@/lib/utils';

interface EngineTagProps {
  engine: string;
  className?: string;
}

/**
 * Engine identity is categorical, so it draws from the chart ramp in fixed
 * order rather than from ad-hoc Tailwind hues. A 6px dot carries the hue and
 * the label stays neutral ink — a full colour-filled pill per engine was
 * competing with the direction chip sitting next to it.
 */
const ENGINE_HUE: Record<string, string> = {
  a: 'var(--chart-1)',
  b: 'var(--chart-3)',
  c: 'var(--chart-5)',
  d: 'var(--chart-2)',
  scalp: 'var(--chart-2)',
  lottery: 'var(--chart-4)',
  ox_alpha: 'var(--chart-5)',
  oxalpha: 'var(--chart-5)',
};

function hueFor(engine: string): string | null {
  const k = engine.trim().toLowerCase().replace(/^engine[_\s]*/, '');
  if (ENGINE_HUE[k]) return ENGINE_HUE[k];
  if (k.startsWith('scalp')) return ENGINE_HUE.scalp;
  return null;
}

export default function EngineTag({ engine, className }: EngineTagProps) {
  const hue = hueFor(engine);
  return (
    <span className={cn('chip', className)}>
      {hue && (
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: `hsl(${hue})` }}
        />
      )}
      {engine}
    </span>
  );
}
