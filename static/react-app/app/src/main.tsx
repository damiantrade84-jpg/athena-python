import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { initCoreUtils } from '@/lib/safeJson'
import { initApiClient } from '@/lib/apiClient'
import { initGlobalState } from '@/lib/globalState'
import { initExecutionFeature } from '@/lib/executionFeature'
import App from './App.tsx'

// Initialize compatibility globals before React mounts.
// Order matters: core utils → API client → state container → execution.
initGlobalState();
initCoreUtils();
initApiClient();
initExecutionFeature();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
