// Thin wrapper over the portal's existing FastAPI routes. The backend is
// unchanged - this rewrite is UI only - so every path here already exists.

export type Agent = {
  name: string;
  id: string | null;
  task: string;
  kind: string;
  kind_label: string;
  kind_hint: string;
  display_name: string;
  blurb: string;
  ready: boolean;
  state: string;
  upload_volume: string;
  output_volume: string;
  tool_approval?: string;
  accepts: string[];
  supports_files: boolean;
  access_reason?: string;
  /** A custom-code agent: the Databricks App it runs as, and its address there. */
  app_name?: string;
  app_url?: string;
  path?: string;
  /** A custom agent run by the portal: which agent and version, its release state, and whether
   *  this is a version still being prepared (shown only to its builders and approvers). */
  custom_id?: string;
  version?: number;
  release?: string;
  preview?: boolean;
  metered?: boolean;
  light?: boolean;
};

export type Session = {
  user_name: string;
  display_name: string;
  groups: string[];
  is_admin: boolean;
  auth_mode: string;
  chat_history: boolean;
  /** The release this portal runs (set by the deployer); empty in a checkout. */
  version?: string;
  /** The client's branding (deployer); empty strings when unbranded. */
  brand?: { name: string; color: string; logo: string };
};

export type Grant = {
  principal: string;
  kind: string;
  level: string;
  inherited: boolean;
};

export type AdminAgent = Agent & {
  grants: Grant[];
  acl_error: string | null;
  manageable: boolean;
  you_can_manage?: boolean;
  agent_id?: string;
};

export type ToolType = { type: string; label: string; confirmed: boolean };
export type SourceItem = { value: string; label: string; detail: string };
export type McpState = "not_deployed" | "deploying" | "running" | "stopped" | "failed";
export type McpEntry = {
  slug: string;
  name: string;
  description: string;
  version: string;
  owner: string;
  app_name: string;
  tools: { name: string; description: string; changes_data: boolean }[];
  needs: { secrets: string[]; volumes: string[] };
  problem: string;
  state: McpState;
  state_note: string;
  url: string;
};
export type BuilderTool = {
  type: string;
  ref: string;
  description: string;
  tool_id?: string;
  readonly?: boolean;
  /** Folder name of a catalog tool the person ticked; the server decides whether it needs deploying. */
  mcp?: string;
};
export type DesignerKind = "supervisor" | "genie" | "knowledge";
/** A tool the designer proposes to create. `fingerprint` is what an approval is tied to. */
export type DesignerNewTool = {
  kind: "uc_function" | "mcp";
  name: string;
  slug?: string;
  description: string;
  sql?: string;
  example?: string;
  code?: string;
  abilities?: { name: string; description: string; changes_data: boolean }[];
  hosts?: string[];
  secrets?: string[];
  volumes?: { volume: string; access: "read" | "write" }[];
  problems: string[];
  fingerprint: string;
  report: {
    kind: "uc_function" | "mcp";
    hosts?: string[];
    settings?: string[];
    folders?: { volume: string; access: string }[];
    reads_files?: boolean;
    saves_files?: boolean;
    calls_internet?: boolean;
    may_change_outside?: boolean;
    packages?: string[];
    lines?: number;
  };
};
export type DesignerToolResult = {
  key: string;
  kind: "uc_function" | "mcp";
  created?: boolean;
  started?: boolean;
  app_name?: string;
  example?: string;
  example_result?: unknown[][] | null;
  example_error?: string;
  error?: string;
};
/** What the interview has worked out so far. Same shape the three builders take. */
export type DesignerDraft = {
  kind?: DesignerKind;
  display_name?: string;
  description?: string;
  instructions?: string;
  tools?: { type: string; ref: string; description: string; mcp?: string }[];
  files?: { upload_volume: string; output_volume: string; accepts: string };
  warehouse_id?: string;
  tables?: string[];
  sample_questions?: string[];
  notes?: string;
  sources?: { volume: string; subfolder: string; name: string; description: string }[];
  access?: { kind: "group" | "user"; principal: string }[];
  access_decided?: boolean;
  chat?: boolean;
  gaps?: string[];
  /** Each thing asked for, checked against what the tools really do. */
  coverage?: { need: string; status: "covered" | "partly" | "missing"; by?: string; note?: string }[];
  /** Tools to create before the assistant is built; the admin reads them first. */
  new_tools?: DesignerNewTool[];
  /** New tools still being written in the background (the page follows each job). */
  pending_tools?: { job: string; slug: string; name: string; description: string }[];
  /** Credentials the designer asked the admin to connect (names and words only). */
  connections?: DesignerConnection[];
  /** The plan: the steps it follows, in the admin's words (approved before anything is built). */
  process?: string[];
  /** Anything still unclear; building is refused while any remain. */
  open_questions?: string[];
};

