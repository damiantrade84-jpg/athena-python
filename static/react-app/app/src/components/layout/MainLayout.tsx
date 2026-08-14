import Header from './Header';
import Sidebar from './Sidebar';
import Toast from './Toast';
import ASEFillWatcher from '@/components/athena/ASEFillWatcher';
import SuggestedTradeAlertWatcher from '@/components/athena/SuggestedTradeAlertWatcher';
import { ScrollArea } from '@/components/ui/scroll-area';

export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden app-shell-bg">
      <Header />
      <SuggestedTradeAlertWatcher />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-hidden">
          <ScrollArea className="h-full">
            <div className="p-5">
              {children}
            </div>
          </ScrollArea>
        </main>
      </div>
      <Toast />
      <ASEFillWatcher />
    </div>
  );
}
