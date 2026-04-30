import { BrowserRouter, Routes, Route } from 'react-router';
import { StoreProvider } from '@/hooks/useStore';
import Home from './pages/Home';

export default function App() {
  return (
    <BrowserRouter>
      <StoreProvider>
        <Routes>
          <Route path="*" element={<Home />} />
        </Routes>
      </StoreProvider>
    </BrowserRouter>
  );
}
