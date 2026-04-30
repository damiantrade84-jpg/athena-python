import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
}

export default function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  return (
    <div className="flex items-center gap-3 p-3 rounded-md bg-destructive/10 border border-destructive/20">
      <AlertTriangle className="w-4 h-4 text-destructive shrink-0" />
      <Badge variant="destructive" className="text-[10px] shrink-0">Error</Badge>
      <span className="text-xs text-destructive flex-1">{message}</span>
      {onRetry && (
        <Button size="sm" variant="outline" className="h-7 gap-1 text-xs" onClick={onRetry}>
          <RefreshCw className="w-3 h-3" />
          Retry
        </Button>
      )}
    </div>
  );
}
