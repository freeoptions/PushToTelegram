/// <reference types="vite/client" />

declare global {
  interface ExportConfigResult {
    canceled?: boolean;
    path?: string;
  }

  interface SelectExportDirResult {
    canceled?: boolean;
    path?: string;
  }

  interface RuntimeInfoResult {
    launch_mode: string;
    is_admin: boolean;
    data_dir: string;
  }

  interface Window {
    desktopBridge?: {
      backendPort: number;
      exportConfig?: (config: unknown) => Promise<ExportConfigResult>;
      selectExportDir?: () => Promise<SelectExportDirResult>;
      getRuntimeInfo?: () => Promise<RuntimeInfoResult>;
    };
  }
}

export {};
