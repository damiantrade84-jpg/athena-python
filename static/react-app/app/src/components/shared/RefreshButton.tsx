import { Button } from '@/components/ui/button';
import { RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';

interface RefreshButtonProps {
  onClick: () => void;
  loading?: boolean;
  className?: string;
  size?: 'sm' | 'default' | 'lg';
}

export default function RefreshButton({ onClick, loading, className, size = 'sm' }: RefreshButtonProps) {
  return (
    <Button
      size={size}
      variant="outline"
      className={cn('gap-1 text-xs', className)}
      onClick={onClick}
      disabled={loading}
    >
      <RefreshCw className={cn('w-3 h-3', loading && 'animate-spin')} />
      Refresh
    </Button>
  );
}
