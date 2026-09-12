/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API origin, fixed at build time. Not a secret, but not caller-controlled either. */
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_LOCAL_ORIGINAL_PREVIEW_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