/** One file of the draft, for the side panel (made from the draft; nothing is stored or run). */
export type DraftFile = {
  path: string;
  kind: "plan" | "instructions" | "tests" | "tool" | "removed";
  text: string;
  change: "new" | "changed" | "same" | "removed";
  diff: string[];
};
/** What was shown for a message; `looked` is only for display and is never sent back. */
/** A file attached while designing (display only; the message text names its path for the model). */
export type DesignerFile = { name: string; path: string; bytes: number; kind: string };
export type DesignerMessage = { role: "user" | "assistant"; content: string; options?: string[]; looked?: string[]; files?: DesignerFile[] };
/** A credential a new tool needs, asked for by the designer by name. The value is entered on screen and never sent to a model. */
export type DesignerConnection = { name: string; label: string; what_for: string; how_to_get: string; kind: "key_file" | "secret_text" };
/** A chat model this admin can use. `fit` says honestly how well it is known to suit the designer. */
export type DesignerModel = {
  name: string;
  label: string;
  kind: "hosted" | "external" | "custom";
  maker: string;
  recommended: boolean;
  /** False = it cannot call functions, which the interview needs. null = not reported. */
  tools: boolean | null;
  /** Real prices from the workspace, in DBUs per million tokens (null when not reported). */
  price_in: number | null;
  price_out: number | null;
  /** "Low cost" / "Moderate cost" / "Higher cost", from the real price. */
  cost: string;
  detail: string;
  fit: string;
  /** What it is good at, e.g. "Balanced: a good default" ("" when unknown). */
  tier?: string;
};
/** The three jobs a model can do. Empty = let the portal choose. */
export type DesignerModels = { chat: string; code: string; judge: string };
export type DesignerInfo = {
  enabled: boolean;
  ready: boolean;
  reason: string;
  models: DesignerModel[];
  defaults: DesignerModels;
};
export type DesignerStep = { key: string; label: string; done: boolean };
export type DesignerTurn = {
  reply: string;
  options: string[];
  multiple: boolean;
  draft: DesignerDraft;
  ready: boolean;
  problems: string[];
  kind_label: string;
  /** The checklist in plain words (what is settled, what is not). */
  progress: DesignerStep[];
  /** What it looked at to answer, e.g. "your tables". */
  looked: string[];
  /** The models actually used, after defaults. */
  models: DesignerModels;
  /** Each needed connection: set in the secret store or not. */
  connections?: Record<string, boolean>;
  /** Said when a model had to be swapped (for example one Databricks has retired). */
  notice: string;
  /** Said when the model answered without looking at anything (so it may miss what you already have). */
  warning: string;
};
export type DesignerTest = { question: string; expect: string; type: "typical" | "edge" | "out_of_scope" };
export type DesignerResult = {
  verdict: "pass" | "fail" | "ungraded";
  reason: string;
  answer: string;
  tools?: string[];
};
/** One call to try a new tool with, and what came back. */
export type ToolTest = { ability: string; arguments: Record<string, unknown>; expect: string; expect_error: boolean };
export type ToolResult = {
  /** pass / fail (crashed, or refused a realistic call) / look (worth a glance) / unchecked (the test said nothing about the tool) */
  verdict: "pass" | "fail" | "look" | "unchecked";
  kind: string;
  reason: string;
  ability: string;
  arguments: Record<string, unknown>;
  expect_error: boolean;
  preview: string;
};
export type ToolPlan = { tests: ToolTest[]; skipped: { ability: string; why: string }[]; notice: string };
/** A repair the code model proposes. Nothing is installed until it has been read and approved. */
export type ToolFix = {
  slug: string;
  name: string;
  code: string;
  what: string;
  diff: string[];
  fingerprint: string;
  problems: string[];
  no_change: boolean;
  notice: string;
};
export type DesignerBuilt = {
  kind: DesignerKind;
  name: string;
  endpoint_name?: string;
  warnings?: string[];
  deploying?: string[];
  access_pending?: number;
  chat?: boolean;
};
export type FileSettings = { upload_volume: string; output_volume: string; accepts: string };
export type BuilderAgent = {
  agent_id: string;
  display_name: string;
  description: string;
  instructions: string;
  endpoint_name: string;
  tools: BuilderTool[];
  files: FileSettings;
  acted_as?: string;
};
export type BuilderSpec = {
  display_name: string;
  description: string;
  instructions: string;
  tools: { type: string; ref: string; description: string; mcp?: string }[];
  files: FileSettings;
  access: { kind: "group" | "user"; principal: string }[];
};

export type GenieSpec = {
  title: string;
  description: string;
  warehouse_id: string;
  tables: string[];
  sample_questions: string[];
  notes: string;
  access: { kind: "group" | "user"; principal: string }[];
  chat: boolean;
};
export type GenieSpace = Omit<GenieSpec, "access" | "chat"> & { space_id: string; etag: string };

