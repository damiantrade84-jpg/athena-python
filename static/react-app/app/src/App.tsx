import { BrowserRouter, Routes, Route } from 'react-router';
import { StoreProvider } from '@/hooks/useStore';
import ErrorBoundary from '@/components/ErrorBoundary';
import Home from './pages/Home';

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <StoreProvider>
          <Routes>
            <Route path="*" element={<Home />} />
          </Routes>
        </StoreProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
