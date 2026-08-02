const { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const { spawn, execFileSync } = require("node:child_process");

const APP_NAME = "PushToTelegram";
const APP_FEATURE_DESCRIPTION = "抓取 B站视频并推送到 Telegram";
const TRAY_TOOLTIP = `${APP_NAME} - ${APP_FEATURE_DESCRIPTION}`;
let backendProcess = null;
let mainWindow = null;
let tray = null;
let trayNoticeShown = false;

function pad(value) {
  return String(value).padStart(2, "0");
}

function getAppBaseDir() {
  if (app.isPackaged) {
    return path.dirname(process.execPath);
  }
  if (process.env.PORTABLE_EXECUTABLE_DIR) {
    return process.env.PORTABLE_EXECUTABLE_DIR;
  }
  return app.getAppPath();
}

function getPersistentDataDir() {
  return path.join(getAppBaseDir(), "data");
}

function getRuntimeDataDir() {
  return path.join(app.getPath("userData"), "runtime", "data");
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function syncRootDataToRuntime() {
  const rootDir = getAppBaseDir();
  const runtimeDir = getRuntimeDataDir();
  ensureDir(runtimeDir);

  const files = ["config.json", "history.db", "app.log"];
  for (const fileName of files) {
    const source = path.join(rootDir, fileName);
    const target = path.join(runtimeDir, fileName);
    if (fs.existsSync(source)) {
      fs.copyFileSync(source, target);
      continue;
    }

    const legacySource = path.join(rootDir, "data", fileName);
    if (fs.existsSync(legacySource) && !fs.existsSync(target)) {
      fs.copyFileSync(legacySource, target);
    }
  }
}

function syncRuntimeDataToRoot() {
  const rootDir = getAppBaseDir();
  const runtimeDir = getRuntimeDataDir();
  if (!fs.existsSync(runtimeDir)) {
    return;
  }

  const files = ["config.json", "history.db", "app.log"];
  for (const fileName of files) {
    const source = path.join(runtimeDir, fileName);
    const target = path.join(rootDir, fileName);
    if (fs.existsSync(source)) {
      fs.copyFileSync(source, target);
    }
  }
}

function buildExportFileName() {
  const now = new Date();
  const year = now.getFullYear();
  const month = pad(now.getMonth() + 1);
  const day = pad(now.getDate());
  const hour = pad(now.getHours());
  const minute = pad(now.getMinutes());
  const second = pad(now.getSeconds());
  return `${APP_NAME}_exportConfig_${year}-${month}-${day} ${hour}_${minute}_${second}.json`;
}

function buildExportTimestamp() {
  const now = new Date();
  const year = now.getFullYear();
  const month = pad(now.getMonth() + 1);
  const day = pad(now.getDate());
  const hour = pad(now.getHours());
  const minute = pad(now.getMinutes());
  const second = pad(now.getSeconds());
  return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
}

function normalizeExportPayload(config) {
  const safeConfig = config ?? {};
  const upTargets = Array.isArray(safeConfig.up_targets) ? safeConfig.up_targets : [];
  const sentVideos = Array.isArray(safeConfig.sent_videos) ? safeConfig.sent_videos : [];
  const requestMin = Number(safeConfig.request_interval_seconds_min ?? safeConfig.request_interval_seconds ?? 0);
  const requestMax = Number(safeConfig.request_interval_seconds_max ?? safeConfig.request_interval_seconds ?? requestMin);

  return {
    app_name: APP_NAME,
    export_time: buildExportTimestamp(),
    telegram: {
      bot_token: safeConfig.bot_token ?? "",
      chat_id: safeConfig.chat_id ?? "",
      message_prefix: safeConfig.message_prefix ?? ""
    },
    bilibili: {
      bili_cookie: safeConfig.bili_cookie ?? "",
      use_browser_cookie: Boolean(safeConfig.use_browser_cookie),
      up_targets: upTargets.map((item) => ({
        uid: item?.uid ?? "",
        label: item?.label ?? ""
      }))
    },
    push_settings: {
      auto_check_hours: Number(safeConfig.auto_check_hours ?? 0),
      fetch_count: Number(safeConfig.fetch_count ?? 0),
      first_sync_count: Number(safeConfig.first_sync_count ?? 0),
      request_interval_seconds_min: requestMin,
      request_interval_seconds_max: requestMax,
      message_interval_seconds: Number(safeConfig.message_interval_seconds ?? 0)
    },
    export_settings: {
      export_dir: safeConfig.export_dir ?? ""
    },
    sent_history: {
      total_sent_records: sentVideos.length,
      items: sentVideos.map((item) => ({
        uid: item?.uid ?? "",
        up_name: item?.up_name ?? "",
        bvid: item?.bvid ?? "",
        title: item?.title ?? "",
        video_url: item?.video_url ?? "",
        published_at: Number(item?.published_at ?? 0)
      }))
    }
  };
}

function getBackendCommand() {
  const candidates = [
    path.join(process.resourcesPath, "PushToTelegramBackend.exe"),
    path.join(getAppBaseDir(), "PushToTelegramBackend.exe"),
  ];

  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) {
      return { command: candidate, args: [], cwd: getAppBaseDir() };
    }
  }

  return {
    command: process.platform === "win32" ? "python" : "python3",
    args: [path.join(app.getAppPath(), "backend_api.py")],
    cwd: getAppBaseDir()
  };
}

function killExistingBackendProcesses() {
  if (process.platform !== "win32") {
    return;
  }

  try {
    execFileSync(
      "taskkill.exe",
      ["/F", "/IM", "PushToTelegramBackend.exe", "/T"],
      { windowsHide: true, stdio: "ignore" }
    );
  } catch {
    // Ignore when no old backend process exists.
  }
}