export type Activity = {
  enabled: boolean;
  days: number;
  note: string;
  questions: number;
  conversations: number;
  assistants: number;
  files_sent: number;
  files_back: number;
  last_active: string;
  previous?: { questions: number; conversations: number };
  per_day: { day: string; questions: number }[];
  top_assistants: { name: string; label: string; questions: number; last_used: string }[];
};
export type PersonRow = {
  user: string;
  questions: number;
  conversations: number;
  assistants: number;
  last_active: string;
};
export type CostRow = { user: string; est_usd: number; questions: number };
export type FileItem = {
  direction: "sent" | "received";
  name: string;
  path: string;
  endpoint: string;
  label: string;
  at: string;
  conversation_id: string;
};
export type Insights = {
  enabled: boolean;
  days: number;
  note: string;
  /** UTC hour buckets ("2026-10-01T09:00:00Z") with questions asked in each. */
  hours: { hour: string; questions: number }[];
  files: FileItem[];
};
export type PortalEvent = {
  at: string;
  actor: string;
  action: string;
  category: string;
  target: string;
  label: string;
  /** "ok", "error", or "tool_error" (the reply came back but a tool inside it failed). */
  status: string;
  http_status: number;
  ms: number;
  error_kind: string;
  error: string;
  detail: Record<string, any>;
};
export type ActivityFeed = {
  stored: boolean;
  days: number;
  total: number;
  people: number;
  failed: number;
  categories: Record<string, number>;
  events: PortalEvent[];
};
export type AgentHealthRow = {
  endpoint: string;
  label: string;
  questions: number;
  failed: number;
  tool_errors: number;
  failure_rate: number;
  avg_ms: number;
  p90_ms: number;
  people: number;
  last_problem: { at: string; kind: string; error: string } | null;
};
export type HealthReport = {
  stored: boolean;
  days: number;
  questions: number;
  failed: number;
  tool_errors: number;
  avg_ms: number;
  kinds: { kind: string; label: string; count: number }[];
  agents: AgentHealthRow[];
  recent: PortalEvent[];
};
export type OrgOverview = {
  enabled: boolean;
  days: number;
  note: string;
  people: number;
  questions: number;
  conversations: number;
  assistants: number;
  new_people: number;
  previous: { people: number; questions: number };
  per_day: { day: string; questions: number; people: number }[];
  assistants_used: { name: string; label: string; questions: number; people: number; last_used: string }[];
};

export type KnowledgeSource = { volume: string; subfolder: string; name: string; description: string };
export type KnowledgeSpec = {
  display_name: string;
  api_name: string;
  description: string;
  instructions: string;
  sources: KnowledgeSource[];
  access: { kind: "group" | "user"; principal: string }[];
};
export type KnowledgeDetail = {
  ka_id: string;
  api_name: string;
  display_name: string;
  description: string;
  instructions: string;
  state: string;
  endpoint_name: string;
  sources: { name: string; description: string; type: string; path: string }[];
};

export type LogLine = { at: string; level: string; source: string; message: string };
export type LogsResult = { stored: boolean; note: string; lines: LogLine[] };
export type AuditEvent = {
  at: string;
  actor: string;
  category: string;
  message: string;
  ok: boolean;
  status: number;
  error: string;
  kind: string;
  detail: { service: string; action: string; ip: string; params: Record<string, string> };
};

export type SavedChat = { id: string; endpoint: string; updated: string; count: number; title: string };
export type SavedMessage = {
  role: "user" | "assistant";
  text: string;
  tools?: string[];
  citations?: { label: string; url: string }[];
  attachments?: FileRef[];
  files?: string[];
};

/** A file an agent made. `sig` is the portal's permit to download it (see /api/download). */
export type FileRef = { name: string; path: string; sig?: string };

/** One tool call waiting for the person, as the tool itself describes it. */
export type ApprovalItem = {
  id: string;
  tool: string;
  server: string;
  description: string;
  read_only: boolean;
  arguments: Record<string, unknown>;
};
/** An answer paused until the person decides; `state` and `sig` go back unchanged. */
export type Approval = { state: unknown; sig: string; items: ApprovalItem[] };

export type Reply = {
  reply: string;
  tools: string[];
  citations: { label: string; url: string }[];
  attachments: FileRef[];
  approval?: Approval;
  warning?: string;
};

async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
  );
  const text = await res.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!res.ok) throw new Error(data.error || data.detail || `Request failed (${res.status})`);
  return data as T;
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!res.ok) throw new Error(data.error || data.detail || `Request failed (${res.status})`);
  return data as T;
}

/** What a builder sets for a custom agent (one that runs in the portal). */
export type CustomSpec = {
  name: string;
  description: string;
  instructions: string;
  model: string;
  rules: string[];
  documents: string[];
  tools: { app: string; name: string }[];
  data: { space_id: string; title: string }[];
  assistants: { key: string; name: string }[];
  /** A test is one chat: its first message, any follow-ups the person sends, and what a good last answer does. */
  tests: { question: string; expect: string; then?: string[] }[];
  audience: { kind: "group" | "user"; principal: string }[];
  ask_before_changes: boolean;
  /** The approved procedure it follows; saved with the version (sha = which text was approved). */
  runbook: { name: string; text: string; sha?: string } | null;
  /** Per answer: most steps and longest reply (part of what is tested). */
  limits: { steps: number; reply_tokens: number };
  /** What is checked on the way in and out, the admin's own rules, and how much it may be used. */
  policy: SafetyPolicy;
};

