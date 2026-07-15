const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopBridge", {
  backendPort: 18765,
  exportConfig: (config) => ipcRenderer.invoke("export-config", config),
  selectExportDir: () => ipcRenderer.invoke("select-export-dir"),
  getRuntimeInfo: () => ipcRenderer.invoke("get-runtime-info")
});
