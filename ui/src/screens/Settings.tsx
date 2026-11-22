import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api, send, type Changes, type KeysData, type KeyStatus, type RegionValue, type ScheduleStatus, type SettingField,
  type SettingsData,
} from "../api";
import { useRunActions } from "../components/RunControls";
import { ErrorNote } from "../components/bits";
import { toast } from "../live";
import { hex } from "../format";
import { ClaudeHelp, Connections, PhoneAlerts } from "./SettingsExtras";

const LABELS: Record<string, string> = {
  titles: "Job titles", skip_titles: "Skip titles with", must_mention: "Stack keywords", max_age_hours: "Posted within (hours)",
  region: "Regions", skip: "Skip places", enabled: "On", provider: "Search service", runs_per_day: "Searches per day",
  look_back: "Look back", sites: "Job sites", places: "Places", skip_words: "Leave out words", pages_per_query: "Pages per site",
  monthly_query_limit: "Monthly search limit", new_companies: "New companies", career_page_lookups_per_run: "Careers page lookups per run",
  new_pages_per_site_per_run: "New pages per site per run", desktop_notifications: "Desktop pop-ups",
  since_last_check_by_default: "Always list since last check", check_all_companies_every_run: "Read every company every run",
  retry_board_search_hours: "Board guessing every (hours)", check_every_minutes: "Minutes between checks (--loop)",
  resume: "Resume file", about_me: "About-me file", contacts_per_company: "Contacts per company", departments: "Hunter departments",
  verify_emails: "Verify addresses", monthly_hunter_credit_limit: "Monthly Hunter limit", follow_up_after_days: "Follow up after (days)",
  model: "Claude model", times: "Run at", keep_in_tray: "Keep running after closing", score_new_matches: "Score new matches", monthly_limit: "Monthly Claude limit",
  resume_tex: "Base resume (.tex)", resume_rules: "Resume rules file", ntfy_topic: "ntfy topic", ntfy_server: "ntfy server",
  tailored_resumes_folder: "Tailored resumes folder",
};
const SWATCHES = ["C6EFCE", "DDEBF7", "D9F2F2", "FCE4D6", "E4DFEC", "FFF2CC", "F8CBAD", "EDEDED"];

const id = (section: string, key: string) => `${section}.${key}`;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function Settings() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("settings") });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [explain, setExplain] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const { start } = useRunActions();

  const original = useMemo(() => {
    const out: Record<string, unknown> = {};
    settings.data?.sections.forEach((s) => s.fields.forEach((f) => (out[id(s.id, f.key)] = f.value)));
    return out;
  }, [settings.data]);

  const changes = useMemo(() => {
    const out: Changes = {};
    for (const [k, v] of Object.entries(draft)) {
      if (same(v, original[k])) continue;
      const [section, key] = k.split(".");
      (out[section] ??= {})[key] = v;
    }
    return out;
  }, [draft, original]);
  const dirty = Object.keys(changes).length > 0;

  // The plain-English summary follows your edits before you save them.
  useEffect(() => {
    if (!dirty) {
      setExplain(null);
      setProblem(null);
      return;
    }
    const t = window.setTimeout(() => {
      send<{ explain: string }>("settings/preview", "POST", { changes })
        .then((r) => { setExplain(r.explain); setProblem(null); })
        .catch((e) => setProblem(e instanceof Error ? e.message : String(e)));
    }, 400);
    return () => window.clearTimeout(t);
  }, [changes, dirty]);

  const value = (section: string, f: SettingField) => (id(section, f.key) in draft ? draft[id(section, f.key)] : f.value);
  const set = (section: string, key: string, v: unknown) => {
    setJustSaved(false);
    setDraft((d) => ({ ...d, [id(section, key)]: v }));
  };

  async function save() {
    setSaving(true);
    try {
      const fresh = await send<SettingsData>("settings", "PUT", { changes });
      client.setQueryData(["settings"], fresh);
      setDraft({});
      setJustSaved(true);
      client.invalidateQueries({ queryKey: ["meta"] });
      client.invalidateQueries({ queryKey: ["summary"] });
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
      toast("Settings not saved. See the note at the bottom.", "bad");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="screen settings">
      <header className="screen-head">
        <h1>Settings</h1>
        <p className="lede">Saved into config.toml, keeping its notes. Runs pick up changes on their next start.</p>
      </header>
      <ErrorNote error={settings.error} />
      {justSaved && (
        <div className="saved-note">
          <span>Saved. Your matches update on the next run, or now without making any requests:</span>
          <button className="button" onClick={() => { start("refilter"); setJustSaved(false); }}>Re-filter saved postings</button>
        </div>
      )}
      <div className="settings-layout">
        <div className="settings-forms">
          {settings.data?.sections.map((s) => (
            <section key={s.id} className="panel settings-section" aria-labelledby={`sec-${s.id}`}>
              <h2 id={`sec-${s.id}`}>{s.title}</h2>
              {s.fields.map((f) => (
                <Field key={f.key} field={f} value={value(s.id, f)} changed={Boolean(changes[s.id] && f.key in changes[s.id])}
                  onChange={(v) => set(s.id, f.key, v)} />
              ))}
              {s.id === "alerts" && <PhoneAlerts />}
            </section>
          ))}
          <ClaudeHelp />
          <Connections />
          <Background />
          <Keys />
        </div>
        <aside className="panel explain">
          <h2>In plain words</h2>
          {dirty && <p className="hint">{problem ? "This change can't be saved yet:" : "With your unsaved changes:"}</p>}
          {problem ? <p className="error-note">{problem}</p>
            : <pre className="explain-text">{explain ?? settings.data?.explain ?? ""}</pre>}
        </aside>
      </div>
      {dirty && (
        <div className="save-bar">
          <span>{Object.values(changes).reduce((t, s) => t + Object.keys(s).length, 0)} unsaved changes</span>
          <button className="text-button" onClick={() => { setDraft({}); setProblem(null); }}>Discard</button>
          <button className="button primary" onClick={save} disabled={saving || Boolean(problem)}>Save changes</button>
        </div>
      )}
    </div>
  );
}

