import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface EngineTagProps {
  engine: string;
  className?: string;
}

const engineColors: Record<string, string> = {
  'A': 'bg-blue-500/20 text-blue-400 border-blue-500/40',
  'B': 'bg-purple-500/20 text-purple-400 border-purple-500/40',
  'C': 'bg-teal-500/20 text-teal-400 border-teal-500/40',
  'D': 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  'Engine A': 'bg-blue-500/20 text-blue-400 border-blue-500/40',
  'Engine B': 'bg-purple-500/20 text-purple-400 border-purple-500/40',
  'Engine C': 'bg-teal-500/20 text-teal-400 border-teal-500/40',
  'Engine D': 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  'engine_a': 'bg-blue-500/20 text-blue-400 border-blue-500/40',
  'engine_b': 'bg-purple-500/20 text-purple-400 border-purple-500/40',
  'engine_c': 'bg-teal-500/20 text-teal-400 border-teal-500/40',
  'engine_d': 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  'scalp': 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  'scalp_vp': 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  'engine d': 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  'lottery': 'bg-pink-500/20 text-pink-400 border-pink-500/40',
};

export default function EngineTag({ engine, className }: EngineTagProps) {
  const colorClass = engineColors[engine] || 'bg-muted text-muted-foreground border-muted-foreground/40';
  return (
    <Badge variant="outline" className={cn('text-[10px]', colorClass, className)}>
      {engine}
    </Badge>
  );
}
