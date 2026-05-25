import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bell,
  Boxes,
  CheckCircle2,
  Copy,
  Cpu,
  Database,
  KeyRound,
  Lock,
  Mic,
  MicOff,
  MousePointerClick,
  MessageSquare,
  PlaySquare,
  RefreshCw,
  Send,
  Settings,
  ShieldAlert,
  Smartphone,
  Terminal,
  Volume2,
  Wifi,
  Workflow,
  Zap,
} from "lucide-react";

type View = "overview" | "chat" | "flows" | "runs" | "models" | "tools" | "config";

type ModelCandidate = {
  alias: string;
  provider: string;
  model: string;
  base_url?: string;
};

type ModelsPayload = {
  profile: string;
  model_alias: string;
  candidates: ModelCandidate[];
  skipped: string[];
};

type HealthPayload = {
  status: string;
  profile: string;
  profiles: string[];
  repo: string;
  keys: Record<string, "set" | "empty">;
  models: ModelsPayload;
  tools?: ToolInfo[];
  teams?: TeamsInfo;
};

type ChatEntry = {
  role: "operator" | "hermes";
  text: string;
  model?: string;
};

type RunRecord = {
  id?: string;
  status: string;
  exit_code: number | null;
  profile: string;
  workflow: string;
  jsonl_log: string;
  markdown_report: string;
  report_name?: string;
  selected_models?: Array<Record<string, unknown>>;
  command_results: Array<Record<string, unknown>>;
  created_at: string;
};

type ReportPayload = {
  name: string;
  path: string;
  markdown: string;
};

type ToolInfo = {
  name: string;
  enabled: boolean;
  ready: boolean;
  adapter: string;
  description: string;
  risk_level: string;
  requires_adapter: boolean;
  confirmation_policy?: string;
  actions: string[];
  status: string;
};

type ToolResult = {
  status: string;
  tool: string;
  action: string;
  reason?: string;
  confirmation_policy?: string;
  workspace?: string;
  exists?: boolean;
  is_dir?: boolean;
  allowed_paths?: string[];
  entries?: string[];
  result?: unknown;
};

type TeamsInfo = {
  enabled: boolean;
  mode: string;
  endpoint: string;
  profile: string;
  requires_hmac: boolean;
  secret_env: string;
  response_timeout_seconds: number;
};

type MobileInfo = {
  enabled: boolean;
  mode: string;
  path: string;
  token_required: boolean;
  token_header: string;
  authorization: string;
  ntfy: {
    enabled: boolean;
    configured: boolean;
    server: string;
    topic_env: string;
    token_env: string;
  };
  endpoints: Record<string, string>;
};

type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: (() => void) | null;
  start: () => void;
};

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

const API_BASE =
  import.meta.env.VITE_HERMES2_API_URL ||
  (window.location.port === "5173" ? "http://127.0.0.1:8765" : window.location.origin);
const RISK_PATTERNS = ["git push", "rm -rf", "sudo", "chmod -R", "docker system prune", "curl -X POST"];

const navItems: Array<{ view: View; label: string; icon: typeof MessageSquare }> = [
  { view: "chat", label: "Talk", icon: MessageSquare },
  { view: "flows", label: "Workflows", icon: Workflow },
  { view: "runs", label: "History", icon: PlaySquare },
  { view: "models", label: "Models", icon: Boxes },
  { view: "tools", label: "Tools", icon: MousePointerClick },
  { view: "config", label: "Settings", icon: Settings },
];

function isRisky(command: string) {
  const lowered = command.toLowerCase();
  return RISK_PATTERNS.some((pattern) => lowered.includes(pattern.toLowerCase()));
}

function compactModel(model?: string) {
  if (!model) return "unknown";
  return model.length > 27 ? `${model.slice(0, 24)}...` : model;
}