function Field({ field, value, changed, onChange }: {
  field: SettingField; value: unknown; changed: boolean; onChange: (v: unknown) => void;
}) {
  const label = LABELS[field.key] ?? field.key;
  const [kind, options] = field.kind.split(":");
  let control;
  if (kind === "list") control = <Chips value={(value as string[]) ?? []} onChange={onChange} label={label} />;
  else if (kind === "number")
    control = <input type="number" min={0} className="num-input" value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />;
  else if (kind === "bool")
    control = <label className="switch"><input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
      <span>{value ? "Yes" : "No"}</span></label>;
  else if (kind === "choice")
    control = <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
      {options.split(",").map((o) => <option key={o} value={o}>{o}</option>)}</select>;
  else if (kind === "choices") {
    const chosen = (value as string[]) ?? [];
    control = <div className="choice-group">{options.split(",").map((o) => (
      <label key={o} className="check"><input type="checkbox" checked={chosen.includes(o)}
        onChange={(e) => onChange(e.target.checked ? [...chosen, o] : chosen.filter((x) => x !== o))} />{o}</label>))}</div>;
  } else if (kind === "regions") control = <Regions value={(value as RegionValue[]) ?? []} onChange={onChange} />;
  else control = <input value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  return (
    <div className={`field ${changed ? "is-changed" : ""} ${kind === "regions" ? "wide" : ""}`}>
      <div className="field-label">
        <span>{label}</span>
        <small>{field.help}</small>
      </div>
      <div className="field-control">{control}</div>
    </div>
  );
}

/** A list of words: type and press Enter (or a comma) to add; click a word's x to remove it. */
export function Chips({ value, onChange, label }: { value: string[]; onChange: (v: string[]) => void; label: string }) {
  const [text, setText] = useState("");
  function add(raw: string) {
    const words = raw.split(",").map((w) => w.trim()).filter((w) => w && !value.includes(w));
    if (words.length) onChange([...value, ...words]);
    setText("");
  }
  function key(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(text);
    } else if (e.key === "Backspace" && !text && value.length) {
      onChange(value.slice(0, -1));
    }
  }
  return (
    <div className="chips-input">
      {value.map((w) => (
        <span key={w} className="word">
          {w}
          <button type="button" aria-label={`Remove ${w}`} onClick={() => onChange(value.filter((x) => x !== w))}>×</button>
        </span>
      ))}
      <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={key} onBlur={() => text && add(text)}
        placeholder={value.length ? "Add" : "Type and press Enter"} aria-label={`Add to ${label}`} />
    </div>
  );
}