export type PolicyRule = { text: string; kind: "phrase" | "rule"; where: "input" | "output" | "both"; action: "block" | "remove" };

export type UsageLimits = { requests: number; tokens: number; cost_usd: number; days: number; per_person_daily: number };

/** A safety policy (safety.py): everything but `usage` is part of what is tested. */
export type SafetyPolicy = {
  name: string;
  input: { attacks: "block" | "warn" | "off"; personal_data: "block" | "mask" | "off"; secrets: "block" | "mask"; unsafe: "block" | "off"; max_chars: number };
  output: { personal_data: "remove" | "block" | "off"; unsafe: "block" | "off" };
  rules: PolicyRule[];
  /** The model that judges unsafe content and plain-English rules ("" = none). */
  checker: string;
  /** When the checker cannot answer: let it through and record it, or hold it back. */
  on_error: "allow" | "block";
  usage: UsageLimits;
};

export type SavedPolicy = { id: string; name: string; settings: SafetyPolicy; by?: string; at?: string };

export type SafetyView = {
  guardrails: {
    known: boolean;
    note?: string;
    endpoint?: string;
    input?: { safety: boolean; pii: string };
    output?: { safety: boolean; pii: string };
    can_change?: boolean;
  };
  policy: SafetyPolicy;
  live_policy: SafetyPolicy | null;
  price: { usd_per_dbu: number | null; note: string };
  residency: Residency;
  ask_before_changes: boolean;
  tests: { total: number; good: number; run: boolean; full: boolean };
};

export type CustomUsage = {
  days: number;
  questions: number;
  tokens_in: number;
  tokens_out: number;
  dbus: number;
  /** DBUs times Databricks' list price for the model's SKU; null when that price cannot be read. */
  usd: number | null;
  price_note: string;
  people: number;
  questions_today: number;
};

export type CustomTestResult = {
  question: string;
  then?: string[];
  expect: string;
  rule: string;
  answer: string;
  tools: string[];
  seconds: number;
  verdict: "pass" | "fail" | "ungraded";
  reason: string;
  accepted?: { by: string; at: string; note: string };
};

export type CustomTestRun = { id: string; version: number; by: string; passed: boolean; at: string; results: CustomTestResult[] };

export type CustomVersion = {
  version: number;
  status: string;
  status_label: string;
  by: string;
  at: string;
  live_since?: string;
  decision?: { by: string; at: string; approved: boolean; note: string };
};

export type CustomSummary = {
  id: string;
  name: string;
  description: string;
  live_version: number | null;
  latest: { version: number; status: string; status_label: string; created_by: string; meta: CustomMeta };
  ready_to_send: boolean;
  next: string;
  versions: CustomVersion[];
};

export type CustomMeta = {
  docs?: {
    state: "reading" | "ready" | "failed" | "none";
    files?: number;
    parts?: number;
    problems?: string[];
    outline?: { file: string; about: string }[];
  };
  test?: { id: string; passed: boolean; total: number; good: number; by: string; at: string; notes?: string[]; full?: boolean; judge?: string };
  submitted?: { by: string; at: string; approvers: string[]; note: string };
  decision?: { by: string; at: string; approved: boolean; note: string };
  live_since?: string;
};

export type CustomDetail = CustomSummary & {
  spec: CustomSpec;
  meta: CustomMeta;
  test: CustomTestRun | null;
  live_spec: CustomSpec | null;
  key: string;
  warnings?: string[];
  what?: string;
  usage?: CustomUsage | null;
};

export type CustomOptions = {
  models: { name: string; label: string; detail: string; recommended: boolean; tools: boolean | null; fit: string }[];
  tools: { app: string; name: string; description: string; running: boolean; starting: boolean; needs: string[]; note: string }[];
  data: { space_id: string; title: string; description: string }[];
  assistants: { key: string; name: string; kind: string }[];
  notes: Record<string, string>;
  /** Where Databricks processes requests to the models, from the workspace's own region. */
  residency?: Residency;
};

export type Residency = { region: string; cloud: string; inside_us_eu: boolean | null; note: string };

export type TestEstimate = { conversations: number; dbus: number; basis: string };

export type TestJob = { state: "running" | "done" | "failed"; done: number; total: number; step: string; seconds: number; error?: string };

export type ApprovalWaiting = { id: string; name: string; version: number; sent_by: string; sent_at: string; note: string; key: string; replaces: number | null };

/** A Databricks App an admin could offer as a custom-code agent. */
export type AppCandidate = {
  name: string;
  description: string;
  running: boolean;
  state: string;
  url: string;
  registered: boolean;
};