function speechRecognitionCtor(): SpeechRecognitionCtor | undefined {
  const scopedWindow = window as typeof window & {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return scopedWindow.SpeechRecognition || scopedWindow.webkitSpeechRecognition;
}

function App() {
  const [view, setView] = useState<View>("overview");
  const [profile, setProfile] = useState("default");
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [models, setModels] = useState<ModelsPayload | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatLog, setChatLog] = useState<ChatEntry[]>([
    {
      role: "hermes",
      text: "Hermes2 is ready. Ask for help, plan a workflow, or review what changed in the workspace.",
    },
  ]);
  const [workflow, setWorkflow] = useState("default_task");
  const [workflowInput, setWorkflowInput] = useState("Validate Hermes2 status and report the active model chain.");
  const [workspace, setWorkspace] = useState("/Users/jack/Documents/Hermes 2.0");
  const [command, setCommand] = useState("git status --short");
  const [bypass, setBypass] = useState(false);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [report, setReport] = useState<ReportPayload | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [toolResult, setToolResult] = useState<ToolResult | null>(null);
  const [toolApp, setToolApp] = useState("Finder");
  const [mobileInfo, setMobileInfo] = useState<MobileInfo | null>(null);
  const [mobileToken, setMobileToken] = useState(() => localStorage.getItem("hermes2_mobile_token") || "");
  const [voiceState, setVoiceState] = useState("");
  const [voiceMode, setVoiceMode] = useState(false);
  const voiceModeRef = useRef(false);
  const mobileMode = useMemo(() => window.location.pathname.startsWith("/mobile"), []);

  const selectedModel = models?.candidates[0] || health?.models?.candidates[0];
  const skipped = models?.skipped || health?.models?.skipped || [];
  const runtimeLoad = Math.min(92, Math.max(18, ((health?.models?.candidates.length || 1) + skipped.length) * 14.2));
  const risky = command.trim() ? isRisky(command) : false;
  const mobileTokenHeaders = useCallback(
    (jsonBody = false) => {
      const headers: Record<string, string> = {};
      if (jsonBody) headers["Content-Type"] = "application/json";
      if (mobileToken.trim()) {
        headers.Authorization = `Bearer ${mobileToken.trim()}`;
        headers["X-Hermes2-Mobile-Token"] = mobileToken.trim();
      }
      return headers;
    },
    [mobileToken],
  );

  useEffect(() => {
    const token = mobileToken.trim();
    if (token) {
      localStorage.setItem("hermes2_mobile_token", token);
    } else {
      localStorage.removeItem("hermes2_mobile_token");
    }
  }, [mobileToken]);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const mobileRes = await fetch(`${API_BASE}/mobile.json`);
      if (mobileRes.ok) setMobileInfo((await mobileRes.json()) as MobileInfo);

      const healthRes = await fetch(`${API_BASE}/health?profile=${encodeURIComponent(profile)}`, {
        headers: mobileTokenHeaders(),
      });
      if (!healthRes.ok) throw new Error(`health returned ${healthRes.status}`);
      const nextHealth = (await healthRes.json()) as HealthPayload;
      setHealth(nextHealth);
      const nextProfile = nextHealth.profiles.includes(profile) ? profile : nextHealth.profile;
      setProfile(nextProfile);

      const modelsRes = await fetch(`${API_BASE}/models?profile=${encodeURIComponent(nextProfile)}`, {
        headers: mobileTokenHeaders(),
      });
      if (!modelsRes.ok) throw new Error(`models returned ${modelsRes.status}`);
      setModels((await modelsRes.json()) as ModelsPayload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Hermes2 server is offline");
      setHealth(null);
      setModels(null);
    }
  }, [mobileTokenHeaders, profile]);

  const refreshRuns = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/runs?limit=20`, { headers: mobileTokenHeaders() });
      if (!res.ok) throw new Error(`runs returned ${res.status}`);
      const payload = (await res.json()) as { runs: RunRecord[] };
      setRuns(payload.runs);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "failed to load runs");
    }
  }, [mobileTokenHeaders]);

  const refreshTools = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/tools`, { headers: mobileTokenHeaders() });
      if (!res.ok) throw new Error(`tools returned ${res.status}`);
      const payload = (await res.json()) as { tools: ToolInfo[] };
      setTools(payload.tools);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "failed to load tools");
    }
  }, [mobileTokenHeaders]);

  const loadReport = useCallback(async (reportName: string) => {
    if (!reportName) return;
    setError("");
    try {
      const res = await fetch(`${API_BASE}/report?name=${encodeURIComponent(reportName)}`, {
        headers: mobileTokenHeaders(),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.error || `report returned ${res.status}`);
      setReport(payload as ReportPayload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "failed to load report");
    }
  }, [mobileTokenHeaders]);

  useEffect(() => {
    void refresh();
    void refreshRuns();
    void refreshTools();
  }, [refresh, refreshRuns, refreshTools]);

  useEffect(() => {
    setWorkflow(profile === "code" ? "code_build" : "default_task");
  }, [profile]);

  async function sendChatMessage(message: string, autoSpeak: boolean) {
    if (!message) return;
    setBusy(true);
    setError("");
    setChatInput("");
    setChatLog((items) => [...items, { role: "operator", text: message }]);
    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: mobileTokenHeaders(true),
        body: JSON.stringify({ profile, message, max_tokens: 384 }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.error || `chat returned ${res.status}`);
      const responseText: string = payload.response;
      setChatLog((items) => [
        ...items,
        { role: "hermes", text: responseText, model: payload.selected_model?.model },
      ]);
      if (autoSpeak && window.speechSynthesis) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(responseText);
        utterance.rate = 0.96;
        utterance.onend = () => {
          if (voiceModeRef.current) {
            startVoiceInput();
          } else {
            setVoiceState("");
          }
        };
        setVoiceState("Speaking...");
        window.speechSynthesis.speak(utterance);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "chat failed");
      setVoiceState(exc instanceof Error ? exc.message : "chat failed");
    } finally {
      setBusy(false);
    }
  }

  async function sendChat(event: FormEvent) {
    event.preventDefault();
    const message = chatInput.trim();
    if (!message) return;
    await sendChatMessage(message, voiceModeRef.current);
  }

  async function runWorkflow(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const commands = command.trim() ? [command.trim()] : [];
      const res = await fetch(`${API_BASE}/run`, {
        method: "POST",
        headers: mobileTokenHeaders(true),
        body: JSON.stringify({
          profile,
          workflow,
          workspace,
          input: workflowInput,
          commands,
          bypass_approvals: bypass,
        }),
      });
      const payload = await res.json();
      if (!res.ok && !payload.status) throw new Error(payload.error || `run returned ${res.status}`);
      const record: RunRecord = { ...payload, created_at: new Date().toISOString(), report_name: "" };
      setRuns((items) => [record, ...items].slice(0, 12));
      setView("runs");
      void refreshRuns();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "workflow failed");
    } finally {
      setBusy(false);
    }
  }

  async function executeTool(tool: string, action: string, payload: Record<string, unknown> = {}) {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/tools/execute`, {
        method: "POST",
        headers: mobileTokenHeaders(true),
        body: JSON.stringify({ tool, action, payload }),
      });
      const result = await res.json();
      if (!res.ok) throw new Error(result.error || `tool returned ${res.status}`);
      setToolResult(result as ToolResult);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "tool execution failed");
    } finally {
      setBusy(false);
    }
  }

  const keyTiles = useMemo(() => {
    const keys = health?.keys || {};
    return ["GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LMSTUDIO_API_KEY", "HERMES2_MOBILE_TOKEN"].map((key) => ({
      key,
      status: keys[key] || "empty",
    }));
  }, [health]);

  function startVoiceInput() {
    const Recognition = speechRecognitionCtor();
    if (!Recognition) {
      setVoiceState("Use system dictation");
      return;
    }
    const recognition = new Recognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    recognition.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript || "";
      if (transcript) {
        setChatInput(transcript);
        setVoiceState("Captured");
        void sendChatMessage(transcript, true);
      } else {
        setVoiceState("Nothing captured");
      }
    };
    recognition.onerror = () => {
      setVoiceState("Voice capture failed");
    };
    setVoiceState("Listening...");
    recognition.start();
  }

  function toggleVoiceMode() {
    const next = !voiceModeRef.current;
    voiceModeRef.current = next;
    setVoiceMode(next);
    if (next) {
      setVoiceState("Voice mode — speak now");
      startVoiceInput();
    } else {
      window.speechSynthesis?.cancel();
      setVoiceState("");
    }
  }

  function speakLatestHermesMessage() {
    const latest = [...chatLog].reverse().find((entry) => entry.role === "hermes");
    if (!latest || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(latest.text);
    utterance.rate = 0.96;
    window.speechSynthesis.speak(utterance);
  }

  if (mobileMode) {
    return (
      <MobileAppView
        health={health}
        models={models || health?.models || null}
        mobileInfo={mobileInfo}
        error={error}
        busy={busy}
        chatLog={chatLog}
        chatInput={chatInput}
        setChatInput={setChatInput}
        sendChat={sendChat}
        selectedModel={selectedModel}
        mobileToken={mobileToken}
        setMobileToken={setMobileToken}
        voiceState={voiceState}
        voiceMode={voiceMode}
        startVoiceInput={startVoiceInput}
        toggleVoiceMode={toggleVoiceMode}
        speakLatestHermesMessage={speakLatestHermesMessage}
        profile={profile}
        setProfile={setProfile}
        profiles={health?.profiles || ["default", "code", "research"]}
        workflow={workflow}
        setWorkflow={setWorkflow}
        workflowInput={workflowInput}
        setWorkflowInput={setWorkflowInput}
        workspace={workspace}
        setWorkspace={setWorkspace}
        command={command}
        setCommand={setCommand}
        runWorkflow={runWorkflow}
        onRefresh={refresh}
      />
    );
  }

  return (
    <main className="stage">
      <section className="device-shell" aria-label="Hermes2 Desktop">
        <div className="device">
          <aside className="rail">
            <button className="meter-button" onClick={() => setView("overview")} aria-label="Overview">
              <span className="meter-value">H2</span>
            </button>
            <nav className="rail-nav">
              {navItems.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.view}
                    className={`rail-item ${view === item.view ? "active" : ""}`}
                    onClick={() => setView(item.view)}
                    aria-label={item.label}
                    title={item.label}
                  >
                    <Icon size={20} />
                    <span>{item.label}</span>
                  </button>
                );
              })}
            </nav>
            <div className="rail-bottom">
              <Terminal size={18} />
            </div>
          </aside>

          <section className="console">
            <header className="topbar">
              <strong>Hermes 2.0</strong>
              <span className="topbar-status">{health ? "Local runtime ready" : "Connecting"}</span>
              <button className={view === "overview" ? "tab active" : "tab"} onClick={() => setView("overview")}>
                Home
              </button>
              <button className={view === "models" ? "tab active" : "tab"} onClick={() => setView("models")}>
                Routing
              </button>
            </header>

            <section className="content">
              {error && (
                <div className="alert error">
                  <ShieldAlert size={18} />
                  <span>{error}</span>
                </div>
              )}

              {view === "overview" && (
                <OverviewView
                  health={health}
                  selectedModel={selectedModel}
                  runtimeLoad={runtimeLoad}
                  skipped={skipped}
                  keyTiles={keyTiles}
                  onRefresh={refresh}
                />
              )}

              {view === "chat" && (
                <ChatView
                  chatLog={chatLog}
                  chatInput={chatInput}
                  setChatInput={setChatInput}
                  sendChat={sendChat}
                  busy={busy}
                  selectedModel={selectedModel}
                  voiceState={voiceState}
                  voiceMode={voiceMode}
                  startVoiceInput={startVoiceInput}
                  toggleVoiceMode={toggleVoiceMode}
                  speakLatestHermesMessage={speakLatestHermesMessage}
                />
              )}

              {view === "flows" && (
                <FlowsView
                  profile={profile}
                  setProfile={setProfile}
                  profiles={health?.profiles || ["default", "code", "research"]}
                  workflow={workflow}
                  setWorkflow={setWorkflow}
                  workflowInput={workflowInput}
                  setWorkflowInput={setWorkflowInput}
                  workspace={workspace}
                  setWorkspace={setWorkspace}
                  command={command}
                  setCommand={setCommand}
                  bypass={bypass}
                  setBypass={setBypass}
                  risky={risky}
                  busy={busy}
                  runWorkflow={runWorkflow}
                />
              )}

              {view === "runs" && (
                <RunsView
                  runs={runs}
                  report={report}
                  refreshRuns={refreshRuns}
                  loadReport={loadReport}
                />
              )}
              {view === "models" && <ModelsView models={models || health?.models || null} refresh={refresh} />}
              {view === "tools" && (
                <ToolsView
                  tools={tools.length ? tools : health?.tools || []}
                  workspace={workspace}
                  busy={busy}
                  result={toolResult}
                  toolApp={toolApp}
                  setToolApp={setToolApp}
                  refreshTools={refreshTools}
                  executeTool={executeTool}
                />
              )}
              {view === "config" && (
                <ConfigView
                  profile={profile}
                  setProfile={setProfile}
                  profiles={health?.profiles || ["default", "code", "research"]}
                  repo={health?.repo}
                  teams={health?.teams}
                  keyTiles={keyTiles}
                />
              )}
            </section>

            <button className="launcher" onClick={() => setView("flows")}>
              <Terminal size={18} />
              <span>New workflow</span>
            </button>
          </section>
        </div>
      </section>
    </main>
  );
}

function MobileAppView({
  health,
  models,
  mobileInfo,
  error,
  busy,
  chatLog,
  chatInput,
  setChatInput,
  sendChat,
  selectedModel,
  mobileToken,
  setMobileToken,
  voiceState,
  voiceMode,
  startVoiceInput,
  toggleVoiceMode,
  speakLatestHermesMessage,
  profile,
  setProfile,
  profiles,
  workflow,
  setWorkflow,
  workflowInput,
  setWorkflowInput,
  workspace,
  setWorkspace,
  command,
  setCommand,
  runWorkflow,
  onRefresh,
}: {
  health: HealthPayload | null;
  models: ModelsPayload | null;
  mobileInfo: MobileInfo | null;
  error: string;
  busy: boolean;
  chatLog: ChatEntry[];
  chatInput: string;
  setChatInput: (value: string | ((current: string) => string)) => void;
  sendChat: (event: FormEvent) => void;
  selectedModel?: ModelCandidate;
  mobileToken: string;
  setMobileToken: (value: string) => void;
  voiceState: string;
  voiceMode: boolean;
  startVoiceInput: () => void;
  toggleVoiceMode: () => void;
  speakLatestHermesMessage: () => void;
  profile: string;
  setProfile: (value: string) => void;
  profiles: string[];
  workflow: string;
  setWorkflow: (value: string) => void;
  workflowInput: string;
  setWorkflowInput: (value: string) => void;
  workspace: string;
  setWorkspace: (value: string) => void;
  command: string;
  setCommand: (value: string) => void;
  runWorkflow: (event: FormEvent) => void;
  onRefresh: () => void;
}) {
  const tokenActive = Boolean(mobileToken.trim());
  const modelText = selectedModel
    ? `${selectedModel.provider} / ${compactModel(selectedModel.model)}`
    : "offline";
  const quickPrompts = [
    "Give me a short Hermes2 status check.",
    "What should I do next on the Hermes2 repo?",
    "Draft a concise plan for my next build session.",
    "Review the latest workflow results and tell me what matters.",
  ];

  return (
    <main className="mobile-stage">
      <section className="mobile-shell" aria-label="Hermes2 Mobile">
        <header className="mobile-top">
          <div className="mobile-brand">
            <span>H2</span>
            <div>
              <strong>Hermes 2.0</strong>
              <em>{health?.status === "ok" ? "Mac connected" : "Mac offline"}</em>
            </div>
          </div>
          <button className="mobile-icon" onClick={onRefresh} aria-label="Refresh">
            <RefreshCw size={18} />
          </button>
        </header>

        <div className="mobile-status-grid">
          <div className="mobile-chip">
            <Wifi size={16} />
            <span>{modelText}</span>
          </div>
          <div className="mobile-chip">
            <Lock size={16} />
            <span>{mobileInfo?.token_required ? (tokenActive ? "Token set" : "Token needed") : "Private"}</span>
          </div>
          <div className="mobile-chip">
            <Bell size={16} />
            <span>{mobileInfo?.ntfy.enabled ? "Notify on" : "Notify off"}</span>
          </div>
        </div>

        {error && (
          <div className="mobile-alert">
            <ShieldAlert size={18} />
            <span>{error}</span>
          </div>
        )}

        {(mobileInfo?.token_required || tokenActive) && (
          <label className="mobile-token">
            <KeyRound size={18} />
            <input
              value={mobileToken}
              onChange={(event) => setMobileToken(event.target.value)}
              placeholder="Mobile token"
              type="password"
            />
          </label>
        )}

        <section className="mobile-chat">
          {chatLog.map((entry, index) => (
            <article className={`mobile-bubble ${entry.role}`} key={`${entry.role}-${index}`}>
              <span>{entry.role === "operator" ? "You" : "Hermes"}</span>
              <p>{entry.text}</p>
              {entry.model && <em>{compactModel(entry.model)}</em>}
            </article>
          ))}
        </section>

        <div className="quick-grid">
          {quickPrompts.map((prompt) => (
            <button type="button" key={prompt} onClick={() => setChatInput(prompt)}>
              {prompt.split(" ").slice(0, 4).join(" ")}
            </button>
          ))}
        </div>

        <form className="mobile-compose" onSubmit={sendChat}>
          <textarea
            value={chatInput}
            onChange={(event) => setChatInput(event.target.value)}
            placeholder="Talk to Hermes"
            rows={3}
          />
          <div className="voice-row">
            <button
              type="button"
              onClick={toggleVoiceMode}
              aria-label={voiceMode ? "Stop voice mode" : "Start voice mode"}
              className={voiceMode ? "voice-active" : ""}
            >
              {voiceMode ? <MicOff size={18} /> : <Mic size={18} />}
              <span>{voiceState || (voiceMode ? "Listening..." : "Speak")}</span>
            </button>
            <button type="button" onClick={speakLatestHermesMessage} aria-label="Read response">
              <Volume2 size={18} />
              <span>Read</span>
            </button>
            <button className="send-mobile" disabled={busy} aria-label="Send">
              <Send size={18} />
            </button>
          </div>
        </form>

        <form className="mobile-panel" onSubmit={runWorkflow}>
          <div className="mobile-panel-head">
            <Smartphone size={18} />
            <strong>Run From Phone</strong>
          </div>
          <div className="mobile-select-row">
            <select value={profile} onChange={(event) => setProfile(event.target.value)}>
              {profiles.map((item) => <option key={item}>{item}</option>)}
            </select>
            <select value={workflow} onChange={(event) => setWorkflow(event.target.value)}>
              <option value="default_task">default_task</option>
              <option value="code_build">code_build</option>
            </select>
          </div>
          <textarea value={workflowInput} onChange={(event) => setWorkflowInput(event.target.value)} rows={3} />
          <input value={workspace} onChange={(event) => setWorkspace(event.target.value)} />
          <input value={command} onChange={(event) => setCommand(event.target.value)} />
          <button className="primary" disabled={busy}>
            <Zap size={18} />
            <span>{busy ? "Running" : "Run check"}</span>
          </button>
        </form>

        <footer className="mobile-footer">
          <span>{API_BASE}</span>
          <span>{models?.candidates.length || 0} model route(s)</span>
        </footer>
      </section>
    </main>
  );
}

function OverviewView({
  health,
  selectedModel,
  runtimeLoad,
  skipped,
  keyTiles,
  onRefresh,
}: {
  health: HealthPayload | null;
  selectedModel?: ModelCandidate;
  runtimeLoad: number;
  skipped: string[];
  keyTiles: Array<{ key: string; status: string }>;
  onRefresh: () => void;
}) {
  return (
    <div className="stack">
      <section className="telemetry">
        <div className="section-label">Readiness</div>
        <div className="big-number">{runtimeLoad.toFixed(1)}<span>%</span></div>
        <div className="progress"><i style={{ width: `${runtimeLoad}%` }} /></div>
      </section>

      <section className="inspector">
        <div>
          <h1>Workspace Health</h1>
          <p>{health?.status === "ok" ? "Hermes2 is connected" : "Runtime status pending"}</p>
        </div>
        <button className="icon-button" onClick={onRefresh} aria-label="Refresh">
          <RefreshCw size={16} />
        </button>
      </section>

      <section className="uptime">
        <div className="section-label">Active model</div>
        <strong>{selectedModel?.provider || "offline"}</strong>
        <span>{compactModel(selectedModel?.model)}</span>
        <div className="segments"><i /><i /><i /><i /><i /></div>
      </section>

      <section>
        <div className="section-label">Needs attention [{skipped.length}]</div>
        <div className="alerts">
          {skipped.length ? skipped.slice(0, 2).map((item) => <AlertRow key={item} text={item} />) : (
            <div className="alert ok"><CheckCircle2 size={18} /><span>Core runtime checks are passing</span></div>
          )}
        </div>
      </section>

      <section>
        <div className="section-label">Connections</div>
        <div className="tile-grid">
          {keyTiles.map((tile) => (
            <div className="mini-tile" key={tile.key}>
              <span>{tile.key.replace("_API_KEY", "")}</span>
              <strong className={tile.status === "set" ? "good" : "warn"}>{tile.status}</strong>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function AlertRow({ text }: { text: string }) {
  return (
    <div className="alert warn">
      <AlertTriangle size={18} />
      <div>
        <strong>{text.split(":")[0]}</strong>
        <span>{text.split(":").slice(1).join(":").trim() || "configuration required"}</span>
      </div>
    </div>
  );
}

function ChatView({
  chatLog,
  chatInput,
  setChatInput,
  sendChat,
  busy,
  selectedModel,
  voiceState,
  voiceMode,
  startVoiceInput,
  toggleVoiceMode,
  speakLatestHermesMessage,
}: {
  chatLog: ChatEntry[];
  chatInput: string;
  setChatInput: (value: string) => void;
  sendChat: (event: FormEvent) => void;
  busy: boolean;
  selectedModel?: ModelCandidate;
  voiceState: string;
  voiceMode: boolean;
  startVoiceInput: () => void;
  toggleVoiceMode: () => void;
  speakLatestHermesMessage: () => void;
}) {
  const isListening = voiceState === "Listening...";
  return (
    <div className="stack chat-view">
      <div className="model-strip">
        <Activity size={16} />
        <span>{selectedModel?.provider || "offline"} / {compactModel(selectedModel?.model)}</span>
      </div>
      <div className="chat-log">
        {chatLog.map((entry, index) => (
          <article className={`bubble ${entry.role}`} key={`${entry.role}-${index}`}>
            <span>{entry.role === "operator" ? "YOU" : "HERMES2"}</span>
            <p>{entry.text}</p>
            {entry.model && <em>{compactModel(entry.model)}</em>}
          </article>
        ))}
      </div>
      <form className="composer" onSubmit={sendChat}>
        <input
          value={chatInput}
          onChange={(event) => setChatInput(event.target.value)}
          placeholder={voiceMode ? "Speak or type..." : "Ask Hermes2"}
        />
        <button
          type="button"
          onClick={toggleVoiceMode}
          aria-label={voiceMode ? "Stop voice conversation" : "Start voice conversation"}
          title={voiceMode ? "Voice mode on — click to stop" : "Start voice conversation"}
          className={voiceMode ? "voice-active" : ""}
        >
          {voiceMode ? <MicOff size={18} /> : <Mic size={18} />}
        </button>
        <button
          type="button"
          onClick={voiceMode ? startVoiceInput : speakLatestHermesMessage}
          aria-label={voiceMode ? "Listen now" : "Read latest response"}
          title={voiceMode ? "Listen now" : "Read latest response"}
          className={isListening ? "voice-listening" : ""}
        >
          {voiceMode ? <Mic size={18} /> : <Volume2 size={18} />}
        </button>
        <button disabled={busy} aria-label="Send">
          <Send size={18} />
        </button>
      </form>
      {voiceState && (
        <div className={`voice-status${isListening ? " listening" : ""}`}>
          {isListening ? <MicOff size={14} /> : <Mic size={14} />}
          <span>{voiceState}</span>
        </div>
      )}
    </div>
  );
}

function FlowsView(props: {
  profile: string;
  setProfile: (value: string) => void;
  profiles: string[];
  workflow: string;
  setWorkflow: (value: string) => void;
  workflowInput: string;
  setWorkflowInput: (value: string) => void;
  workspace: string;
  setWorkspace: (value: string) => void;
  command: string;
  setCommand: (value: string) => void;
  bypass: boolean;
  setBypass: (value: boolean) => void;
  risky: boolean;
  busy: boolean;
  runWorkflow: (event: FormEvent) => void;
}) {
  return (
    <form className="stack flow-form" onSubmit={props.runWorkflow}>
      <div className="section-label">Workflow</div>
      <div className="control-row">
        <label>
          <span>Profile</span>
          <select value={props.profile} onChange={(event) => props.setProfile(event.target.value)}>
            {props.profiles.map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <label>
          <span>Flow</span>
          <select value={props.workflow} onChange={(event) => props.setWorkflow(event.target.value)}>
            <option value="default_task">default_task</option>
            <option value="code_build">code_build</option>
          </select>
        </label>
      </div>
      <label className="field">
        <span>Task</span>
        <textarea value={props.workflowInput} onChange={(event) => props.setWorkflowInput(event.target.value)} />
      </label>
      <label className="field">
        <span>Workspace</span>
        <input value={props.workspace} onChange={(event) => props.setWorkspace(event.target.value)} />
      </label>
      <label className="field">
        <span>Validation command</span>
        <input value={props.command} onChange={(event) => props.setCommand(event.target.value)} />
      </label>
      <div className={`risk-line ${props.risky ? "hot" : ""}`}>
        <ShieldAlert size={18} />
        <span>{props.risky ? "This command needs explicit approval" : "This command is standard validation"}</span>
      </div>
      <label className="toggle">
        <input type="checkbox" checked={props.bypass} onChange={(event) => props.setBypass(event.target.checked)} />
        <span>I approve risky command bypass</span>
      </label>
      <button className="primary" disabled={props.busy}>
        <Zap size={18} />
        <span>{props.busy ? "Running" : "Run workflow"}</span>
      </button>
    </form>
  );
}

function RunsView({
  runs,
  report,
  refreshRuns,
  loadReport,
}: {
  runs: RunRecord[];
  report: ReportPayload | null;
  refreshRuns: () => void;
  loadReport: (reportName: string) => void;
}) {
  if (!runs.length) {
    return (
      <div className="empty">
        <PlaySquare size={24} />
        <span>No Hermes2 runs yet.</span>
        <button className="primary compact" onClick={refreshRuns} type="button">
          <RefreshCw size={16} />
          <span>Refresh</span>
        </button>
      </div>
    );
  }
  return (
    <div className="stack">
      <div className="inspector">
        <div>
          <h1>Run History</h1>
          <p>{runs.length} saved run artifacts</p>
        </div>
        <button className="icon-button" onClick={refreshRuns} aria-label="Refresh runs"><RefreshCw size={16} /></button>
      </div>
      {runs.map((run) => (
        <article className="run-row" key={`${run.created_at}-${run.jsonl_log}`}>
          <div>
            <strong>{run.workflow}</strong>
            <span>{run.profile} / exit {run.exit_code ?? "n/a"}</span>
          </div>
          <span className={`status ${run.status}`}>{run.status}</span>
          <code>{run.markdown_report}</code>
          <div className="run-actions">
            <button
              className="secondary"
              disabled={!run.report_name}
              onClick={() => run.report_name && loadReport(run.report_name)}
              type="button"
            >
              Report
            </button>
            <button
              className="copy-button"
              onClick={() => void navigator.clipboard.writeText(run.markdown_report || run.jsonl_log)}
              aria-label="Copy report path"
              type="button"
            >
              <Copy size={16} />
            </button>
          </div>
          {Boolean(run.command_results?.length) && (
            <span className="run-meta">{run.command_results.length} command event(s)</span>
          )}
        </article>
      ))}
      {report && (
        <article className="report-panel">
          <div>
            <strong>{report.name}</strong>
            <button
              className="copy-button"
              onClick={() => void navigator.clipboard.writeText(report.path)}
              aria-label="Copy report path"
              type="button"
            >
              <Copy size={16} />
            </button>
          </div>
          <pre>{report.markdown}</pre>
        </article>
      )}
    </div>
  );
}

function ModelsView({ models, refresh }: { models: ModelsPayload | null; refresh: () => void }) {
  return (
    <div className="stack">
      <div className="inspector">
        <div>
          <h1>Model Routing</h1>
          <p>{models?.profile || "offline"} / {models?.model_alias || "local_worker"}</p>
        </div>
        <button className="icon-button" onClick={refresh} aria-label="Refresh models"><RefreshCw size={16} /></button>
      </div>
      {models?.candidates.map((candidate, index) => (
        <article className="model-row" key={candidate.alias}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div>
            <strong>{candidate.alias}</strong>
            <p>{candidate.provider}:{candidate.model}</p>
            {candidate.base_url && <code>{candidate.base_url}</code>}
          </div>
        </article>
      ))}
      <div className="section-label">Skipped</div>
      {models?.skipped.length ? models.skipped.map((item) => <AlertRow key={item} text={item} />) : (
        <div className="alert ok"><CheckCircle2 size={18} /><span>All model candidates are available</span></div>
      )}
    </div>
  );
}

function ToolsView({
  tools,
  workspace,
  busy,
  result,
  toolApp,
  setToolApp,
  refreshTools,
  executeTool,
}: {
  tools: ToolInfo[];
  workspace: string;
  busy: boolean;
  result: ToolResult | null;
  toolApp: string;
  setToolApp: (value: string) => void;
  refreshTools: () => void;
  executeTool: (tool: string, action: string, payload?: Record<string, unknown>) => void;
}) {
  function payloadFor(tool: ToolInfo, action: string) {
    if (tool.name === "filesystem" && action === "workspace_status") {
      return { workspace };
    }
    if (tool.name === "computer_use" && action !== "adapter_tools") {
      return { app: toolApp };
    }
    return {};
  }

  return (
    <div className="stack">
      <div className="inspector">
        <div>
          <h1>Tools and Permissions</h1>
          <p>{tools.length} configured tools</p>
        </div>
        <button className="icon-button" onClick={refreshTools} aria-label="Refresh tools"><RefreshCw size={16} /></button>
      </div>
      <label className="field">
        <span>Target app</span>
        <input value={toolApp} onChange={(event) => setToolApp(event.target.value)} />
      </label>
      {tools.map((tool) => (
        <article className="tool-row" key={tool.name}>
          <div>
            <strong>{tool.name}</strong>
            <span className={`status ${tool.ready ? "completed" : "failed"}`}>{tool.status}</span>
          </div>
          <p>{tool.description}</p>
          <code>{tool.adapter} / {tool.risk_level} risk</code>
          <div className="tool-actions">
            {tool.actions.map((action) => {
              const executable = tool.ready;
              return (
                <button
                  className={executable ? "secondary" : "secondary muted"}
                  key={action}
                  disabled={busy || !executable}
                  onClick={() => executeTool(tool.name, action, payloadFor(tool, action))}
                  type="button"
                >
                  {action}
                </button>
              );
            })}
          </div>
          {tool.confirmation_policy && (
            <span className="run-meta">confirmation: {tool.confirmation_policy}</span>
          )}
        </article>
      ))}
      {result && (
        <article className="report-panel">
          <div>
            <strong>{result.tool}:{result.action}</strong>
            <span className={`status ${result.status === "completed" ? "completed" : "failed"}`}>{result.status}</span>
          </div>
          {result.reason && <p>{result.reason}</p>}
          {result.confirmation_policy && <p>confirmation: {result.confirmation_policy}</p>}
          {result.workspace && <code>{result.workspace}</code>}
          {Boolean(result.entries?.length) && (
            <pre>{result.entries?.join("\n")}</pre>
          )}
          {result.result !== undefined && (
            <pre>{JSON.stringify(result.result, null, 2)}</pre>
          )}
        </article>
      )}
    </div>
  );
}

function ConfigView({
  profile,
  setProfile,
  profiles,
  repo,
  teams,
  keyTiles,
}: {
  profile: string;
  setProfile: (value: string) => void;
  profiles: string[];
  repo?: string;
  teams?: TeamsInfo;
  keyTiles: Array<{ key: string; status: string }>;
}) {
  return (
    <div className="stack">
      <div className="section-label">Settings</div>
      <label className="field">
        <span>Active profile</span>
        <select value={profile} onChange={(event) => setProfile(event.target.value)}>
          {profiles.map((item) => <option key={item}>{item}</option>)}
        </select>
      </label>
      <div className="config-card">
        <Cpu size={18} />
        <div><strong>API base</strong><span>{API_BASE}</span></div>
      </div>
      <div className="config-card">
        <Database size={18} />
        <div><strong>Repository</strong><span>{repo || "/Users/jack/Documents/Hermes 2.0"}</span></div>
      </div>
      {teams && (
        <div className="config-card">
          <MessageSquare size={18} />
          <div>
            <strong>Teams bridge</strong>
            <span>{teams.enabled ? `${teams.mode} at ${teams.endpoint}` : "disabled"}</span>
          </div>
        </div>
      )}
      <div className="tile-grid">
        {keyTiles.map((tile) => (
          <div className="mini-tile" key={tile.key}>
            <span>{tile.key}</span>
            <strong className={tile.status === "set" ? "good" : "warn"}>{tile.status}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

export default App;
