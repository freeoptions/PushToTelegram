import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCheck,
  Clock3,
  Cookie,
  FolderOpen,
  LoaderCircle,
  Plus,
  RefreshCw,
  Settings2,
  SlidersHorizontal,
  TestTube2,
  Trash2,
  UploadCloud,
  X
} from "lucide-react";

type UpTarget = {
  uid: string;
  label: string;
};

type AppConfig = {
  bot_token: string;
  chat_id: string;
  up_targets: UpTarget[];
  message_prefix: string;
  bili_cookie: string;
  use_browser_cookie: boolean;
  auto_check_hours: number;
  fetch_count: number;
  first_sync_count: number;
  request_interval_seconds_min: number;
  request_interval_seconds_max: number;
  message_interval_seconds: number;
  export_dir: string;
};

type ApiResponse<T = unknown> = {
  ok: boolean;
  message?: string;
  logs?: string[];
  cookie?: string;
  config?: Partial<AppConfig> & { request_interval_seconds?: number };
  result?: T;
};

type ExportStateResult = {
  config: Partial<AppConfig> & { request_interval_seconds?: number };
  sent_videos: Array<{
    uid: string;
    up_name: string;
    bvid: string;
    title: string;
    video_url: string;
    published_at: number;
  }>;
};

const EMPTY_CONFIG: AppConfig = {
  bot_token: "",
  chat_id: "",
  up_targets: [],
  message_prefix: "B站投稿更新提醒",
  bili_cookie: "",
  use_browser_cookie: true,
  auto_check_hours: 0,
  fetch_count: 10,
  first_sync_count: 10,
  request_interval_seconds_min: 15,
  request_interval_seconds_max: 25,
  message_interval_seconds: 1.5,
  export_dir: ""
};

const BACKEND_URL = `http://127.0.0.1:${window.desktopBridge?.backendPort ?? 18765}`;

function normalizeTargets(targets: UpTarget[]) {
  const seen = new Set<string>();
  const result: UpTarget[] = [];
  for (const item of targets) {
    const uid = item.uid.trim();
    const label = item.label.trim();
    if (!/^\d+$/.test(uid) || seen.has(uid)) continue;
    seen.add(uid);
    result.push({ uid, label });
  }
  return result;
}

function normalizeConfig(raw?: Partial<AppConfig> & { request_interval_seconds?: number } | null): AppConfig {
  const legacyValue = Number(raw?.request_interval_seconds ?? 15);
  const minValue = Number(raw?.request_interval_seconds_min ?? legacyValue);
  const maxValue = Number(raw?.request_interval_seconds_max ?? legacyValue);

  return {
    bot_token: raw?.bot_token ?? "",
    chat_id: raw?.chat_id ?? "",
    up_targets: Array.isArray(raw?.up_targets) ? raw.up_targets : [],
    message_prefix: raw?.message_prefix ?? "B站投稿更新提醒",
    bili_cookie: raw?.bili_cookie ?? "",
    use_browser_cookie: raw?.use_browser_cookie ?? true,
    auto_check_hours: Number(raw?.auto_check_hours ?? 0),
    fetch_count: Number(raw?.fetch_count ?? 10),
    first_sync_count: Number(raw?.first_sync_count ?? 10),
    request_interval_seconds_min: Math.max(0, minValue),
    request_interval_seconds_max: Math.max(Math.max(0, minValue), maxValue),
    message_interval_seconds: Number(raw?.message_interval_seconds ?? 1.5),
    export_dir: raw?.export_dir ?? ""
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    headers: {
      "Content-Type": "application/json"
    },
    ...init
  });

  return (await response.json()) as ApiResponse<T>;
}

function metricAuto(hours: number) {
  return hours > 0 ? `每 ${hours} 小时` : "已关闭";
}

function formatSeconds(value: number) {
  return `${value.toFixed(value % 1 === 0 ? 0 : 1)} 秒`;
}

