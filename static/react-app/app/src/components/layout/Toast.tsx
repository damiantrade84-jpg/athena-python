import { useStore } from '@/hooks/useStore';
import { CheckCircle, XCircle, Info } from 'lucide-react';
import { cn } from '@/lib/utils';

export default function Toast() {
  const { toast } = useStore();

  if (!toast) return null;

  const icons = {
    success: CheckCircle,
    error: XCircle,
    info: Info,
  };

  const colors = {
    success: 'border-long/40 bg-long/10 text-long',
    error: 'border-short/40 bg-short/10 text-short',
    info: 'border-primary/40 bg-primary/10 text-primary',
  };

  const Icon = icons[toast.type];

  return (
    <div className="fixed bottom-4 right-4 z-50 animate-in slide-in-from-bottom-4 fade-in duration-300">
      <div className={cn(
        'flex items-center gap-2.5 px-4 py-3 rounded-lg border backdrop-blur-xl shadow-lg',
        colors[toast.type]
      )}>
        <Icon className="w-4 h-4 shrink-0" />
        <span className="text-sm font-medium">{toast.message}</span>
      </div>
    </div>
  );
}