/** What an admin sets for a custom-code agent; the app itself is not changed. */
export type AppAgentSpec = {
  app_name: string;
  display_name: string;
  blurb: string;
  path: string;
  upload_volume: string;
  accepts: string;
  output_volume: string;
  share?: { kind: "group" | "user"; principal: string }[];
};

export const api = {
  customAgents: () => request<{ enabled: boolean; reason?: string; agents: CustomSummary[] }>("/api/admin/custom-agents"),
  customOptions: () => request<CustomOptions>("/api/admin/custom-agents/options"),
  customGet: (id: string) => request<CustomDetail>(`/api/admin/custom-agents/${id}`),
  customCreate: (spec: CustomSpec) => request<CustomDetail>("/api/admin/custom-agents", spec),
  customSave: (id: string, spec: CustomSpec) => send<CustomDetail>("PUT", `/api/admin/custom-agents/${id}`, spec),
  customAudience: (id: string, audience: CustomSpec["audience"]) =>
    send<CustomDetail>("PUT", `/api/admin/custom-agents/${id}/audience`, { audience }),
  customTest: (id: string, full = true, judge = "") => request<{ job: string }>(`/api/admin/custom-agents/${id}/tests`, { full, judge }),
  customEstimate: (id: string) =>
    request<{ quick: TestEstimate; full: TestEstimate; judge: string }>(`/api/admin/custom-agents/${id}/tests/estimate`),
  customJob: (job: string) => request<TestJob>(`/api/admin/custom-agents/jobs/${job}`),
  customAccept: (id: string, test: string, index: number, note: string) =>
    request<CustomTestRun>(`/api/admin/custom-agents/${id}/tests/${test}/accept`, { index, note }),
  customSubmit: (id: string, approvers: string[], note: string) =>
    request<CustomDetail>(`/api/admin/custom-agents/${id}/submit`, { approvers, note }),
  customRetire: (id: string) => request<CustomDetail>(`/api/admin/custom-agents/${id}/retire`, {}),
  customDelete: (id: string) => send<{ deleted: string }>("DELETE", `/api/admin/custom-agents/${id}`),
  customDraft: (text: string, model?: string) =>
    request<{ name: string; description: string; instructions: string; rules: string[]; tests: { question: string; expect: string; then?: string[] }[]; notes: string[] }>(
      "/api/admin/custom-agents/draft",
      { text, ...(model ? { models: { chat: model } } : {}) }
    ),
  customSuggestTests: (spec: CustomSpec, model?: string) =>
    request<{ tests: { question: string; expect: string; then?: string[] }[]; notes: string[] }>("/api/admin/custom-agents/suggest-tests", {
      spec,
      ...(model ? { models: { chat: model } } : {}),
    }),
  customSafety: (id: string, approver = false) =>
    request<SafetyView>(approver ? `/api/approvals/${id}/safety` : `/api/admin/custom-agents/${id}/safety`),
  customGuardrails: (model: string, safety: boolean, pii: string) =>
    request<SafetyView["guardrails"]>("/api/admin/custom-agents/guardrails", { model, safety, pii }),
  customUsageLimits: (id: string, usage: UsageLimits) => send<CustomDetail>("PUT", `/api/admin/custom-agents/${id}/usage-limits`, { usage }),
  safetyPolicies: () =>
    request<{ presets: SavedPolicy[]; saved: SavedPolicy[]; can_save: boolean; note: string }>("/api/admin/safety-policies"),
  saveSafetyPolicy: (settings: SafetyPolicy, id = "") => request<SavedPolicy>("/api/admin/safety-policies", { settings, id }),
  deleteSafetyPolicy: (id: string) => send<{ ok: boolean }>("DELETE", `/api/admin/safety-policies/${id}`),
  customRestore: (id: string, version: number) => request<CustomDetail>(`/api/admin/custom-agents/${id}/restore`, { version }),
  customToolStart: (app: string) =>
    request<{ starting: boolean; needs?: DesignerConnection[] }>("/api/admin/custom-agents/tools/start", { app }),
  approvals: () => request<{ items: ApprovalWaiting[] }>("/api/approvals"),
  approvalGet: (id: string) => request<CustomDetail>(`/api/approvals/${id}`),
  approvalDecide: (id: string, approve: boolean, note: string) => request<unknown>(`/api/approvals/${id}`, { approve, note }),
  appAgents: () => request<{ enabled: boolean; reason?: string; agents: Agent[] }>("/api/admin/app-agents"),
  appAgentApps: () => request<{ apps: AppCandidate[] }>("/api/admin/app-agents/apps"),
  appAgentCheck: (name: string) =>
    request<{ found: boolean; running: boolean; state?: string; url?: string; description?: string; message: string }>(
      `/api/admin/app-agents/check?name=${encodeURIComponent(name)}`
    ),
  appAgentTry: (body: { app_name: string; path: string; message?: string }) =>
    request<{ ok: boolean; reply?: string; tools?: string[]; seconds?: number; message?: string }>("/api/admin/app-agents/try", body),
  appAgentAdd: (spec: AppAgentSpec) => request<{ agent: Agent; warnings: string[] }>("/api/admin/app-agents", spec),
  appAgentSave: (name: string, spec: Omit<AppAgentSpec, "app_name" | "share">) =>
    send<{ agent: Agent }>("PUT", `/api/admin/app-agents/${encodeURIComponent(name)}`, spec),
  appAgentRemove: (name: string) => send<{ removed: string }>("DELETE", `/api/admin/app-agents/${encodeURIComponent(name)}`),
  session: () => request<Session>("/api/session"),
  agents: () => request<{ agents: Agent[] }>("/api/agents"),
  chat: (
    endpoint: string,
    history: { role: string; content: string }[],
    files: string[],
    conversation_id?: string,
    file_names?: string[]
  ) => request<Reply>("/api/chat", { endpoint, history, files, conversation_id, file_names }),
  chatDecide: (
    endpoint: string,
    history: { role: string; content: string }[],
    approval: Approval,
    decisions: Record<string, boolean>,
    conversation_id?: string
  ) => request<Reply>("/api/chat/decide", { endpoint, history, approval, decisions, conversation_id }),
  chats: (endpoint: string) =>
    request<{ enabled: boolean; chats: SavedChat[] }>(
      `/api/chats?endpoint=${encodeURIComponent(endpoint)}`
    ),
  chatOpen: (id: string) => request<{ messages: SavedMessage[] }>(`/api/chats/${id}`),
  chatDelete: async (id: string) => {
    const res = await fetch(`/api/chats/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Could not delete that conversation");
  },
  adminOverview: () =>
    request<{
      agents: AdminAgent[];
      groups: { name: string; id: string; local: boolean }[];
      users: { name: string; display: string }[];
    }>("/api/admin/overview"),
  grant: (endpoint_id: string, kind: string, principal: string, level: string | null) =>
    request<{ access_control_list: unknown[]; warning?: string }>("/api/admin/grant", {
      endpoint_id,
      kind,
      principal,
      level,
    }),
  dashMe: (days: number) => request<Activity>(`/api/dashboard/me?days=${days}`),
  dashInsights: (days: number) => request<Insights>(`/api/dashboard/me/insights?days=${days}`),
  dashOrg: (days: number) => request<OrgOverview>(`/api/admin/dashboard/overview?days=${days}`),
  activityFeed: (days: number, category = "", status = "", actor = "") =>
    request<ActivityFeed>(
      `/api/admin/activity?${new URLSearchParams({ days: String(days), category, status, actor }).toString()}`
    ),
  agentHealth: (days: number) => request<HealthReport>(`/api/admin/agent-health?days=${days}`),
  dashPeople: (days: number) =>
    request<{ enabled: boolean; note: string; people: PersonRow[] }>(`/api/admin/dashboard/people?days=${days}`),
  dashPerson: (user: string, days: number) =>
    request<Activity>(`/api/admin/dashboard/person?user=${encodeURIComponent(user)}&days=${days}`),
  dashCosts: (days: number) =>
    request<{
      enabled: boolean;
      available: boolean;
      note: string;
      rows: CostRow[];
      unattributed_usd: number | null;
      total_usd: number | null;
    }>(`/api/admin/dashboard/costs?days=${days}`),
  builderTypes: () => request<{ types: ToolType[] }>("/api/admin/builder/types"),
  builderSources: (kind: string, catalog = "", schema = "") =>
    request<{ items: SourceItem[]; note: string; acted_as: string }>(
      `/api/admin/builder/sources?kind=${encodeURIComponent(kind)}&catalog=${encodeURIComponent(
        catalog
      )}&schema=${encodeURIComponent(schema)}`
    ),
  mcps: (refresh = false) =>
    request<{ repo: string; ref: string; mcps: McpEntry[]; note: string; acted_as: string }>(
      `/api/admin/builder/mcps${refresh ? "?refresh=true" : ""}`
    ),
  /** Start getting ticked tools ready; the wizard then watches `mcps()` until they are. */
  mcpPrepare: (slugs: string[]) =>
    request<{ preparing: string[] }>("/api/admin/builder/mcps/prepare", { slugs }),
  builderPrincipals: () =>
    request<{
      groups: { name: string; id: string; local: boolean }[];
      users: { name: string; display: string }[];
    }>("/api/admin/builder/principals"),
  knowledgeList: () =>
    request<{
      assistants: { ka_id: string; display_name: string; description: string; state: string; endpoint_name: string }[];
    }>("/api/admin/builder/knowledge"),
  knowledgeGet: (id: string) => request<KnowledgeDetail>(`/api/admin/builder/knowledge/${id}`),
  knowledgeCreate: (spec: KnowledgeSpec) =>
    request<{ ka_id: string; endpoint_name: string; acted_as: string; warnings: string[]; access_pending: number }>(
      "/api/admin/builder/knowledge",
      spec
    ),
  knowledgeUpdate: (id: string, body: { display_name: string; description: string; instructions: string }) =>
    send<{ ka_id: string; warnings: string[] }>("PUT", `/api/admin/builder/knowledge/${id}`, body),
  knowledgeDelete: (id: string) => send<{ deleted: string }>("DELETE", `/api/admin/builder/knowledge/${id}`),
  genieList: () =>
    request<{ spaces: { space_id: string; title: string; description: string }[] }>("/api/admin/builder/genie"),
  genieGet: (id: string) => request<GenieSpace>(`/api/admin/builder/genie/${id}`),
  genieCreate: (spec: GenieSpec) =>
    request<{
      space_id: string;
      acted_as: string;
      warnings: string[];
      chat: boolean;
      access_pending: number;
    }>("/api/admin/builder/genie", spec),
  genieUpdate: (id: string, spec: GenieSpec) =>
    send<{ space_id: string }>("PUT", `/api/admin/builder/genie/${id}`, spec),
  genieDelete: (id: string) => send<{ deleted: string }>("DELETE", `/api/admin/builder/genie/${id}`),
  builderAgents: () =>
    request<{
      agents: { agent_id: string; display_name: string; description: string; endpoint_name: string; creator: string }[];
      acted_as: string;
    }>("/api/admin/builder/agents"),
  builderGet: (id: string) => request<BuilderAgent>(`/api/admin/builder/agents/${id}`),
  builderCreate: (spec: BuilderSpec) =>
    request<{
      agent_id: string;
      endpoint_name: string;
      acted_as: string;
      warnings: string[];
      access_pending: number;
      deploying: string[];
    }>(
      "/api/admin/builder/agents",
      spec
    ),
  builderUpdate: (id: string, spec: BuilderSpec) =>
    send<{ agent_id: string; acted_as: string; warnings: string[]; deploying: string[] }>("PUT", `/api/admin/builder/agents/${id}`, spec),
  builderDelete: (id: string) => send<{ deleted: string }>("DELETE", `/api/admin/builder/agents/${id}`),
  designerStatus: () => request<DesignerInfo>("/api/admin/designer/status"),
  designerDraftFiles: (draft: DesignerDraft, previous?: DesignerDraft) =>
    request<{ files: DraftFile[] }>("/api/admin/designer/draft-files", previous ? { draft, previous } : { draft }),
  /** All of the draft's files as one ZIP (a Blob to save). */
  designerDraftZip: async (draft: DesignerDraft): Promise<Blob> => {
    const res = await fetch("/api/admin/designer/draft-files/zip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft }),
    });
    if (!res.ok) throw new Error(`The download failed (${res.status})`);
    return res.blob();
  },
  designerTurn: (messages: DesignerMessage[], draft: DesignerDraft, models: Partial<DesignerModels> = {}) =>
    request<DesignerTurn>("/api/admin/designer/turn", {
      // only what the interview needs: no display-only fields
      messages: messages.map((m) => ({ role: m.role, content: m.content, options: m.options })),
      draft,
      models,
    }),
  designerBuild: (draft: DesignerDraft) => request<DesignerBuilt>("/api/admin/designer/build", { draft }),
  designerToolsCreate: (draft: DesignerDraft, approved: string[]) =>
    request<{ results: DesignerToolResult[] }>("/api/admin/designer/tools/create", { draft, approved }),
  designerToolsStatus: (apps: string[]) =>
    request<{ apps: Record<string, { state: string; note: string }> }>(
      `/api/admin/designer/tools/status?apps=${encodeURIComponent(apps.join(","))}`
    ),
  toolTestPlan: (slug: string, models: Partial<DesignerModels> = {}) =>
    request<ToolPlan>("/api/admin/designer/tool-test/plan", { slug, models }),
  toolTestRun: (slug: string, test: ToolTest, models: Partial<DesignerModels> = {}) =>
    request<ToolResult>("/api/admin/designer/tool-test/run", { slug, test, models }),
  toolTestFix: (slug: string, failures: ToolResult[], models: Partial<DesignerModels> = {}) =>
    request<ToolFix>("/api/admin/designer/tool-test/fix", { slug, failures, models }),
  toolTestApply: (slug: string, code: string, approved: string, what: string) =>
    request<{ started: boolean; app_name: string; since: string }>("/api/admin/designer/tool-test/apply", { slug, code, approved, what }),
  toolTestReady: (slug: string, since: string) =>
    request<{ ready: boolean; failed: boolean; note: string }>(
      `/api/admin/designer/tool-test/ready?slug=${encodeURIComponent(slug)}&since=${encodeURIComponent(since)}`
    ),
  designerTestPlan: (draft: DesignerDraft, models: Partial<DesignerModels> = {}) =>
    request<{ tests: DesignerTest[] }>("/api/admin/designer/test-plan", { draft, models }),
  designerState: (endpoint: string) =>
    request<{ state: "waiting" | "starting" | "ready" }>(
      `/api/admin/designer/assistant-state?endpoint=${encodeURIComponent(endpoint)}`
    ),
  designerTestRun: (endpoint: string, question: string, expect: string, models: Partial<DesignerModels> = {}) =>
    request<DesignerResult>("/api/admin/designer/test-run", { endpoint, question, expect, models }),
  meta: (payload: Record<string, string>) => request<unknown>("/api/admin/meta", payload),
  logs: (days: number) => request<LogsResult>(`/api/admin/logs?days=${days}`),
  /** Databricks' own audit record (system.access.audit), as sentences. */
  audit: (days: number, cats: string) =>
    request<{ available: boolean; note: string; events: AuditEvent[] }>(
      `/api/admin/audit?days=${days}&cats=${encodeURIComponent(cats)}`
    ),
  cost: (days: number) =>
    request<{
      days: number;
      available: boolean;
      note: string;
      total_usd: number | null;
      /** `raw` is the endpoint (or SKU) name the bill is recorded under. */
      lines: { sku: string; raw: string; dbus: number; usd: number | null }[];
    }>(`/api/admin/cost?days=${days}`),
};

async function parsed(res: Response) {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    return { error: text.slice(0, 300) };
  }
}

/** Attach a file while designing. `conflict` is true when a file of that name is already there (nothing was changed). */
export async function designerAttach(
  file: File,
  folder: string,
  onConflict: "ask" | "replace" | "keep_both" = "ask"
): Promise<{ ok: true; file: DesignerFile } | { ok: false; conflict: boolean; error: string }> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("folder", folder);
  fd.append("on_conflict", onConflict);
  const res = await fetch("/api/admin/designer/files", { method: "POST", body: fd });
  const data: any = await parsed(res);
  if (res.ok) return { ok: true, file: { name: data.name, path: data.path, bytes: data.bytes, kind: data.kind } };
  return { ok: false, conflict: res.status === 409, error: data.error || data.detail || `Upload failed (${res.status})` };
}

export async function designerDetach(path: string) {
  await fetch(`/api/admin/designer/files?path=${encodeURIComponent(path)}`, { method: "DELETE" });
}

/** Save a credential. The value goes to the portal and on to the workspace's secret store, nowhere else. */
export async function designerConnect(name: string, value: string, kind: string, replace = false) {
  const res = await fetch(`/api/admin/designer/connections/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value, kind, replace }),
  });
  const data: any = await parsed(res);
  if (!res.ok) throw Object.assign(new Error(data.error || data.detail || `Could not save (${res.status})`), { status: res.status });
  return data as { name: string; set: boolean };
}