export function Regions({ value, onChange }: { value: RegionValue[]; onChange: (v: RegionValue[]) => void }) {
  const update = (i: number, patch: Partial<RegionValue>) => onChange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const move = (i: number, by: number) => {
    const next = [...value];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    onChange(next);
  };
  return (
    <div className="regions">
      {value.map((r, i) => (
        <div key={i} className="region-card" style={{ borderLeftColor: hex(r.color) ?? "var(--line-strong)" }}>
          <div className="region-top">
            <input className="region-name" value={r.name} onChange={(e) => update(i, { name: e.target.value })}
              aria-label="Region name" placeholder="Region name" />
            <div className="swatches" role="group" aria-label={`Colour for ${r.name}`}>
              {SWATCHES.map((c) => (
                <button key={c} type="button" className={`sw ${r.color === c ? "is-on" : ""}`} style={{ background: hex(c) }}
                  aria-label={`Colour ${c}`} aria-pressed={r.color === c} onClick={() => update(i, { color: c })} />
              ))}
            </div>
            <div className="region-actions">
              <button type="button" className="text-button" disabled={i === 0} onClick={() => move(i, -1)}>Up</button>
              <button type="button" className="text-button" disabled={i === value.length - 1} onClick={() => move(i, 1)}>Down</button>
              <button type="button" className="text-button danger" onClick={() => onChange(value.filter((_, j) => j !== i))}>Remove</button>
            </div>
          </div>
          <Chips value={r.match} onChange={(match) => update(i, { match })} label={`places for ${r.name}`} />
        </div>
      ))}
      <button type="button" className="button" onClick={() => onChange([...value, { name: "", color: "", match: [] }])}>
        Add a region
      </button>
      <p className="hint">Short all-caps codes such as BC, ON and CA must match exactly. The first region that matches wins.</p>
    </div>
  );
}

// ------------------------------------------------------------------ keys

const SOURCE_TEXT: Record<string, string> = {
  "password store": "Saved in your computer's password store",
  ".env": "From the .env file",
  environment: "From a system environment variable",
};

function Keys() {
  const keys = useQuery({ queryKey: ["keys"], queryFn: () => api<KeysData>("keys") });
  const data = keys.data;
  const searchKey = { serper: "SERPER_API_KEY", brave: "BRAVE_API_KEY", serpapi: "SERPAPI_API_KEY" }[data?.search_provider ?? "serper"];
  const shown = data?.keys.filter((k) => !k.name.endsWith("_API_KEY") || ["HUNTER_API_KEY", "ANTHROPIC_API_KEY", searchKey].includes(k.name) || k.set);
  return (
    <section className="panel settings-section" aria-labelledby="sec-keys">
      <h2 id="sec-keys">Keys and accounts</h2>
      <p className="hint">
        {data?.store
          ? "Keys are saved in Windows Credential Manager (the Keychain on a Mac), not in a file, and taken out of .env."
          : "This computer has no password store the app can use, so keys are saved in the .env file."}{" "}
        The scheduled task and the command line read them from the same place.
      </p>
      <ErrorNote error={keys.error} />
      {shown?.map((k) => <KeyRow key={k.name} k={k} />)}
    </section>
  );
}

