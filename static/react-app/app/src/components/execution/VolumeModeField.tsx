import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

export type ExecutionVolumeMode = 'min_lot' | 'calculated';

export interface VolumeModeFieldProps {
  volumeMode: ExecutionVolumeMode;
  onVolumeModeChange: (mode: ExecutionVolumeMode) => void;
  sizingOverride?: number;
  onSizingOverrideChange?: (value: number) => void;
  compact?: boolean;
  className?: string;
}

const SIZING_TIERS = [
  { label: 'Full', value: 1.0 },
  { label: 'Half', value: 0.5 },
  { label: 'Quarter', value: 0.25 },
] as const;

export default function VolumeModeField({
  volumeMode,
  onVolumeModeChange,
  sizingOverride = 1.0,
  onSizingOverrideChange,
  compact = false,
  className = '',
}: VolumeModeFieldProps) {
  return (
    <div className={`space-y-2 ${className}`}>
      <Label className="text-[10px] uppercase text-muted-foreground tracking-wide">
        Position size
      </Label>
      <RadioGroup
        value={volumeMode}
        onValueChange={(v) => onVolumeModeChange(v as ExecutionVolumeMode)}
        className={compact ? 'grid gap-2' : 'grid gap-2'}
      >
        <div className="flex items-start gap-2 rounded-md border border-border/50 px-2 py-1.5">
          <RadioGroupItem value="min_lot" id="vol-min-lot" className="mt-0.5" />
          <Label htmlFor="vol-min-lot" className="text-xs font-normal leading-snug cursor-pointer">
            Minimum size
            <span className="block text-[10px] text-muted-foreground">
              Broker minimum for this symbol (e.g. 0.01 lots).
            </span>
          </Label>
        </div>
        <div className="flex items-start gap-2 rounded-md border border-border/50 px-2 py-1.5">
          <RadioGroupItem value="calculated" id="vol-calculated" className="mt-0.5" />
          <Label htmlFor="vol-calculated" className="text-xs font-normal leading-snug cursor-pointer">
            Increase size
            <span className="block text-[10px] text-muted-foreground">
              Risk-based sizing from balance and stop distance.
            </span>
          </Label>
        </div>
      </RadioGroup>
      {volumeMode === 'calculated' && onSizingOverrideChange && (
        <div className="flex items-center gap-2 pl-1">
          <span className="text-[10px] text-muted-foreground">Scale</span>
          <Select
            value={String(sizingOverride)}
            onValueChange={(v) => onSizingOverrideChange(parseFloat(v))}
          >
            <SelectTrigger className="h-7 w-[110px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SIZING_TIERS.map((tier) => (
                <SelectItem key={tier.value} value={String(tier.value)} className="text-xs">
                  {tier.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}
