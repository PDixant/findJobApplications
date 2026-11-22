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

export type RunMode = "full" | "refilter" | "since" | "search" | "career_pages" | "check" | "scores";

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
  applied: string | null;
  pay: Pay | null;
  work_auth: { id: string; label: string }[] | null;
  workplace: "remote" | "hybrid" | "on-site" | null;
  relocation: boolean | null;
  reposted?: boolean;
  open_days?: number | null;
  long_open?: boolean;
  connections: number;
  fit: { verdict: Verdict; score: number; summary: string } | null;
  tailored: boolean;
};

export type Pay = { min: number; max: number; currency: string; period: "year" | "hour"; text: string };
export type Verdict = "APPLY" | "STRETCH" | "SKIP";

export type Fit = {
  verdict: Verdict; score: number; summary: string; strengths: string[]; gaps: string[];
  requirements: { text: string; status: "MET" | "PARTIAL" | "MISSING" }[]; at: string; model: string;
};

export type Tailored = {
  tex: string; pdf: string | null; compiler: string | null; problem: string | null; verdict: Verdict | null;
  numbers_to_find: string[]; notes: string; at: string;
};

export type Connection = { first: string; last: string; url: string; position: string; connected: string };

export type JobDetailsData = { key: string; fit: Fit | null; tailored: Tailored | null; people: Connection[] };

export type AiStatus = {
  key_set: boolean; used: number; limit: number; model: string; score_new_matches: boolean;
  resume_tex: string; resume_tex_found: boolean; latex: string | null; rules_found: boolean;
  unscored: number; scoring: boolean;
};

export type FollowUp = { key: string; company: string; title: string; kind: "application" | "email"; days: number };

export type Pipeline = {
  by_status: Record<string, number>; applied_total: number; applied_weeks: { week: string; applied: number }[];
  heard_back: number; response_rate: number | null; fit: Record<Verdict, number>;
  follow_ups: FollowUp[]; follow_up_after_days: number;
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

// ------------------------------------------------------------------ Phases 3 and 4

export type Person = {
  name: string; position: string; email: string; kind: string; executive: boolean;
  confidence: number; verification: string; linkedin: string;
};

export type Draft = {
  id: number; company: string; job_keys: string[]; to_email: string; to_name: string; position: string; kind: string;
  subject: string; body: string; linkedin_note: string; attach: string; follow_up: number;
  status: "draft" | "opened" | "sent" | "replied"; created: string; updated: string; sent: string | null;
};

export type OutreachCompany = {
  company: string;
  stage: "follow_up" | "to_write" | "drafts" | "sent" | "replied";
  domain: string | null;
  contacts: { people: Person[]; suggested: string[]; domain: string | null; pattern: string | null;
              fetched: string; error: string | null } | null;
  linkedin: string[];
  jobs: { key: string; title: string; url: string; status: string; outreach: string; posted: string | null;
          region: string; outreach_sent: string | null; applied: string | null }[];
  drafts: Draft[];
  follow_up_due: string[];
};

export type OutreachData = {
  companies: OutreachCompany[];
  hunter: { used: number; limit: number; key_set: boolean };
  writer: { key_set: boolean; model: string };
  gmail: boolean;
  follow_up_after_days: number;
  packets: { folder: string; has_emails: boolean; name: string }[];
};

export type RunRecord = {
  id: number; started: string; finished: string | null; label: string; source: string;
  status: "running" | "finished" | "cancelled" | "failed";
  summary: {
    minutes?: number; boards_read?: number; boards_paused?: number; requests?: number;
    by_site?: Record<string, number>; new_postings?: number; new_matches?: string[]; open_matches?: number;
    offline?: boolean; companies?: string[] | null; error?: string;
  };
};

export type ScheduleStatus = {
  enabled: boolean; times: string[]; paused: boolean; next: string | null; running_in_app: boolean;
  startup_supported: boolean; startup: boolean; tray: boolean;
};

export type UpdateInfo = { current: string; latest: string | null; url: string | null; newer: boolean; installed: boolean };