function isAdmin() {
  if (process.platform !== "win32") {
    return false;
  }

  try {
    const output = execFileSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "[bool](([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"
      ],
      {
        windowsHide: true,
        encoding: "utf8"
      }
    );
    return output.trim().toLowerCase() === "true";
  } catch {
    return false;
  }
}

function getLaunchMode() {
  return isAdmin() ? "管理员方式运行" : "普通方式运行";
}

function startBackend() {
  if (backendProcess) {
    return;
  }

  killExistingBackendProcesses();
  syncRootDataToRuntime();
  const { command, args, cwd } = getBackendCommand();
  backendProcess = spawn(command, args, {
    cwd,
    env: {
      ...process.env,
      PORTABLE_EXECUTABLE_DIR: getAppBaseDir(),
      PUSH_TO_BILI_APP_DIR: getAppBaseDir()
    },
    windowsHide: true,
    stdio: "ignore"
  });

  backendProcess.on("exit", () => {
    backendProcess = null;
  });
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1480,
    height: 930,
    minWidth: 1360,
    minHeight: 860,
    backgroundColor: "#f5f9ff",
    title: APP_NAME,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    window.loadURL(devUrl);
    window.webContents.openDevTools({ mode: "detach" });
  } else {
    window.loadFile(path.join(app.getAppPath(), "dist", "index.html"));
  }

  window.maximize();

  window.on("minimize", (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      hideToTray();
    }
  });

  window.on("close", (event) => {
    if (!app.isQuitting) {
      event.preventDefault();
      hideToTray();
    }
  });

  mainWindow = window;
}

function getTrayIconPath() {
  const candidates = [
    path.join(process.resourcesPath, "push_to_bili_icon.ico"),
    path.join(process.resourcesPath, "push_to_bili_qt.ico"),
    path.join(process.resourcesPath, "app.asar.unpacked", "data", "push_to_bili_icon.ico"),
    path.join(process.resourcesPath, "data", "push_to_bili_icon.ico"),
    path.join(app.getAppPath(), "data", "push_to_bili_icon.ico"),
    path.join(process.resourcesPath, "app.asar.unpacked", "data", "push_to_bili_qt.ico"),
    path.join(process.resourcesPath, "data", "push_to_bili_qt.ico"),
    path.join(app.getAppPath(), "data", "push_to_bili_qt.ico"),
    path.join(getAppBaseDir(), "push_to_bili_icon.ico"),
    path.join(getAppBaseDir(), "push_to_bili_qt.ico")
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return null;
}

function showMainWindow() {
  if (!mainWindow) {
    createWindow();
    return;
  }

  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.show();
  if (!mainWindow.isMaximized()) {
    mainWindow.maximize();
  }
  mainWindow.focus();
  trayNoticeShown = false;
}

function toggleMainWindow() {
  if (!mainWindow) {
    createWindow();
    return;
  }

  if (mainWindow.isVisible()) {
    hideToTray();
    return;
  }

  showMainWindow();
}

function hideToTray() {
  if (!mainWindow) {
    return;
  }

  mainWindow.hide();
  if (tray && !trayNoticeShown) {
    tray.displayBalloon?.({
      iconType: "info",
      title: APP_NAME,
      content: "软件已最小化到系统托盘，点击托盘图标可重新打开。"
    });
    trayNoticeShown = true;
  }
}

function createTray() {
  if (tray) {
    return;
  }

  if (!Tray.isSupported()) {
    return;
  }

  const iconPath = getTrayIconPath();
  if (!iconPath) {
    return;
  }

  const trayImage = nativeImage.createFromPath(iconPath);
  if (trayImage.isEmpty()) {
    return;
  }

  tray = new Tray(trayImage);
  tray.setToolTip(TRAY_TOOLTIP);
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "显示软件",
        click: () => showMainWindow()
      },
      {
        label: "退出",
        click: () => {
          app.isQuitting = true;
          app.quit();
        }
      }
    ])
  );
  tray.on("click", () => {
    toggleMainWindow();
  });
}

async function selectExportDir() {
  const target = await dialog.showOpenDialog(mainWindow, {
    title: "选择导出目录",
    properties: ["openDirectory", "createDirectory"]
  });

  if (target.canceled || target.filePaths.length === 0) {
    return { canceled: true };
  }

  return { canceled: false, path: target.filePaths[0] };
}

async function exportConfigFile(config) {
  const exportDir = String(config?.export_dir ?? "").trim();
  if (!exportDir) {
    throw new Error("请先在设置中选择导出目录");
  }

  fs.mkdirSync(exportDir, { recursive: true });
  const targetPath = path.join(exportDir, buildExportFileName());
  const payload = JSON.stringify(normalizeExportPayload(config), null, 2);
  fs.writeFileSync(targetPath, payload, "utf-8");
  return { canceled: false, path: targetPath };
}

function getRuntimeInfo() {
  return {
    launch_mode: getLaunchMode(),
    is_admin: isAdmin(),
    data_dir: getPersistentDataDir()
  };
}

const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) {
  app.quit();
} else {
  app.on("second-instance", () => {
    showMainWindow();
  });

  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);
    startBackend();
    createWindow();
    createTray();

    app.on("activate", () => {
      showMainWindow();
    });
  });
}

ipcMain.handle("export-config", async (_event, config) => exportConfigFile(config));
ipcMain.handle("select-export-dir", async () => selectExportDir());
ipcMain.handle("get-runtime-info", async () => getRuntimeInfo());

app.on("window-all-closed", () => {
  return;
});

app.on("before-quit", () => {
  app.isQuitting = true;
  syncRuntimeDataToRoot();
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
});
