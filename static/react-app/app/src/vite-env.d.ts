/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_BUILD_ID: string;
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  __ATHENA_FRONTEND__?: {
    buildId?: string;
    bundle?: string;
  };
}
