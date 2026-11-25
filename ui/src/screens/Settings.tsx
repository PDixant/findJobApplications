import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api, send, type Changes, type KeysData, type KeyStatus, type PlainCheck, type PlainSummary, type RegionValue,
  type ScheduleStatus, type SettingField, type SettingsData,
} from "../api";
import { useRunActions } from "../components/RunControls";
import { Confirm, ErrorNote, Icon, Skeleton, Tabs } from "../components/bits";
import { toast } from "../live";
import { hex, n } from "../format";
import { go, useRoute } from "../router";
import { applyPaint, applyTheme, currentChoice, currentPaint, type PaintChoice, type ThemeChoice } from "../theme";
import { ClaudeHelp, Connections, PhoneAlerts } from "./SettingsExtras";
import { UpdatesPanel } from "../components/Update";

const LABELS: Record<string, string> = {
  titles: "Job titles", skip_titles: "Skip titles with", must_mention: "Stack keywords", max_age_hours: "Posted within (hours)",
  region: "Regions", skip: "Skip places", enabled: "On", provider: "Search service", runs_per_day: "Searches per day",
  look_back: "Look back", sites: "Job sites", places: "Places", skip_words: "Leave out words", pages_per_query: "Pages per search",
  monthly_query_limit: "Monthly search limit", new_companies: "New companies", career_page_lookups_per_run: "Careers page lookups per run",
  new_pages_per_site_per_run: "New pages per site per run", desktop_notifications: "Desktop pop-ups",
  since_last_check_by_default: "Always list since last check", check_all_companies_every_run: "Read every company every run",
  retry_board_search_hours: "Board guessing every (hours)", check_every_minutes: "Minutes between checks (--loop)",
  resume: "Resume file", about_me: "About-me file", contacts_per_company: "Contacts per company", departments: "Hunter departments",
  verify_emails: "Verify addresses", monthly_hunter_credit_limit: "Monthly Hunter limit", follow_up_after_days: "Follow up after (days)",
  model: "Claude model", times: "Run at", keep_in_tray: "Keep running after closing", score_new_matches: "Score new matches", monthly_limit: "Monthly Claude limit",
  resume_tex: "Base resume (.tex)", resume_rules: "Resume rules file", ntfy_topic: "ntfy topic", ntfy_server: "ntfy server",
  tailored_resumes_folder: "Tailored resumes folder", built_queries: "Built searches", my_queries: "Your own searches", same_titles_as_jobs: "Same titles as Which jobs",
  years_experience: "Your years of experience", years_over_ok: "Stretch allowance (years)", too_many_years: "Jobs asking for more",
};
const SWATCHES = ["C6EFCE", "DDEBF7", "D9F2F2", "FCE4D6", "E4DFEC", "FFF2CC", "F8CBAD", "EDEDED"];

type TabId = "jobs" | "search" | "runs" | "claude" | "keys" | "appearance" | "updates";
const TABS: { id: TabId; label: string; sections: string[] }[] = [
  { id: "jobs", label: "Which jobs", sections: ["jobs", "places"] },
  { id: "search", label: "Finding companies", sections: ["search", "career_sites"] },
  { id: "runs", label: "Runs and alerts", sections: ["schedule", "tracker", "alerts"] },
  { id: "claude", label: "Claude and email", sections: ["ai", "outreach"] },
  { id: "keys", label: "Keys", sections: [] },
  { id: "appearance", label: "Appearance", sections: [] },
  { id: "updates", label: "Updates", sections: [] },
];

const id = (section: string, key: string) => `${section}.${key}`;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
type SearchPreview = { searches: { site: string | null; query: string; mine: boolean }[]; pages_per_query: number };