function formatRange(min: number, max: number) {
  if (min === max) {
    return formatSeconds(min);
  }
  return `${formatSeconds(min)} - ${formatSeconds(max)}`;
}

function groupLogs(logs: string[]) {
  const groups: string[][] = [];
  let current: string[] = [];

  for (const line of logs) {
    const isActionStart = line.startsWith("操作：");
    if (isActionStart && current.length > 0) {
      groups.push(current);
      current = [];
    }
    current.push(line);
  }

  if (current.length > 0) {
    groups.push(current);
  }

  return groups.reverse();
}

function statusTone(status: string) {
  if (status.includes("失败")) return "danger";
  if (status.includes("执行") || status.includes("连接")) return "working";
  return "ready";
}

export default function App() {
  const [config, setConfig] = useState<AppConfig>(EMPTY_CONFIG);
  const [targets, setTargets] = useState<UpTarget[]>([]);
  const [draftUid, setDraftUid] = useState("");
  const [draftLabel, setDraftLabel] = useState("");
  const [status, setStatus] = useState("正在连接本地服务...");
  const [logs, setLogs] = useState<string[]>([]);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "info" | "success" | "error"; text: string } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [autoSaveReady, setAutoSaveReady] = useState(false);
  const [saveTick, setSaveTick] = useState(0);
  const drawerRef = useRef<HTMLElement | null>(null);
  const saveSeqRef = useRef(0);

  useEffect(() => {
    let active = true;

    void request("/config")
      .then((payload) => {
        if (!active) return;

        if (payload.ok && payload.config) {
          const nextConfig = normalizeConfig(payload.config);
          setConfig(nextConfig);
          setTargets(nextConfig.up_targets);
          setStatus("本地服务已连接");
          setAutoSaveReady(true);
          return;
        }

        setStatus(payload.message || "读取配置失败");
      })
      .catch((error: Error) => {
        if (active) {
          setStatus(`连接失败：${error.message}`);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => {
      setNotice((current) => (current?.text === notice.text ? null : current));
    }, 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const payload = useMemo<AppConfig>(
    () => ({
      ...config,
      up_targets: normalizeTargets(targets)
    }),
    [config, targets]
  );

  async function persistConfig(nextPayload: AppConfig, successText?: string) {
    const seq = saveSeqRef.current + 1;
    saveSeqRef.current = seq;

    const res = await request("/config/save", {
      method: "POST",
      body: JSON.stringify(nextPayload)
    });

    if (!res.ok || !res.config) {
      throw new Error(res.message || "配置保存失败");
    }

    if (seq !== saveSeqRef.current) {
      return;
    }

    const nextConfig = normalizeConfig(res.config);
    setConfig(nextConfig);
    setTargets(nextConfig.up_targets);
    setSaveTick((value) => value + 1);
    if (successText) {
      setNotice({ tone: "success", text: successText });
    }
  }

  useEffect(() => {
    if (!autoSaveReady) return;

    const timer = window.setTimeout(() => {
      void request("/config/save", {
        method: "POST",
        body: JSON.stringify(payload)
      })
        .then((res) => {
          if (res.ok && res.config) {
            const nextConfig = normalizeConfig(res.config);
            setConfig(nextConfig);
            setTargets(nextConfig.up_targets);
            setSaveTick((value) => value + 1);
            return;
          }
          throw new Error(res.message || "自动保存失败");
        })
        .catch((error: Error) => {
          setNotice({ tone: "error", text: error.message });
        });
    }, 500);

    return () => window.clearTimeout(timer);
  }, [autoSaveReady, payload]);

  useEffect(() => {
    if (!autoSaveReady) return;

    const saveBeforeUnload = () => {
      const body = new Blob([JSON.stringify(payload)], { type: "application/json" });
      navigator.sendBeacon(`${BACKEND_URL}/config/save`, body);
    };

    window.addEventListener("beforeunload", saveBeforeUnload);
    window.addEventListener("pagehide", saveBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", saveBeforeUnload);
      window.removeEventListener("pagehide", saveBeforeUnload);
    };
  }, [autoSaveReady, payload]);

  useEffect(() => {
    if (!settingsOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!drawerRef.current) return;
      if (!drawerRef.current.contains(event.target as Node)) {
        setSettingsOpen(false);
      }
    };
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, [settingsOpen]);

  const targetCount = payload.up_targets.length;
  const cookieMode = config.use_browser_cookie ? "浏览器优先" : "手动优先";
  const autoText = metricAuto(config.auto_check_hours);
  const requestText = formatRange(config.request_interval_seconds_min, config.request_interval_seconds_max);
  const messageText = formatSeconds(config.message_interval_seconds);
  const syncText = busyAction ? "执行中" : "待命";
  const noticeText = notice?.text || "等待任务";
  const exportDirText = config.export_dir || "未设置";
  const logGroups = groupLogs(logs);
  const statusClass = statusTone(status);

  function addTarget() {
    const uid = draftUid.trim();
    const label = draftLabel.trim();
    if (!/^\d+$/.test(uid)) {
      setNotice({ tone: "error", text: "UID 必须是纯数字" });
      return;
    }
    if (targets.some((item) => item.uid === uid)) {
      setNotice({ tone: "error", text: "这个 UID 已经存在" });
      return;
    }
    const nextTargets = [...targets, { uid, label }];
    const nextPayload = {
      ...config,
      up_targets: normalizeTargets(nextTargets)
    };
    setTargets(nextTargets);
    setDraftUid("");
    setDraftLabel("");
    void persistConfig(nextPayload, "UP 主已添加并保存").catch((error: Error) => {
      setNotice({ tone: "error", text: error.message });
    });
  }

  function removeTarget(uid: string) {
    const target = targets.find((item) => item.uid === uid);
    const name = target?.label || uid;
    const confirmed = window.confirm(`确认删除监控目标「${name}」吗？`);
    if (!confirmed) return;
    const nextTargets = targets.filter((item) => item.uid !== uid);
    const nextPayload = {
      ...config,
      up_targets: normalizeTargets(nextTargets)
    };
    setTargets(nextTargets);
    void persistConfig(nextPayload, "UP 主已删除并保存").catch((error: Error) => {
      setNotice({ tone: "error", text: error.message });
    });
  }

  async function chooseExportDir() {
    try {
      const result = await window.desktopBridge?.selectExportDir?.();
      if (!result || result.canceled || !result.path) return;

      const nextPayload: AppConfig = {
        ...payload,
        export_dir: result.path
      };

      const saveResult = await request("/config/save", {
        method: "POST",
        body: JSON.stringify(nextPayload)
      });

      if (!saveResult.ok || !saveResult.config) {
        throw new Error(saveResult.message || "导出目录保存失败");
      }

      const reloadResult = await request("/config");
      if (!reloadResult.ok || !reloadResult.config) {
        throw new Error(reloadResult.message || "导出目录刷新失败");
      }

      const nextConfig = normalizeConfig(reloadResult.config);
      setConfig(nextConfig);
      setTargets(nextConfig.up_targets);
      setSaveTick((value) => value + 1);
      setNotice({ tone: "success", text: "导出目录已更新" });
      setStatus(`操作：设置导出目录，结果：${result.path}`);
    } catch (error) {
      const message = (error as Error).message;
      setLogs([`操作：设置导出目录失败`, `结果：${message}`]);
      setStatus("操作：设置导出目录失败");
      setNotice({ tone: "error", text: "设置导出目录失败" });
    }
  }

  async function exportConfig() {
    try {
      const exportState = await request<ExportStateResult>("/export/state");
      if (!exportState.ok || !exportState.result) {
        throw new Error(exportState.message || "读取导出数据失败");
      }

      const result = await window.desktopBridge?.exportConfig?.({
        ...exportState.result.config,
        sent_videos: exportState.result.sent_videos
      });
      if (!result || result.canceled) return;

      setNotice({ tone: "success", text: "配置已导出到指定位置" });
      setLogs([`操作：导出配置`, `结果：${result.path || "已导出到指定目录"}`]);
      setStatus("操作：导出配置完成");
    } catch (error) {
      const message = (error as Error).message;
      setLogs([`操作：导出配置失败`, `结果：${message}`]);
      setNotice({ tone: "error", text: "导出配置失败" });
      setStatus("操作：导出配置失败");
    }
  }

  async function readCookie() {
    setBusyAction("cookie");
    try {
      const res = await request("/cookie/read", {
        method: "POST",
        body: JSON.stringify({})
      });

      if (!res.ok) {
        throw new Error(res.message || "读取 Cookie 失败");
      }

      setConfig((current) => ({ ...current, bili_cookie: res.cookie || "" }));
      setStatus("浏览器 Cookie 已回填");
      setNotice({ tone: "success", text: "Cookie 读取完成" });
      setLogs([`操作：读取 Cookie`, `结果：读取完成`]);
    } catch (error) {
      const message = (error as Error).message;
      setLogs([`操作：读取 Cookie 失败`, `结果：${message}`]);
      setStatus("操作：读取 Cookie 失败");
      setNotice({ tone: "error", text: "读取 Cookie 失败" });
    } finally {
      setBusyAction(null);
    }
  }

  async function runTask(path: "/task/test" | "/task/check", action: string) {
    setBusyAction(action);
    setStatus("任务执行中");

    try {
      const res = await request(path, {
        method: "POST",
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        throw new Error(res.message || "任务执行失败");
      }

      setLogs(res.logs || []);
      setStatus(res.message || "任务执行完成");
      setNotice({ tone: "success", text: res.message || "执行完成" });
    } catch (error) {
      const message = (error as Error).message;
      const actionLabel = action === "check" ? "立即检查投稿失败" : "测试 Telegram 失败";
      setLogs([`操作：${actionLabel}`, `结果：${message}`]);
      setStatus(`操作：${actionLabel}`);
      setNotice({ tone: "error", text: actionLabel });
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="console-page">
      <div className="app-window">
        <header className="app-topbar">
          <div className="brand-block">
            <div className="brand-mark">B</div>
            <div className="brand-copy">
              <h1>PushToTelegram</h1>
              <span>B 站投稿提醒工具</span>
            </div>
          </div>

          <div className={`top-status status-pill-${statusClass}`}>
            {busyAction ? <LoaderCircle className="spin" size={16} /> : <CheckCheck size={16} />}
            <span>{status}</span>
          </div>

          <div className="topbar-actions">
            <button className="toolbar-btn" onClick={() => void runTask("/task/test", "test")} disabled={!!busyAction}>
              <TestTube2 size={16} />
              测试 Telegram
            </button>
            <button className="toolbar-btn" onClick={() => void readCookie()} disabled={!!busyAction}>
              <Cookie size={16} />
              读取 Cookie
            </button>
            <button className="toolbar-btn" onClick={() => void exportConfig()}>
              <Settings2 size={16} />
              导出配置
            </button>
            <button className="primary-toolbar-btn" onClick={() => void runTask("/task/check", "check")} disabled={!!busyAction}>
              <UploadCloud size={16} />
              立即检查投稿
            </button>
          </div>
        </header>

        <div className="app-body">
          <aside className="side-nav">
            <div className="nav-section-title">工作台</div>
            <div className="nav-item nav-item-active">
              <span className="nav-glyph">总</span>
              <span>总览</span>
            </div>
            <div className="nav-item">
              <span className="nav-glyph">UP</span>
              <span>UP 监控</span>
            </div>
            <div className="nav-item">
              <span className="nav-glyph">T</span>
              <span>Telegram</span>
            </div>
            <div className="nav-item">
              <span className="nav-glyph">C</span>
              <span>Cookie</span>
            </div>
            <div className="nav-section-title">系统</div>
            <div className="nav-item">
              <span className="nav-glyph">志</span>
              <span>运行日志</span>
            </div>
            <button className="nav-item nav-button" onClick={() => setSettingsOpen(true)}>
              <span className="nav-glyph">设</span>
              <span>高级设置</span>
            </button>

            <section className="nav-card">
              <div className="nav-card-title">当前状态</div>
              <span className={`state-chip state-chip-${statusClass}`}>
                {busyAction ? "执行中" : syncText}
              </span>
              <p>已自动保存 {saveTick} 次，当前监控 {targetCount} 个目标。</p>
            </section>
          </aside>

          <main className="workspace">
            <div className="workspace-head">
              <div>
                <h2>专业桌面控制台</h2>
                <p>信息密度更高，但保留足够留白，方便长时间检查任务、配置参数和排查日志。</p>
              </div>
              <div className="workspace-head-actions">
                <button className="toolbar-btn" onClick={() => setSettingsOpen(true)}>
                  <SlidersHorizontal size={16} />
                  管理参数
                </button>
              </div>
            </div>

            <section className="metrics-row" aria-label="监控指标">
              <div className="metric-card">
                <span>监控</span>
                <strong>{targetCount} 个</strong>
              </div>
              <div className="metric-card">
                <span>抓取</span>
                <strong>{config.fetch_count} 条</strong>
              </div>
              <div className="metric-card">
                <span>巡检</span>
                <strong>{autoText}</strong>
              </div>
              <div className="metric-card">
                <span>请求</span>
                <strong>{requestText}</strong>
              </div>
              <div className="metric-card">
                <span>消息</span>
                <strong>{messageText}</strong>
              </div>
              <div className="metric-card">
                <span>Cookie</span>
                <strong>{cookieMode}</strong>
              </div>
            </section>

            <div className="workspace-grid">
              <section className="panel target-panel">
                <div className="panel-head">
                  <div>
                    <h3>UP 主列表</h3>
                    <span>{targetCount} 个监控目标</span>
                  </div>
                  <button className="secondary-btn" onClick={() => addTarget()}>
                    <Plus size={16} />
                    添加
                  </button>
                </div>

                <div className="target-form">
                  <label>
                    <span>UID</span>
                    <input value={draftUid} onChange={(e) => setDraftUid(e.target.value)} placeholder="输入 B 站 UID" />
                  </label>
                  <label>
                    <span>UP 名称</span>
                    <input value={draftLabel} onChange={(e) => setDraftLabel(e.target.value)} placeholder="可选备注名" />
                  </label>
                </div>

                <div className="target-table">
                  <div className="target-table-head">
                    <span>名称</span>
                    <span>UID</span>
                    <span>状态</span>
                    <span>操作</span>
                  </div>
                  {targets.length === 0 ? (
                    <div className="target-empty">暂无监控目标，请先填写 UID 和名称后添加。</div>
                  ) : (
                    targets.map((item, index) => (
                      <div className="target-table-row" key={item.uid}>
                        <div className="target-name-cell">
                          <span className="target-index">{index + 1}</span>
                          <strong>{item.label || "未命名 UP"}</strong>
                        </div>
                        <span className="target-uid">{item.uid}</span>
                        <span className="row-chip">启用</span>
                        <button className="icon-danger-btn" onClick={() => removeTarget(item.uid)} aria-label={`删除 ${item.label || item.uid}`}>
                          <Trash2 size={16} />
                        </button>
                      </div>
                    ))
                  )}
                </div>
              </section>

              <section className="panel log-panel">
                <div className="panel-head">
                  <div>
                    <h3>运行日志</h3>
                    <span>{logGroups.length > 0 ? `${logGroups.length} 组记录` : "暂无记录"}</span>
                  </div>
                  <RefreshCw size={18} />
                </div>

                <div className="log-list">
                  {logGroups.length === 0 ? (
                    <div className="log-empty">暂无日志，执行检查后会在这里显示结果。</div>
                  ) : (
                    logGroups.map((group, groupIndex) => (
                      <div className="log-group-card" key={`group-${groupIndex}`}>
                        {group.map((line, lineIndex) => (
                          <div className="log-entry" key={`${groupIndex}-${lineIndex}-${line}`}>
                            <span className="log-dot" />
                            <span>{line}</span>
                          </div>
                        ))}
                      </div>
                    ))
                  )}
                </div>
              </section>

              <section className="panel settings-strip">
                <div className="panel-head">
                  <div>
                    <h3>参数与节奏</h3>
                    <span>自动保存已开启</span>
                  </div>
                  <button className="primary-compact-btn" onClick={() => setSettingsOpen(true)}>
                    <SlidersHorizontal size={16} />
                    高级设置
                  </button>
                </div>

                <div className="settings-strip-grid">
                  <label>
                    <span>自动检查（小时）</span>
                    <input
                      type="number"
                      min={0}
                      max={24}
                      value={config.auto_check_hours}
                      onChange={(e) => setConfig({ ...config, auto_check_hours: Number(e.target.value) })}
                    />
                  </label>
                  <label>
                    <span>每次抓取</span>
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={config.fetch_count}
                      onChange={(e) => setConfig({ ...config, fetch_count: Number(e.target.value) })}
                    />
                  </label>
                  <label>
                    <span>首轮推送</span>
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={config.first_sync_count}
                      onChange={(e) => setConfig({ ...config, first_sync_count: Number(e.target.value) })}
                    />
                  </label>
                  <label>
                    <span>请求最小间隔</span>
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={config.request_interval_seconds_min}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          request_interval_seconds_min: Number(e.target.value)
                        })
                      }
                    />
                  </label>
                  <label>
                    <span>请求最大间隔</span>
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={config.request_interval_seconds_max}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          request_interval_seconds_max: Number(e.target.value)
                        })
                      }
                    />
                  </label>
                  <label>
                    <span>消息间隔</span>
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={config.message_interval_seconds}
                      onChange={(e) => setConfig({ ...config, message_interval_seconds: Number(e.target.value) })}
                    />
                  </label>
                </div>
              </section>
            </div>
          </main>

          <aside className="right-rail">
            <section className={`status-hero status-hero-${statusClass}`}>
              <div>
                <h2>运行概览</h2>
                <p>{status}</p>
              </div>
              <strong>{targetCount}</strong>
            </section>

            <section className="panel telegram-panel">
              <div className="panel-head">
                <div>
                  <h3>Telegram 推送</h3>
                  <span>{config.bot_token && config.chat_id ? "已填写" : "待配置"}</span>
                </div>
              </div>
              <div className="compact-form">
                <label>
                  <span>Bot Token</span>
                  <input value={config.bot_token} onChange={(e) => setConfig({ ...config, bot_token: e.target.value })} placeholder="Bot Token" />
                </label>
                <label>
                  <span>Chat ID</span>
                  <input value={config.chat_id} onChange={(e) => setConfig({ ...config, chat_id: e.target.value })} placeholder="Chat ID" />
                </label>
                <label className="full-field">
                  <span>消息前缀</span>
                  <input value={config.message_prefix} onChange={(e) => setConfig({ ...config, message_prefix: e.target.value })} placeholder="消息前缀" />
                </label>
              </div>
            </section>

            <section className="panel cookie-panel">
              <div className="panel-head">
                <div>
                  <h3>B 站 Cookie</h3>
                  <span>{cookieMode}</span>
                </div>
                <button
                  className={`toggle-chip ${config.use_browser_cookie ? "toggle-chip-active" : ""}`}
                  onClick={() => setConfig({ ...config, use_browser_cookie: !config.use_browser_cookie })}
                >
                  <Cookie size={14} />
                  {cookieMode}
                </button>
              </div>
              <textarea
                className="cookie-textarea"
                value={config.bili_cookie}
                onChange={(e) => setConfig({ ...config, bili_cookie: e.target.value })}
                placeholder="可手动粘贴 B 站 Cookie，浏览器优先模式会优先读取本机浏览器。"
              />
            </section>

            <section className="panel export-panel">
              <div className="panel-head">
                <div>
                  <h3>导出与反馈</h3>
                  <span>{noticeText}</span>
                </div>
              </div>
              <div className="export-dir-card">
                <span>导出目录</span>
                <strong>{exportDirText}</strong>
              </div>
              <div className="rail-actions">
                <button className="toolbar-btn" onClick={() => void chooseExportDir()}>
                  <FolderOpen size={16} />
                  选择目录
                </button>
                <button className="toolbar-btn" onClick={() => void exportConfig()}>
                  <Settings2 size={16} />
                  导出配置
                </button>
              </div>
            </section>
          </aside>
        </div>
      </div>

      <div className={`settings-overlay ${settingsOpen ? "settings-overlay-open" : ""}`} />
      <aside ref={drawerRef} className={`settings-drawer ${settingsOpen ? "settings-drawer-open" : ""}`}>
        <div className="drawer-head">
          <h2>高级设置</h2>
          <button className="drawer-close" onClick={() => setSettingsOpen(false)} aria-label="关闭设置">
            <X size={18} />
          </button>
        </div>

        <div className="drawer-body">
          <section className="panel drawer-panel">
            <div className="panel-head">
              <div>
                <h3>导出目录</h3>
                <span>配置文件会导出到这里</span>
              </div>
              <FolderOpen size={18} />
            </div>

            <div className="export-dir-card">
              <span>当前目录</span>
              <strong>{exportDirText}</strong>
            </div>

            <div className="drawer-actions">
              <button className="secondary-btn" onClick={() => void chooseExportDir()}>
                <FolderOpen size={18} />
                选择目录
              </button>
            </div>
          </section>

          <section className="panel drawer-panel">
            <div className="panel-head">
              <div>
                <h3>抓取节奏</h3>
                <span>控制检查频率和消息发送节奏</span>
              </div>
              <Clock3 size={18} />
            </div>

            <div className="form-grid drawer-form-grid">
              <label>
                <span>自动检查间隔（小时）</span>
                <input
                  type="number"
                  min={0}
                  max={24}
                  value={config.auto_check_hours}
                  onChange={(e) => setConfig({ ...config, auto_check_hours: Number(e.target.value) })}
                />
              </label>
              <label>
                <span>每次抓取条数</span>
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={config.fetch_count}
                  onChange={(e) => setConfig({ ...config, fetch_count: Number(e.target.value) })}
                />
              </label>
              <label>
                <span>首轮推送条数</span>
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={config.first_sync_count}
                  onChange={(e) => setConfig({ ...config, first_sync_count: Number(e.target.value) })}
                />
              </label>
              <label>
                <span>请求最小间隔（秒）</span>
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={config.request_interval_seconds_min}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      request_interval_seconds_min: Number(e.target.value)
                    })
                  }
                />
              </label>
              <label>
                <span>请求最大间隔（秒）</span>
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={config.request_interval_seconds_max}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      request_interval_seconds_max: Number(e.target.value)
                    })
                  }
                />
              </label>
              <label>
                <span>消息发送间隔（秒）</span>
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={config.message_interval_seconds}
                  onChange={(e) => setConfig({ ...config, message_interval_seconds: Number(e.target.value) })}
                />
              </label>
            </div>
          </section>
        </div>
      </aside>

      {notice ? (
        <div className={`toast toast-${notice.tone}`}>
          <span>{notice.text}</span>
        </div>
      ) : null}
    </div>
  );
}