/** Which of these connections are set (names only). */
export type DesignerJob = {
  id: string;
  slug: string;
  name: string;
  state: "writing" | "done" | "failed" | "lost";
  step: string;
  seconds: number;
  problems: string[];
  notice?: string;
  item?: DesignerNewTool;
};

/** How a new tool being written in the background is going. "lost" when the portal no longer has it. */
export async function designerJob(id: string): Promise<DesignerJob> {
  const res = await fetch(`/api/admin/designer/jobs/${encodeURIComponent(id)}`);
  const data: any = await parsed(res);
  if (res.status === 404) return { id, slug: "", name: "", state: "lost", step: data.error || data.detail || "", seconds: 0, problems: [] };
  if (!res.ok) throw new Error(data.error || data.detail || `Could not check the tool (${res.status})`);
  return data as DesignerJob;
}

export async function designerConnectionStatus(names: string[]): Promise<Record<string, boolean>> {
  const res = await fetch(`/api/admin/designer/connections?names=${encodeURIComponent(names.join(","))}`);
  const data: any = await parsed(res);
  return res.ok ? data.connections || {} : {};
}

export async function upload(endpoint: string, file: File) {
  const fd = new FormData();
  fd.append("endpoint", endpoint);
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  const text = await res.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!res.ok) throw new Error(data.error || data.detail || "Upload failed");
  return data as { path: string; name: string; bytes: number };
}

// A plain GET link, not a fetch wrapper: letting the browser navigate lets it
// honour the backend's Content-Disposition header and show its own native
// download UI, rather than the app re-implementing a save-file flow.
export function downloadUrl(endpoint: string, path: string, sig?: string) {
  return "/api/download?" + new URLSearchParams({ endpoint, path, ...(sig ? { sig } : {}) }).toString();
}

/** Read a runbook file into text (nothing is stored until the agent is saved). */
export async function customRunbook(file: File): Promise<{ name: string; text: string; chars: number; removed_keys: boolean; searched: boolean }> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/admin/custom-agents/runbook", { method: "POST", body: fd });
  const data: any = await parsed(res);
  if (!res.ok) throw new Error(data.error || data.detail || `Could not read it (${res.status})`);
  return data;
}