export function KeyRow({ k }: { k: KeyStatus }) {
  const client = useQueryClient();
  const [value, setValue] = useState("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const secret = k.name !== "GMAIL_ADDRESS";

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast(e instanceof Error ? e.message : "That did not work.", "bad");
    } finally {
      setBusy(false);
    }
  }
  const save = () => act(async () => {
    await send(`keys/${k.name}`, "PUT", { value });
    setValue("");
    setResult(null);
    await client.invalidateQueries({ queryKey: ["keys"] });
    client.invalidateQueries({ queryKey: ["summary"] });
    toast(`${k.label} saved.`, "good");
  });
  const test = () => act(async () => setResult(await send<{ ok: boolean; message: string }>(`keys/${k.name}/test`, "POST")));
  const remove = () => act(async () => {
    await send(`keys/${k.name}`, "DELETE");
    setResult(null);
    await client.invalidateQueries({ queryKey: ["keys"] });
  });

  return (
    <div className="key-row">
      <div className="field-label">
        <span>{k.label}</span>
        <small>{k.set ? `${SOURCE_TEXT[k.source ?? ""] ?? "Set"}: ${k.preview}` : "Not set"}</small>
      </div>
      <form className="key-controls" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <input type={secret ? "password" : "email"} value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={k.set ? "Paste a new value to replace it" : "Paste it here"} autoComplete="off" aria-label={k.label} />
        <button className="button" type="submit" disabled={!value.trim() || busy}>Save</button>
        {k.set && k.name !== "GMAIL_ADDRESS" && (
          <button className="button" type="button" onClick={test} disabled={busy}>Test</button>
        )}
        {k.set && k.source !== "environment" && (
          <button className="text-button danger" type="button" onClick={remove} disabled={busy}>Remove</button>
        )}
      </form>
      {result && <p className={result.ok ? "key-result ok" : "key-result bad"}>{result.message}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ running in the background

function Background() {
  const client = useQueryClient();
  const schedule = useQuery({ queryKey: ["schedule"], queryFn: () => api<ScheduleStatus>("schedule") });
  const tasks = useQuery({ queryKey: ["old-tasks"], queryFn: () => api<{ tasks: { name: string; path: string; state: string }[] }>("system/old-tasks") });
  const s = schedule.data;
  const live = tasks.data?.tasks.filter((t) => t.state !== "Disabled") ?? [];
  async function act(fn: () => Promise<unknown>, done: string) {
    try {
      await fn();
      toast(done, "good");
    } catch (e) {
      toast(e instanceof Error ? e.message : "That did not work.", "bad");
    }
    client.invalidateQueries({ queryKey: ["schedule"] });
    client.invalidateQueries({ queryKey: ["old-tasks"] });
  }
  if (!s) return null;
  return (
    <section className="panel settings-section" aria-labelledby="sec-background">
      <h2 id="sec-background">Running in the background</h2>
      <p className="hint">
        {s.tray
          ? "Closing the window keeps Job Watcher running by the clock, so automatic runs happen. Quit it from that icon."
          : "Job Watcher runs only while its window is open. Closing the window quits it, so automatic runs happen only while it's open."}
      </p>
      {s.startup_supported && s.keep_in_tray && (
        <div className="field">
          <div className="field-label"><span>Start when you sign in</span>
            <small>Opens quietly by the clock, so automatic runs happen without you opening it</small></div>
          <div className="field-control">
            <label className="switch"><input type="checkbox" checked={s.startup}
              onChange={(e) => act(() => send("startup", "POST", { on: e.target.checked }), e.target.checked ? "Job Watcher starts when you sign in." : "Job Watcher no longer starts on its own.")} />
              <span>{s.startup ? "Yes" : "No"}</span></label>
          </div>
        </div>
      )}
      <div className="field">
        <div className="field-label"><span>Pause automatic runs</span><small>Until you turn this off again</small></div>
        <div className="field-control">
          <label className="switch"><input type="checkbox" checked={s.paused}
            onChange={(e) => act(() => send("schedule/pause", "POST", { paused: e.target.checked }), e.target.checked ? "Automatic runs paused." : "Automatic runs back on.")} />
            <span>{s.paused ? "Paused" : "No"}</span></label>
        </div>
      </div>
      {live.length > 0 && (
        <div className="old-tasks">
          <p><strong>Your old Windows scheduled task still runs Job Watcher.</strong> Now that the app runs on its own schedule, turn it off so runs don't double up. You can turn it back on in Task Scheduler.</p>
          {live.map((t) => (
            <div key={t.path + t.name} className="old-task">
              <span>{t.name} <span className="quiet">({t.state})</span></span>
              <button className="button" onClick={() => act(() => send("system/old-tasks/disable", "POST", { name: t.name, path: t.path }), `Turned off ${t.name}.`)}>Turn it off</button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
