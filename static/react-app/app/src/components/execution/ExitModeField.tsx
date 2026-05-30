import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import type { ExitModeSelection } from '@/lib/manualExecuteHelpers';

export interface ExitModeFieldProps {
  exitMode: ExitModeSelection;
  onExitModeChange: (mode: ExitModeSelection) => void;
  compact?: boolean;
  className?: string;
}

const OPTIONS: { value: ExitModeSelection; label: string; hint: string }[] = [
  { value: 'default', label: 'Use default', hint: 'Resolve from the group / global setting.' },
  { value: 'traditional_static', label: 'Traditional (static)', hint: 'Fixed broker SL + TP. No trail.' },
  { value: 'adaptive_trail', label: 'Adaptive trail', hint: 'Chandelier trail + profit-protect.' },
  { value: 'manual', label: 'Manual', hint: 'Uses the SL/TP you entered. No clamp.' },
  { value: 'time_based', label: 'Time-based', hint: 'Closes after the group-configured bars.' },
];

export default function ExitModeField({
  exitMode,
  onExitModeChange,
  compact = false,
  className = '',
}: ExitModeFieldProps) {
  return (
    <div className={`space-y-2 ${className}`}>
      <Label className="text-[10px] uppercase text-muted-foreground tracking-wide">
        Exit strategy
      </Label>
      <RadioGroup
        value={exitMode}
        onValueChange={(v) => onExitModeChange(v as ExitModeSelection)}
        className="grid gap-1.5"
      >
        {OPTIONS.map((opt) => (
          <div
            key={opt.value}
            className="flex items-start gap-2 rounded-md border border-border/50 px-2 py-1.5"
          >
            <RadioGroupItem value={opt.value} id={`exit-${opt.value}`} className="mt-0.5" />
            <Label
              htmlFor={`exit-${opt.value}`}
              className="text-xs font-normal leading-snug cursor-pointer"
            >
              {opt.label}
              {!compact && (
                <span className="block text-[10px] text-muted-foreground">{opt.hint}</span>
              )}
            </Label>
          </div>
        ))}
      </RadioGroup>
    </div>
  );
}
