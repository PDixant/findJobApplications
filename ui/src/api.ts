// The window's only way to reach your data: the local server started by app.py.
// app.py opens the window at /#token=..., and every request carries that token.

const TOKEN_KEY = "jw.token";

function readToken(): string {
  const match = window.location.hash.match(/token=([^&]+)/);
  if (match) {
    const token = decodeURIComponent(match[1]);
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable; the token stays in memory */
    }
    history.replaceState(null, "", window.location.pathname + "#/dashboard");
    return token;
  }
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export const TOKEN = readToken();

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    ...init,
    headers: { "X-Token": TOKEN, "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!response.ok) {
    let message = `The app's server answered ${response.status}.`;
    try {
      const body = await response.json();
      if (body?.detail) message = String(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export function eventsUrl(): string {
  return `/api/events?token=${encodeURIComponent(TOKEN)}`;
}

// ------------------------------------------------------------------ shapes

export type Region = { name: string; color: string | null };

export type Meta = {
  folder: string;
  statuses: string[];
  outreach: string[];
  regions: Region[];
  colors: {
    status: Record<string, [string, string]>;
    outreach: Record<string, string>;
    level: Record<string, string | null>;
    tier: Record<string, string>;
  };
  run_modes: Record<string, string>;
  settings_problem: string | null;
};

export type RunMode = "full" | "refilter" | "since";

export type Run = {
  id: string;
  mode: RunMode;
  label: string;
  status: "running" | "finished" | "cancelled" | "failed" | "skipped";
  phase: string;
  started: string;
  finished: string | null;
  done: number;
  total: number | null;
  last_company: string;
  failed_boards: number;
  new: string[];
  rate_limited: string[];
  requests?: Record<string, number>;
  error: string;
  cancelled: boolean;
};

export type Summary = {
  last_run: string | null;
  last_check: string | null;
  jobs: { tracked: number; open: number; new_24h: number };
  companies: { listed: number; enabled: number; results: Record<string, number> };
  postings: { total: number; open: number };
  usage: {
    month: string;
    search: { provider: string; used: number; limit: number; key_set: boolean };
    hunter: { used: number; limit: number; key_set: boolean };
  };
  schedule: { runs_per_day: number | null };
  run: Run | null;
  busy_elsewhere: boolean;
  edits_in: "app" | "excel";
};

export type Job = {
  key: string;
  company: string;
  title: string;
  url: string;
  locations: string[];
  region: string;
  level: string;
  stack: string[];
  provider: string;
  found: string | null;
  posted: string | null;
  open: boolean;
  closed: string | null;
  status: string;
  notes: string;
  outreach: string;
  score: string;
  tier: string;
};

export type Company = {
  priority: string;
  region: string;
  company: string;
  score: string;
  tier: string;
  type: string;
  ticker: string;
  stage: string;
  board: string;
  slug: string;
  careers_url: string;
  domain: string;
  ats: string;
  hubs: string;
  status: string;
  verified: string;
  market_cap_b: string;
  stock_12m_pct: string;
  revenue_growth_pct: string;
  notes: string;
  enabled: boolean;
  pending: boolean;
  from_config: boolean;
  result: string;
  why: string;
  checked: string | null;
  found_on: string;
  found_slug: string;
  open: number | null;
  matches: number;
};

export type Posting = {
  key: string;
  company: string;
  provider: string;
  title: string;
  locations: string[];
  url: string;
  posted: string | null;
  first_seen: string | null;
  open: boolean;
  closed: string | null;
  tracked: boolean;
};

export type PostingPage = { total: number; page: number; size: number; rows: Posting[] };

// What each company result means, in plain words, with its colour on the board map.
export const RESULTS: Record<string, { label: string; tone: string; explain: string }> = {
  OK: { label: "Read", tone: "ok", explain: "Its job board was read this run." },
  RETRY: {
    label: "Paused",
    tone: "paused",
    explain: "A site asked for a pause (rate limit) or the run was cancelled. Read again next run.",
  },
  "NO JOBS READ": {
    label: "Nothing readable",
    tone: "unreadable",
    explain: "Its careers page loaded but listed no jobs the app could read.",
  },
  "NOT FOUND": { label: "No board found", tone: "missing", explain: "No job board or careers page found yet." },
  ERROR: { label: "Error", tone: "error", explain: "Reading its board failed this run." },
  PENDING: { label: "Waiting", tone: "paused", explain: "Board detection is spread over several runs." },
  MANUAL: { label: "Check by hand", tone: "manual", explain: "Uses its own careers site; check it directly." },
  OFF: { label: "Turned off", tone: "off", explain: "Disabled in companies.csv." },
  "": { label: "Not checked yet", tone: "off", explain: "Not read yet. It will be on the next run." },
};

export const RESULT_ORDER = ["OK", "RETRY", "NO JOBS READ", "NOT FOUND", "ERROR", "PENDING", "MANUAL", "OFF", ""];

export function resultInfo(result: string) {
  return RESULTS[result] ?? { label: result, tone: "off", explain: "" };
}

// ------------------------------------------------------------------ editing

export type JobEdit = Partial<Pick<Job, "status" | "notes" | "outreach">>;

export type FieldKind = string; // list, number, bool, text, regions, choice:a,b, choices:a,b
export type RegionValue = { name: string; color: string; match: string[] };
export type SettingField = { key: string; kind: FieldKind; help: string; value: unknown };
export type SettingsSection = { id: string; title: string; fields: SettingField[] };
export type SettingsData = { sections: SettingsSection[]; explain: string };
export type Changes = Record<string, Record<string, unknown>>;

export type KeyStatus = { name: string; label: string; set: boolean; source: string | null; preview: string };
export type KeysData = { store: boolean; search_provider: string; keys: KeyStatus[] };

export type ImportPreview = {
  id: string;
  filename: string;
  rows: number;
  added: Pick<Company, "company" | "region" | "board" | "slug" | "careers_url" | "notes">[];
  updated: Pick<Company, "company" | "region" | "board" | "slug" | "careers_url" | "notes">[];
};

export const send = <T,>(path: string, method: string, body?: unknown) =>
  api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

export async function upload<T>(path: string, file: File): Promise<T> {
  return api<T>(path, { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } });
}