export function Settings() {
  const client = useQueryClient();
  const route = useRoute();
  const tabParam = route.params.get("tab") ?? route.params.get("section");
  const tab: TabId = TABS.some((t) => t.id === tabParam) ? (tabParam as TabId) : "jobs";
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("settings") });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [preview, setPreview] = useState<{ summary: PlainSummary } & SearchPreview | null>(null);
  const saved = useQuery({ queryKey: ["search-queries", settings.data], queryFn: () => api<SearchPreview>("search/queries") });
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
  const changedIn = (t: (typeof TABS)[number]) => t.sections.reduce((sum, s) => sum + Object.keys(changes[s] ?? {}).length, 0);

  // The plain-words summary follows your edits before you save them.
  useEffect(() => {
    if (!dirty) {
      setPreview(null);
      setProblem(null);
      return;
    }
    const t = window.setTimeout(() => {
      send<{ summary: PlainSummary } & SearchPreview>("settings/preview", "POST", { changes })
        .then((r) => { setPreview(r); setProblem(null); })
        .catch((e) => setProblem(e instanceof Error ? e.message : String(e)));
    }, 400);
    return () => window.clearTimeout(t);
  }, [changes, dirty]);

  const value = (section: string, f: SettingField) => (id(section, f.key) in draft ? draft[id(section, f.key)] : f.value);
  // The search's own title list only matters when it isn't using your job filter's titles.
  const hidden = (s: SettingsData["sections"][number], f: SettingField) => {
    if (s.id !== "search" || f.key !== "titles") return false;
    const sameTitles = s.fields.find((x) => x.key === "same_titles_as_jobs");
    return Boolean(sameTitles && value("search", sameTitles));
  };
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
      toast("Your settings weren't saved. The reason is in In plain words.", "bad");
    } finally {
      setSaving(false);
    }
  }

  const current = TABS.find((t) => t.id === tab)!;
  const sections = settings.data?.sections.filter((s) => current.sections.includes(s.id)) ?? [];
  const showSearches = () => {
    go("settings", { tab: "search" });
    window.setTimeout(() => document.getElementById("search-list")?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
  };

  return (
    <>
      <header className="page-head with-tabs">
        <h1>Settings</h1>
        <p className="lede">Saved in my_settings.toml, which is yours alone: updates to Pathfinder never change it. Runs use your changes from their next start.</p>
        <Tabs label="Settings" active={tab} onPick={(t) => go("settings", { tab: t })}
          items={TABS.map((t) => ({ id: t.id, label: t.label, count: changedIn(t) ? `${changedIn(t)} changed` : undefined }))} />
      </header>
      <hr className="character" style={{ marginBottom: 32 }} />
      <ErrorNote error={settings.error} />
      {justSaved && (
        <div className="notice info" role="status">
          <Icon name="check" size={20} />
          <div className="saved-note">
            <span>Saved. Your matches update on the next run, or right now without making any requests.</span>
            <button className="button small" onClick={() => { start("refilter"); setJustSaved(false); }}>Re-filter saved postings</button>
          </div>
        </div>
      )}
      <div className="settings-layout">
        <div className="settings-forms">
          {!settings.data && current.sections.length > 0 && <Skeleton height={400} style={{ borderRadius: 12 }} />}
          {tab === "claude" && <ClaudeHelp />}
          {sections.map((s) => (
            <section key={s.id} className="card settings-section" aria-labelledby={`sec-${s.id}`}>
              <h2 id={`sec-${s.id}`}>{s.title}</h2>
              {s.fields.filter((f) => !hidden(s, f)).map((f) => (
                <Field key={f.key} field={f} value={value(s.id, f)} changed={Boolean(changes[s.id] && f.key in changes[s.id])}
                  onChange={(v) => set(s.id, f.key, v)} />
              ))}
              {s.id === "alerts" && <PhoneAlerts />}
              {s.id === "search" && (
                <SearchList data={preview ?? saved.data} unsaved={dirty}
                  onEdit={(q) => {
                    const mine = (value("search", s.fields.find((f) => f.key === "my_queries")!) as string[]) ?? [];
                    if (!mine.includes(q)) set("search", "my_queries", [...mine, q]);
                  }} />
              )}
            </section>
          ))}
          {tab === "runs" && <Background />}
          {tab === "claude" && <Connections />}
          {tab === "keys" && <Keys />}
          {tab === "appearance" && <Appearance />}
          {tab === "updates" && <UpdatesPanel />}
        </div>
        <PlainWords summary={preview?.summary ?? settings.data?.summary} saved={settings.data?.summary} dirty={dirty}
          problem={problem} onSearches={showSearches} />
      </div>
      {dirty && (
        <div className="save-bar" role="region" aria-label="Unsaved changes">
          <span>{n(Object.values(changes).reduce((t, s) => t + Object.keys(s).length, 0))} unsaved {Object.values(changes).reduce((t, s) => t + Object.keys(s).length, 0) === 1 ? "change" : "changes"}</span>
          <button className="text-button" onClick={() => { setDraft({}); setProblem(null); }}>Discard</button>
          <button className="button primary" onClick={save} disabled={saving || Boolean(problem)}>Save changes</button>
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------ in plain words

const SHOWN = 6;

/** Words, the first few, with the rest one click away. */
function Words({ words, empty = "None" }: { words: string[]; empty?: string }) {
  const [all, setAll] = useState(false);
  if (!words.length) return <span className="quiet">{empty}</span>;
  const shown = all ? words : words.slice(0, SHOWN);
  return (
    <>
      {shown.join(", ")}
      {words.length > SHOWN && (
        <button className="text-button more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `and ${n(words.length - SHOWN)} more`}
        </button>
      )}
    </>
  );
}

function CheckValue({ c }: { c: PlainCheck }) {
  const [places, setPlaces] = useState(false);
  if (c.groups) {
    return (
      <>
        <p className="plain-value">
          {c.groups.map((g) => g.name).join(", ") || <span className="quiet">No regions, so nothing matches</span>}
          {c.groups.length > 0 && (
            <button className="text-button more" onClick={() => setPlaces(!places)}>{places ? "Hide places" : "Show places"}</button>
          )}
        </p>
        {places && c.groups.map((g) => (
          <p key={g.name} className="plain-group"><b>{g.name}:</b> <span className="secondary">{g.words.join(", ")}</span></p>
        ))}
        {c.except && c.except.length > 0 && <p className="plain-note">Not if it's only in <Words words={c.except} /></p>}
      </>
    );
  }
  return (
    <>
      <p className="plain-value">{c.words ? <Words words={c.words} /> : c.text}</p>
      {c.note && <p className="plain-note">{c.note}</p>}
    </>
  );
}

function OnOff({ on, children, bad }: { on: boolean; children: ReactNode; bad?: boolean }) {
  return (
    <span className={`plain-state ${bad ? "bad" : on ? "on" : "off"}`}>
      <Icon name={bad ? "alert" : on ? "check" : "dash"} />{children}
    </span>
  );
}

/** What your settings do, in short sentences: the checks a job must pass, how new companies are
 *  found, and what else is on. With unsaved changes it shows the result of them, and marks what moved. */
function PlainWords({ summary, saved, dirty, problem, onSearches }: {
  summary?: PlainSummary; saved?: PlainSummary; dirty: boolean; problem: string | null; onSearches: () => void;
}) {
  if (!summary) return <aside className="card plain"><Skeleton height={320} /></aside>;
  const moved = (a: unknown, b: unknown) => dirty && !same(a, b);
  const was = (cid: string) => saved?.checks.find((c) => c.id === cid);
  const s = summary.search;
  return (
    <aside className="card plain" aria-labelledby="plain-title">
      <h2 id="plain-title" className="t-title-m">In plain words</h2>
      {problem ? (
        <p className="error-note" role="alert"><Icon name="alert" />{problem} Fix this and the summary comes back.</p>
      ) : (
        <p className="plain-lead">
          {dirty ? "With your unsaved changes, a" : "A"} job goes into your tracker when it passes all {summary.checks.length} of these checks.
        </p>
      )}
      <ul className="plain-checks" aria-label="Checks a job must pass">
        {summary.checks.map((c) => {
          const changed = moved(c, was(c.id));
          return (
            <li key={c.id} className={`plain-check ${changed ? "is-changed" : ""}`}>
              <p className="plain-label"><span>{c.label}</span>{changed && <span className="changed">Changed</span>}</p>
              <CheckValue c={c} />
            </li>
          );
        })}
      </ul>

      <div className={`plain-block plain-check ${moved(s, saved?.search) ? "is-changed" : ""}`} style={{ paddingBottom: 0 }}>
        <h3>Finding new companies</h3>
        {s.on ? (
          <>
            <p className="plain-value"><OnOff on>On, {s.per_day} a day through {s.provider}</OnOff></p>
            {!s.key && <p className="plain-value" style={{ marginTop: 4 }}><OnOff on={false} bad>No {s.provider} key yet. Add it under Keys.</OnOff></p>}
            <p className="plain-note">
              {s.searches ? `${n(s.searches)} ${s.searches === 1 ? "search" : "searches"} a run, up to ${n(s.queries)} queries. ` : "Nothing to search yet. Pick a job site or add searches of your own. "}
              At most {n(s.monthly_limit)} a month, each looking back over {s.look_back}.{" "}
              New companies are {s.watch_new ? "watched right away" : "added turned off, for you to review"}.
            </p>
            {s.searches > 0 && <button className="text-button" style={{ marginTop: 8 }} onClick={onSearches}>See every search</button>}
          </>
        ) : (
          <p className="plain-value"><OnOff on={false}>Off. Turn it on under Finding companies.</OnOff></p>
        )}
      </div>

      <div className="plain-block">
        <h3>Also</h3>
        <dl className="plain-extras">
          {summary.extras.map((x) => (
            <div key={x.id}><dt>{x.label}</dt><dd><OnOff on={x.on}>{x.text}</OnOff></dd></div>
          ))}
        </dl>
      </div>
      <p className="plain-files">Your tracker is <code>{summary.files.tracker}</code> and your company list is <code>{summary.files.companies}</code>.</p>
    </aside>
  );
}

// ------------------------------------------------------------------ fields

function Field({ field, value, changed, onChange }: {
  field: SettingField; value: unknown; changed: boolean; onChange: (v: unknown) => void;
}) {
  const label = LABELS[field.key] ?? field.key;
  const [kind, options] = field.kind.split(":");
  const inputId = `f-${field.key}`;
  let control;
  if (kind === "list") control = <Chips value={(value as string[]) ?? []} onChange={onChange} label={label} />;
  else if (kind === "lines") control = <Lines value={(value as string[]) ?? []} onChange={onChange} label={label} />;
  else if (kind === "number")
    control = <input id={inputId} type="number" min={0} value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />;
  else if (kind === "bool")
    control = <label className="switch"><input id={inputId} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
      {value ? "On" : "Off"}</label>;
  else if (kind === "choice")
    control = <select id={inputId} value={String(value)} onChange={(e) => onChange(e.target.value)}>
      {options.split(",").map((o) => <option key={o} value={o}>{o}</option>)}</select>;
  else if (kind === "choices") {
    const chosen = (value as string[]) ?? [];
    control = <div className="choice-group" role="group" aria-label={label}>{options.split(",").map((o) => (
      <label key={o} className="check"><input type="checkbox" checked={chosen.includes(o)}
        onChange={(e) => onChange(e.target.checked ? [...chosen, o] : chosen.filter((x) => x !== o))} />{o}</label>))}</div>;
  } else if (kind === "regions") control = <Regions value={(value as RegionValue[]) ?? []} onChange={onChange} />;
  else control = <input id={inputId} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  return (
    <div className={`field ${changed ? "is-changed" : ""} ${kind === "regions" || kind === "lines" ? "wide" : ""}`}>
      <div className="field-label">
        <span><label htmlFor={inputId}>{label}</label></span>
        <small>{field.help}</small>
      </div>
      <div className="field-control">{control}</div>
    </div>
  );
}

/** One item per line, for long values such as search queries. Keeps your typing as is and saves the
 *  non-empty lines. */
function Lines({ value, onChange, label }: { value: string[]; onChange: (v: string[]) => void; label: string }) {
  const [text, setText] = useState(value.join("\n"));
  useEffect(() => {
    setText((t) => (same(t.split("\n").map((x) => x.trim()).filter(Boolean), value) ? t : value.join("\n")));
  }, [value]);
  return (
    <textarea className="lines-input" value={text} aria-label={label} spellCheck={false}
      rows={Math.min(8, Math.max(3, text.split("\n").length + 1))}
      placeholder={'site:jobs.ashbyhq.com intitle:"Frontend" ("Vancouver" OR "Remote")'}
      onChange={(e) => {
        setText(e.target.value);
        onChange(e.target.value.split("\n").map((x) => x.trim()).filter(Boolean));
      }} />
  );
}

/** Exactly what each web search sends, as you edit. A built search can be copied into your own
 *  list to change it. */
function SearchList({ data, unsaved, onEdit }: {
  data: SearchPreview | undefined; unsaved: boolean; onEdit: (query: string) => void;
}) {
  if (!data) return null;
  const count = data.searches.length;
  return (
    <div className="search-list" id="search-list">
      <h3>{unsaved ? "Searches with your unsaved changes" : "Searches each run sends"}</h3>
      <p className="hint">
        {count
          ? `${count} ${count === 1 ? "search" : "searches"}, each reading up to ${data.pages_per_query} result ${data.pages_per_query === 1 ? "page" : "pages"}: up to ${count * data.pages_per_query} queries a run.`
          : "Nothing to search: pick at least one job site, or add searches of your own."}
      </p>
      <ol>
        {data.searches.map((q) => (
          <li key={q.query}>
            <code>{q.query}</code>
            {q.mine ? <span>Yours</span> : (
              <button type="button" className="text-button" onClick={() => onEdit(q.query)}
                title="Copies it into Your own searches, where you can change it. Then turn off Built searches if you only want yours.">
                Edit as my own
              </button>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A list of words: type and press Enter (or a comma) to add; a word's x removes it. */
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
    <div className="chips-input" onClick={(e) => (e.currentTarget.querySelector("input") as HTMLInputElement | null)?.focus()}>
      {value.map((w) => (
        <span key={w} className="word">
          {w}
          <button type="button" aria-label={`Remove ${w}`} onClick={() => onChange(value.filter((x) => x !== w))}><Icon name="close" size={14} /></button>
        </span>
      ))}
      <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={key} onBlur={() => text && add(text)}
        placeholder={value.length ? "Add another" : "Type, then press Enter"} aria-label={`Add to ${label}`} />
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
        <div key={i} className="region-card">
          <div className="region-top">
            <input className="region-name" value={r.name} onChange={(e) => update(i, { name: e.target.value })}
              aria-label="Region name" placeholder="Region name" />
            <div className="swatches" role="group" aria-label={`Colour for ${r.name} in your tracker`}>
              <span className="eyebrow">In your tracker</span>
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
      <button type="button" className="button small" style={{ alignSelf: "flex-start" }}
        onClick={() => onChange([...value, { name: "", color: "", match: [] }])}>
        Add a region
      </button>
      <p className="hint">Short codes such as BC, ON and CA must match exactly. The first region that matches wins. Removing a region takes effect when you save.</p>
    </div>
  );
}

// ------------------------------------------------------------------ appearance

const THEME_CHOICES: { id: ThemeChoice; label: string; note: string }[] = [
  { id: "system", label: "Match my computer", note: "Light or dark, whichever your computer is set to, and it changes when your computer does." },
  { id: "light", label: "Light", note: "Warm paper and ink, whatever your computer is set to." },
  { id: "dark", label: "Dark", note: "Ink and soft light, whatever your computer is set to." },
];

const PAINT_CHOICES: { id: PaintChoice; label: string; note: string }[] = [
  { id: "screen", label: "Kanso", note: "Warm paper and ink, with a colour that changes by screen: blue for finding jobs, green for your applications, ink in Settings." },
  { id: "structural", label: "Structural", note: "Cool grey paper and deep blue. In dark, a midnight blue." },
  { id: "tiger-eye", label: "Tiger Eye", note: "Warm sand and burnt orange. In dark, a deep brown." },
  { id: "infrared", label: "Infrared", note: "Blush paper and carmine red. In dark, a red-tinged black. Losses and errors keep their own red, with a sign or an icon." },
  { id: "nori", label: "Nori", note: "Pale sage and dusky green. In dark, a deep forest green with ivory light." },
  { id: "sumi", label: "Sumi", note: "Paper and ink only, no colour at all." },
];

type AppearanceChoice = { theme: ThemeChoice; paint: PaintChoice };

function Appearance() {
  const client = useQueryClient();
  const appearance = useQuery({ queryKey: ["appearance"], queryFn: () => api<AppearanceChoice>("appearance") });
  const theme = appearance.data?.theme ?? currentChoice();
  const paint = appearance.data?.paint ?? currentPaint();
  async function save(change: Partial<AppearanceChoice>) {
    // shown at once; saving follows
    if (change.theme) applyTheme(change.theme);
    if (change.paint) applyPaint(change.paint);
    try {
      client.setQueryData(["appearance"], await send<AppearanceChoice>("appearance", "PUT", change));
    } catch (e) {
      toast(e instanceof Error ? e.message : "That didn't save. Try again.", "bad");
      applyTheme(theme);
      applyPaint(paint);
    }
  }
  return (
    <section className="card settings-section" aria-labelledby="sec-appearance">
      <h2 id="sec-appearance">Appearance</h2>
      <p className="hint">Saved on this computer, and used every time Pathfinder opens.</p>

      <h3 className="theme-group" id="appearance-mode">Light or dark</h3>
      <div className="theme-choices" role="radiogroup" aria-labelledby="appearance-mode">
        {THEME_CHOICES.map((c) => (
          <label key={c.id} className={`theme-choice ${theme === c.id ? "is-on" : ""}`}>
            <input type="radio" name="theme" value={c.id} checked={theme === c.id} onChange={() => save({ theme: c.id })} />
            <span className={`theme-swatch is-${c.id}`} aria-hidden="true" />
            <span className="theme-text">
              <span className="theme-label">{c.label}</span>
              <span className="hint">{c.note}</span>
            </span>
          </label>
        ))}
      </div>

      <h3 className="theme-group" id="appearance-paint">Theme</h3>
      <p className="hint">The whole look: page, cards, text and accent. Each theme comes from one colour combination in the Kanso design language, and has a light and a dark version.</p>
      <div className="theme-choices" role="radiogroup" aria-labelledby="appearance-paint">
        {PAINT_CHOICES.map((c) => (
          <label key={c.id} className={`theme-choice ${paint === c.id ? "is-on" : ""}`}>
            <input type="radio" name="paint" value={c.id} checked={paint === c.id} onChange={() => save({ paint: c.id })} />
            <span className={`theme-preview is-${c.id}`} aria-hidden="true" />
            <span className="theme-text">
              <span className="theme-label">{c.label}</span>
              <span className="hint">{c.note}</span>
            </span>
          </label>
        ))}
      </div>
    </section>
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
    <section className="card settings-section" aria-labelledby="sec-keys">
      <h2 id="sec-keys">Keys and accounts</h2>
      <p className="hint">
        {data?.store
          ? `Keys are saved in ${data.store_name ?? "your computer's password store"}, not in a file, and taken out of .env.`
          : "This computer has no password store Pathfinder can use. A key can still go in .env, a plain text file only your account can read; you'll be asked first."}{" "}
        The command line reads them from the same place.
      </p>
      <ErrorNote error={keys.error} />
      {shown?.map((k) => <KeyRow key={k.name} k={k} />)}
    </section>
  );
}

export function KeyRow({ k }: { k: KeyStatus }) {
  const client = useQueryClient();
  const keys = useQuery({ queryKey: ["keys"], queryFn: () => api<KeysData>("keys") });
  const [value, setValue] = useState("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [askPlain, setAskPlain] = useState(false);
  const secret = k.name !== "GMAIL_ADDRESS";
  const noStore = keys.data ? !keys.data.store : false;

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast(e instanceof Error ? e.message : "That didn't go through. Try again.", "bad");
    } finally {
      setBusy(false);
    }
  }
  const save = (plainFileOk = false) => act(async () => {
    if (noStore && !plainFileOk) {
      setAskPlain(true);
      return;
    }
    await send(`keys/${k.name}`, "PUT", { value, plain_file_ok: plainFileOk });
    setAskPlain(false);
    setValue("");
    setResult(null);
    await client.invalidateQueries({ queryKey: ["keys"] });
    client.invalidateQueries({ queryKey: ["summary"] });
    client.invalidateQueries({ queryKey: ["settings"] });
    toast(`${k.label} saved.`, "good");
  });
  const test = () => act(async () => setResult(await send<{ ok: boolean; message: string }>(`keys/${k.name}/test`, "POST")));
  const remove = () => act(async () => {
    await send(`keys/${k.name}`, "DELETE");
    setResult(null);
    await client.invalidateQueries({ queryKey: ["keys"] });
    client.invalidateQueries({ queryKey: ["settings"] });
    toast(`${k.label} removed.`, "info");
  });

  return (
    <div className="key-row">
      <div className="field-label">
        <span>{k.label}</span>
        <small className={k.set ? "key-state on" : "key-state"}>
          <Icon name={k.set ? "check" : "dash"} size={14} />
          {k.set ? `${SOURCE_TEXT[k.source ?? ""] ?? "Set"}: ${k.preview}` : "Not set"}
        </small>
      </div>
      <div className="field-control" style={{ width: "100%" }}>
        <form className="key-controls" style={{ width: "100%" }} onSubmit={(e) => { e.preventDefault(); save(); }}>
          <input type={secret ? "password" : "email"} value={value} onChange={(e) => setValue(e.target.value)}
            placeholder={k.set ? "Paste a new value to replace it" : "Paste it here"} autoComplete="off" aria-label={k.label} />
          <button className="button" type="submit" disabled={!value.trim() || busy}>Save</button>
          {k.set && k.name !== "GMAIL_ADDRESS" && <button className="button" type="button" onClick={test} disabled={busy}>Test</button>}
        </form>
        {k.set && k.source !== "environment" && (
          <Confirm label="Remove" disabled={busy} yes="Remove it" onYes={remove}
            question={`Remove your ${k.label} from this computer? You'd have to paste it again to use it.`} />
        )}
      </div>
      {askPlain && (
        <div className="confirm" role="group" aria-label="Save in a plain file?" style={{ marginTop: 12, gridColumn: "1 / -1" }}>
          <p>This computer has no password store, so the key would go in <code>.env</code>, a plain text file only your account can read. It's never shared in git. Save it there?</p>
          <button type="button" className="button small" onClick={() => save(true)} disabled={busy}>Save in .env</button>
          <button type="button" className="text-button" onClick={() => setAskPlain(false)}>Don't save</button>
        </div>
      )}
      {result && (
        <p className={result.ok ? "key-result ok" : "key-result bad"}><Icon name={result.ok ? "check" : "alert"} />{result.message}</p>
      )}
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
      toast(e instanceof Error ? e.message : "That didn't go through. Try again.", "bad");
    }
    client.invalidateQueries({ queryKey: ["schedule"] });
    client.invalidateQueries({ queryKey: ["old-tasks"] });
  }
  if (!s) return null;
  return (
    <section className="card settings-section" aria-labelledby="sec-background">
      <h2 id="sec-background">Running in the background</h2>
      <p className="hint">
        {s.tray
          ? "Closing the window keeps Pathfinder running by the clock, so automatic runs happen. Quit it from that icon."
          : "Pathfinder runs only while its window is open. Closing the window quits it, so automatic runs happen only while it's open."}
      </p>
      {s.startup_supported && s.keep_in_tray && (
        <div className="field">
          <div className="field-label"><span>Start when you sign in</span>
            <small>Opens quietly by the clock, so automatic runs happen without you opening it</small></div>
          <div className="field-control">
            <label className="switch"><input type="checkbox" checked={s.startup}
              onChange={(e) => act(() => send("startup", "POST", { on: e.target.checked }), e.target.checked ? "Pathfinder starts when you sign in." : "Pathfinder no longer starts on its own.")} />
              {s.startup ? "On" : "Off"}</label>
          </div>
        </div>
      )}
      <div className="field">
        <div className="field-label"><span>Pause automatic runs</span><small>Until you turn this off again</small></div>
        <div className="field-control">
          <label className="switch"><input type="checkbox" checked={s.paused}
            onChange={(e) => act(() => send("schedule/pause", "POST", { paused: e.target.checked }), e.target.checked ? "Automatic runs paused." : "Automatic runs are back on.")} />
            {s.paused ? "Paused" : "Off"}</label>
        </div>
      </div>
      {live.length > 0 && (
        <div className="old-tasks">
          <p><strong>Your old Windows scheduled task still runs Pathfinder.</strong> The app runs on its own schedule now,
            so turn the old task off and runs won't double up. You can turn it back on in Task Scheduler.</p>
          {live.map((t) => (
            <div key={t.path + t.name} className="old-task">
              <span>{t.name} <span className="quiet">({t.state})</span></span>
              <button className="button small" onClick={() => act(() => send("system/old-tasks/disable", "POST", { name: t.name, path: t.path }), `Turned off ${t.name}.`)}>Turn it off</button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
