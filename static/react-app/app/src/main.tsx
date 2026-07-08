import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { initCoreUtils } from '@/lib/safeJson'
import { initApiClient } from '@/lib/apiClient'
import { initGlobalState } from '@/lib/globalState'
import { initLegacyPairBrowserBridge } from '@/lib/legacyPairBrowserBridge'
import { initExecutionFeature } from '@/lib/executionFeature'
import App from './App.tsx'

function initFrontendBuildMeta() {
  const buildId = import.meta.env.VITE_APP_BUILD_ID
  const bundle = import.meta.url.split('/').pop()?.split('?')[0] ?? undefined
  window.__ATHENA_FRONTEND__ = { buildId, bundle }
}

// Initialize compatibility globals before React mounts.
// Order matters: core utils → API client → state container → execution.
initFrontendBuildMeta()
initGlobalState();
initLegacyPairBrowserBridge();
initCoreUtils();
initApiClient();
initExecutionFeature();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
