import { BrainCircuit } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { AIReviewProvider } from '@/types/athena';

type ProviderOption = {
  value: AIReviewProvider;
  label: string;
  title: string;
};

const OPTIONS: ProviderOption[] = [
  { value: 'grok', label: 'Grok', title: 'Use Grok for AI review' },
  { value: 'claude', label: 'Claude', title: 'Use Claude for AI review' },
  { value: 'openai', label: 'ChatGPT / GPT-5.5', title: 'Use OpenAI GPT-5.5 for AI review' },
];

export function AIReviewProviderToggle({
  value,
  onChange,
  className,
}: {
  value: AIReviewProvider;
  onChange: (value: AIReviewProvider) => void;
  className?: string;
}) {
  return (
    <div
      className={cn('flex items-center gap-1 rounded-md border border-input bg-background p-0.5', className)}
      aria-label="AI review provider"
    >
      <BrainCircuit className="ml-1 h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
      {OPTIONS.map((option) => (
        <Button
          key={option.value}
          type="button"
          size="sm"
          variant={value === option.value ? 'default' : 'ghost'}
          className="h-7 px-2 text-[11px] whitespace-nowrap"
          onClick={() => onChange(option.value)}
          aria-pressed={value === option.value}
          title={option.title}
        >
          {option.label}
        </Button>
      ))}
    </div>
  );
}
