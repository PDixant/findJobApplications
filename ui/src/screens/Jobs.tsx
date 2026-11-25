import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { api, send, type AiStatus, type Fit, type Job, type JobDetailsData, type JobEdit, type Meta, type PostingPage,
  type Tailored } from "../api";
import { Chip, Drawer, Empty, ErrorNote, External } from "../components/bits";
import { FitBadge, Flags, payText, WORKPLACES } from "../components/Fit";
import { toast } from "../live";
import { Table } from "../components/Table";
import { ago, day, hex, n, when } from "../format";
import { loadPrefs, savePrefs, useEditJob, useJobs, useMeta, useSummary } from "../queries";
import { go, type Screen } from "../router";

function Tabs({ active }: { active: Screen }) {
  const jobs = useJobs();
  const summary = useSummary();
  return (
    <nav className="tabs" aria-label="Jobs views">
      <button className={active === "jobs" ? "tab is-active" : "tab"} onClick={() => go("jobs")}>
        Your matches <span className="num">{jobs.data ? n(jobs.data.length) : ""}</span>
      </button>
      <button className={active === "postings" ? "tab is-active" : "tab"} onClick={() => go("postings")}>
        All saved postings <span className="num">{summary.data ? n(summary.data.postings.open) : ""}</span>
      </button>
    </nav>
  );
}

// ------------------------------------------------------------------ your matches

type Filters = {
  text: string;
  regions: string[];
  level: string;
  status: string;
  outreach: string;
  fit: string;
  workplace: string;
  only: string;
  closed: boolean;
};
const NO_FILTERS: Filters = {
  text: "", regions: [], level: "", status: "", outreach: "", fit: "", workplace: "", only: "", closed: false,
};
const FIT_CHOICES: Record<string, string> = { APPLY: "Apply", STRETCH: "Stretch", SKIP: "Skip", none: "Not scored" };
const ONLY: Record<string, (j: Job) => boolean> = {
  "With a pay range": (j) => Boolean(j.pay),
  "Where you know someone": (j) => j.connections > 0,
  "No work permit limits": (j) => !(j.work_auth ?? []).length,
  "Within your years": (j) => !j.years_over,
  "Relocation help": (j) => j.relocation === true,
  "Fresh (not reposted)": (j) => !j.reposted && !j.long_open,
};

export function Jobs() {
  const jobs = useJobs();
  const meta = useMeta();
  const [filters, setFilters] = useState<Filters>(() => loadPrefs("jobFilters", NO_FILTERS));
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => savePrefs("jobFilters", filters), [filters]);
  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));

  const shown = useMemo(() => {
    const words = filters.text.toLowerCase().split(/\s+/).filter(Boolean);
    return (jobs.data ?? []).filter((j) => {
      if (!filters.closed && !j.open) return false;
      if (filters.regions.length && !filters.regions.includes(j.region)) return false;
      if (filters.level && j.level !== filters.level) return false;
      if (filters.status && j.status !== filters.status) return false;
      if (filters.outreach && (j.outreach || "None") !== filters.outreach) return false;
      if (filters.fit && (j.fit?.verdict ?? "none") !== filters.fit) return false;
      if (filters.workplace && j.workplace !== filters.workplace) return false;
      if (filters.only && ONLY[filters.only] && !ONLY[filters.only](j)) return false;
      const hay = `${j.title} ${j.company} ${j.locations.join(" ")} ${j.stack.join(" ")}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }, [jobs.data, filters]);

  const editJob = useEditJob();
  const onEdit = editJob.mutate;
  const columns = useMemo(() => jobColumns(meta.data, (key, changes) => onEdit({ key, changes })), [meta.data, onEdit]);
  const job = jobs.data?.find((j) => j.key === selected) ?? null;
  const filtered = JSON.stringify(filters) !== JSON.stringify({ ...NO_FILTERS, closed: filters.closed });

  return (
    <div className={`screen with-drawer ${job ? "drawer-open" : ""}`}>
      <div className="screen-main">
        <header className="screen-head">
          <h1>Jobs</h1>
          <Tabs active="jobs" />
        </header>
        <div className="filters" role="search">
          <div className="filter-row">
          <input
            className="search"
            type="search"
            placeholder="Search title, company, place or stack"
            value={filters.text}
            onChange={(e) => set({ text: e.target.value })}
            aria-label="Search your matches"
          />
          <Select label="Level" value={filters.level} onChange={(level) => set({ level })}
            options={["Senior/Lead", "Mid", "Unlabelled"]} />
          <Select label="Status" value={filters.status} onChange={(status) => set({ status })}
            options={meta.data?.statuses ?? []} />
          <Select label="Outreach" value={filters.outreach} onChange={(outreach) => set({ outreach })}
            options={["None", ...(meta.data?.outreach ?? [])]} />
          <Select label="Fit" value={filters.fit} onChange={(fit) => set({ fit })} options={Object.keys(FIT_CHOICES)}
            labels={FIT_CHOICES} />
          <Select label="Where" value={filters.workplace} onChange={(workplace) => set({ workplace })}
            options={Object.keys(WORKPLACES)} labels={WORKPLACES} />
          <Select label="Only" value={filters.only} onChange={(only) => set({ only })} options={Object.keys(ONLY)} />
          <label className="check">
            <input type="checkbox" checked={filters.closed} onChange={(e) => set({ closed: e.target.checked })} />
            Show closed
          </label>
          </div>
          <div className="region-toggles" role="group" aria-label="Regions">
            {meta.data?.regions.map((r) => {
              const on = filters.regions.includes(r.name);
              return (
                <button
                  key={r.name}
                  className={`region-toggle ${on ? "is-on" : ""}`}
                  style={{ "--region": hex(r.color) ?? "var(--line)" } as React.CSSProperties}
                  aria-pressed={on}
                  onClick={() =>
                    set({ regions: on ? filters.regions.filter((x) => x !== r.name) : [...filters.regions, r.name] })
                  }
                >
                  {r.name}
                </button>
              );
            })}
          </div>
        </div>
        <p className="count-line">
          {jobs.data ? `Showing ${n(shown.length)} of ${n(jobs.data.length)}` : "Loading your matches"}
          {filtered && (
            <button className="text-button" onClick={() => setFilters({ ...NO_FILTERS, closed: filters.closed })}>
              Clear filters
            </button>
          )}
        </p>
        <ErrorNote error={jobs.error} />
        {jobs.data && shown.length === 0 ? (
          <Empty title={jobs.data.length ? "No matches fit these filters" : "No matches yet"}>
            {jobs.data.length
              ? "Clear a filter or turn on Show closed."
              : "Matches appear here after a run finds postings that fit your settings."}
          </Empty>
        ) : (
          <Table
            data={shown}
            columns={columns}
            rowKey={(j) => j.key}
            selected={selected}
            onOpen={(j) => setSelected(j.key)}
            initialSort={[{ id: "found", desc: true }]}
            rowClass={(j) => (j.open ? "" : "is-closed")}
          />
        )}
      </div>
      <Drawer open={Boolean(job)} onClose={() => setSelected(null)} title={job?.title ?? ""}>
        {job && <JobDetails job={job} meta={meta.data} onEdit={(changes) => onEdit({ key: job.key, changes })} />}
      </Drawer>
    </div>
  );
}

function Select({ label, value, options, onChange, labels }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void; labels?: Record<string, string>;
}) {
  return (
    <label className="select">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Any</option>
        {options.map((o) => (
          <option key={o} value={o}>{labels?.[o] ?? o}</option>
        ))}
      </select>
    </label>
  );
}

function StatusSelect({ job, meta, onEdit, label }: {
  job: Job; meta?: Meta; onEdit: (c: JobEdit) => void; label?: boolean;
}) {
  const [bg, fg] = meta?.colors.status[job.status] ?? [];
  return (
    <select className="inline-select" value={job.status} aria-label={label ? undefined : `Status for ${job.title}`}
      style={bg ? { backgroundColor: hex(bg), color: hex(fg) } : undefined}
      onChange={(e) => onEdit({ status: e.target.value })}>
      {(meta?.statuses ?? [job.status]).map((s) => <option key={s} value={s}>{s}</option>)}
    </select>
  );
}

function OutreachSelect({ job, meta, onEdit, label }: {
  job: Job; meta?: Meta; onEdit: (c: JobEdit) => void; label?: boolean;
}) {
  const color = meta?.colors.outreach[job.outreach];
  return (
    <select className={`inline-select ${job.outreach ? "" : "is-empty"}`} value={job.outreach}
      aria-label={label ? undefined : `Outreach for ${job.title}`}
      style={color ? { backgroundColor: hex(color), color: "#1f2a3d" } : undefined}
      onChange={(e) => onEdit({ outreach: e.target.value })}>
      <option value="">None</option>
      {(meta?.outreach ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
    </select>
  );
}

function jobColumns(meta: Meta | undefined, onEdit: (key: string, c: JobEdit) => void): ColumnDef<Job, any>[] { // eslint-disable-line @typescript-eslint/no-explicit-any
  const regionColor = new Map(meta?.regions.map((r) => [r.name, r.color]) ?? []);
  return [
    {
      id: "found", header: "Found", accessorFn: (j) => j.found ?? "", size: 72,
      cell: (c) => <span className="quiet" title={when(c.row.original.found)}>{day(c.row.original.found)}</span>,
    },
    {
      id: "company", header: "Company", accessorFn: (j) => j.company.toLowerCase(), size: 140,
      cell: (c) => (
        <span className="company-cell">
          {c.row.original.company}
          {c.row.original.tier && <span className={`tier tier-${c.row.original.tier}`}
            title={`Company score ${c.row.original.score}`}>{c.row.original.tier}</span>}
        </span>
      ),
    },
    {
      id: "title", header: "Title", accessorFn: (j) => j.title.toLowerCase(),
      cell: (c) => (
        <span className="title-cell">
          <External href={c.row.original.url}>{c.row.original.title}</External>
          <Flags job={c.row.original} />
        </span>
      ),
    },
    {
      id: "fit", header: "Fit", accessorFn: (j) => j.fit?.score ?? -1, size: 104, sortDescFirst: true,
      cell: (c) => <FitBadge fit={c.row.original.fit} />,
    },
    {
      id: "pay", header: "Pay", accessorFn: (j) => j.pay?.max ?? -1, size: 132, sortDescFirst: true,
      cell: (c) => <span className="num pay" title={c.row.original.pay?.text}>{payText(c.row.original.pay)}</span>,
    },
    {
      id: "region", header: "Region", accessorKey: "region", size: 140,
      cell: (c) => <Chip color={regionColor.get(c.row.original.region)}>{c.row.original.region}</Chip>,
    },
    {
      id: "status", header: "Status", accessorKey: "status", size: 124,
      cell: (c) => <StatusSelect job={c.row.original} meta={meta} onEdit={(ch) => onEdit(c.row.original.key, ch)} />,
    },
    {
      id: "outreach", header: "Outreach", accessorKey: "outreach", size: 116,
      cell: (c) => <OutreachSelect job={c.row.original} meta={meta} onEdit={(ch) => onEdit(c.row.original.key, ch)} />,
    },
  ];
}

function JobDetails({ job, meta, onEdit }: { job: Job; meta?: Meta; onEdit: (c: JobEdit) => void }) {
  const [notes, setNotes] = useState(job.notes);
  useEffect(() => setNotes(job.notes), [job.key, job.notes]);
  const notesChanged = notes !== job.notes;
  const description = useQuery({
    queryKey: ["description", job.key],
    queryFn: () => api<{ text: string | null }>(`description?key=${encodeURIComponent(job.key)}`),
    staleTime: Infinity,
  });
  const regionColor = meta?.regions.find((r) => r.name === job.region)?.color;
  return (
    <div className="details">
      <p className="details-sub">
        {job.company}
        {job.tier && <span className={`tier tier-${job.tier}`}>{job.tier}</span>}
        {job.score && <span className="quiet"> company score {job.score}</span>}
      </p>
      <div className="details-chips">
        <Chip color={regionColor}>{job.region}</Chip>
        {job.level !== "Unlabelled" && <Chip color={meta?.colors.level[job.level]}>{job.level}</Chip>}
        {!job.open && <Chip>Closed {day(job.closed)}</Chip>}
      </div>
      <div className="edit-grid">
        <label>Status <StatusSelect job={job} meta={meta} onEdit={onEdit} label /></label>
        <label>Outreach <OutreachSelect job={job} meta={meta} onEdit={onEdit} label /></label>
      </div>
      <label className="notes-field">
        <span>Your notes</span>
        <textarea value={notes} rows={3} placeholder="Who referred you, salary range, next step"
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => notesChanged && onEdit({ notes })} />
      </label>
      {notesChanged && <p className="hint">Saved when you click outside the box.</p>}
      <FitPanel job={job} />
      <dl className="facts">
        {job.pay && <div><dt>Pay</dt><dd>{payText(job.pay)} <span className="quiet">({job.pay.text})</span></dd></div>}
        {job.workplace && <div><dt>Where you work</dt><dd>{WORKPLACES[job.workplace]}</dd></div>}
        {job.years != null && (
          <div><dt>Experience</dt><dd>Asks for {job.years}+ years{job.years_over ? ", more than you set in Settings" : ""}</dd></div>
        )}
        {job.relocation != null && <div><dt>Relocation</dt><dd>{job.relocation ? "Help offered" : "Not offered"}</dd></div>}
        {(job.work_auth ?? []).length > 0 && (
          <div><dt>Work permit</dt><dd>{job.work_auth!.map((a) => a.label).join(", ")}</dd></div>
        )}
        {(job.reposted || job.long_open) && (
          <div><dt>History</dt><dd>{[job.reposted && "Posted here before under the same title",
            job.long_open && `Open for ${job.open_days} days`].filter(Boolean).join(". ")}</dd></div>
        )}
        <div><dt>Location</dt><dd>{job.locations.join("; ") || "Not listed"}</dd></div>
        <div><dt>Posted</dt><dd>{job.posted ? `${when(job.posted)} (${ago(job.posted)})` : "No date on the posting"}</dd></div>
        <div><dt>Found</dt><dd>{when(job.found)}</dd></div>
        <div><dt>Stack match</dt><dd>{job.stack.join(", ") || "None"}</dd></div>
        <div><dt>Board</dt><dd>{job.provider}</dd></div>
      </dl>
      <p className="actions">
        <External href={job.url}>Open the posting</External>
      </p>
      <ResumePanel job={job} />
      <PeoplePanel job={job} />
      <h3>Description</h3>
      {description.isLoading && <p className="quiet">Loading the description</p>}
      <ErrorNote error={description.error} />
      {description.data && (description.data.text
        ? <div className="description">{tidy(description.data.text)}</div>
        : <p className="quiet">The description was not downloaded. Open the posting to read it.</p>)}
    </div>
  );
}

// ------------------------------------------------------------------ Claude and people you know

const STATUS_WORD = { MET: "Have it", PARTIAL: "Partly", MISSING: "Missing" } as const;

function useDetails(job: Job) {
  return useQuery({
    queryKey: ["job-details", job.key, job.fit?.score, job.tailored, job.connections],
    queryFn: () => api<JobDetailsData>(`jobs/details?key=${encodeURIComponent(job.key)}`),
  });
}

function useAi() {
  return useQuery({ queryKey: ["ai"], queryFn: () => api<AiStatus>("ai"), staleTime: 30_000 });
}

function useClaude(job: Job, what: "fit" | "tailor") {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      await send<Fit | Tailored>(`jobs/${what}?key=${encodeURIComponent(job.key)}`, "POST");
      await client.invalidateQueries({ queryKey: ["jobs"] });
      client.invalidateQueries({ queryKey: ["ai"] });
      client.invalidateQueries({ queryKey: ["job-details", job.key] });
      toast(what === "fit" ? `Fit scored for ${job.title}.` : `Tailored resume saved for ${job.company}.`, "good");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Claude could not do that.", "bad");
    } finally {
      setBusy(false);
    }
  }
  return { busy, run };
}

function FitPanel({ job }: { job: Job }) {
  const details = useDetails(job);
  const ai = useAi();
  const { busy, run } = useClaude(job, "fit");
  const fit = details.data?.fit;
  return (
    <section className="ai-panel" aria-labelledby="fit-title">
      <div className="ai-head">
        <h3 id="fit-title">Fit for you</h3>
        {job.fit && <FitBadge fit={job.fit} large />}
      </div>
      {fit ? (
        <>
          <p className="fit-summary">{fit.summary}</p>
          {fit.requirements.length > 0 && (
            <ul className="reqs">
              {fit.requirements.map((r) => (
                <li key={r.text} className={`req req-${r.status.toLowerCase()}`}>
                  <span className="req-status">{STATUS_WORD[r.status]}</span>{r.text}
                </li>
              ))}
            </ul>
          )}
          {fit.gaps.length > 0 && <p className="hint">Gaps: {fit.gaps.join("; ")}</p>}
        </>
      ) : (
        <p className="hint">
          {ai.data && !ai.data.key_set
            ? "Add your Anthropic key in Settings, Keys and accounts, and Claude scores how well each match fits your resume."
            : "Claude reads the posting and your resume and says Apply, Stretch or Skip, with what you have and what is missing."}
        </p>
      )}
      <button className="button" onClick={run} disabled={busy || (ai.data ? !ai.data.key_set : false)}>
        {busy ? "Reading the posting" : fit ? "Score again" : "Score my fit"}
      </button>
    </section>
  );
}

function ResumePanel({ job }: { job: Job }) {
  const details = useDetails(job);
  const ai = useAi();
  const { busy, run } = useClaude(job, "tailor");
  const t = details.data?.tailored;
  const open = (path: string, folder = false) =>
    send("files/open", "POST", { path, folder }).catch((e) => toast(e instanceof Error ? e.message : "Could not open it.", "bad"));
  const ready = ai.data?.key_set && ai.data.resume_tex_found;
  return (
    <section className="ai-panel" aria-labelledby="resume-title">
      <div className="ai-head"><h3 id="resume-title">Tailored resume</h3></div>
      {t ? (
        <>
          <p className="hint">Made {new Date(t.at).toLocaleDateString()}. {t.notes}</p>
          <p className="file-actions">
            {t.pdf && <button className="button" onClick={() => open(t.pdf!)}>Open the PDF</button>}
            <button className="button" onClick={() => open(t.tex)}>Open the .tex</button>
            <button className="text-button" onClick={() => open(t.tex, true)}>Show in folder</button>
          </p>
          {t.problem && <p className="hint">{t.problem}</p>}
          {t.numbers_to_find.length > 0 && (
            <div className="numbers">
              <p className="hint">Numbers that would make it stronger, if you have them:</p>
              <ul>{t.numbers_to_find.map((x) => <li key={x}>{x}</li>)}</ul>
            </div>
          )}
          {t.pdf && <p className="hint">Outreach attaches this PDF for {job.company}.</p>}
        </>
      ) : (
        <p className="hint">
          {ai.data && !ai.data.resume_tex_found
            ? `Put your base LaTeX resume in the Job Watcher folder as ${ai.data.resume_tex} and Claude rewrites it for this posting.`
            : "Claude rewrites your base LaTeX resume for this posting, using only facts already in it, and saves a .tex (and a PDF when LaTeX is installed)."}
        </p>
      )}
      <button className="button" onClick={run} disabled={busy || !ready}>
        {busy ? "Writing your resume (about a minute)" : t ? "Tailor again" : "Tailor my resume"}
      </button>
    </section>
  );
}

function PeoplePanel({ job }: { job: Job }) {
  const details = useDetails(job);
  const people = details.data?.people ?? [];
  if (!job.connections || !people.length) return null;
  return (
    <section className="ai-panel" aria-labelledby="people-title">
      <div className="ai-head"><h3 id="people-title">People you know at {job.company}</h3></div>
      <ul className="people">
        {people.map((p) => (
          <li key={p.url || p.first + p.last}>
            {p.url ? <External href={p.url}>{p.first} {p.last}</External> : `${p.first} ${p.last}`}
            <span className="quiet"> {p.position}</span>
          </li>
        ))}
      </ul>
      <p className="hint">A referral from someone you know beats a cold application. Ask before the recruiter email.</p>
    </section>
  );
}

const tidy = (text: string) => text.replace(/[ \t\u00a0]{2,}/g, " ").replace(/\n{3,}/g, "\n\n").trim();

// ------------------------------------------------------------------ all postings

export function Postings() {
  const [q, setQ] = useState("");
  const [where, setWhere] = useState("");
  const [status, setStatus] = useState("open");
  const [page, setPage] = useState(1);
  const [debounced, setDebounced] = useState({ q: "", where: "" });
  useEffect(() => {
    const t = window.setTimeout(() => { setDebounced({ q, where }); setPage(1); }, 250);
    return () => window.clearTimeout(t);
  }, [q, where]);
  const size = 100;
  const postings = useQuery({
    queryKey: ["postings", debounced, status, page],
    queryFn: () => api<PostingPage>(`postings?${new URLSearchParams({
      q: debounced.q, where: debounced.where, status, page: String(page), size: String(size),
    })}`),
    placeholderData: keepPreviousData,
  });
  const data = postings.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / size)) : 1;

  const columns = useMemo<ColumnDef<PostingPage["rows"][number], any>[]>(() => [ // eslint-disable-line @typescript-eslint/no-explicit-any
    { id: "seen", header: "First seen", size: 96,
      cell: (c) => <span className="quiet" title={when(c.row.original.first_seen)}>{day(c.row.original.first_seen)}</span> },
    { id: "posted", header: "Posted", size: 84, cell: (c) => <span className="quiet">{day(c.row.original.posted)}</span> },
    { id: "company", header: "Company", size: 170, cell: (c) => c.row.original.company },
    { id: "title", header: "Title",
      cell: (c) => <External href={c.row.original.url}>{c.row.original.title}</External> },
    { id: "where", header: "Location", size: 240,
      cell: (c) => <span className="quiet">{c.row.original.locations.join("; ")}</span> },
    { id: "tracked", header: "In your tracker", size: 112,
      cell: (c) => (c.row.original.tracked ? <Chip color="DDEBF7">Tracked</Chip> : null) },
  ], []);

  return (
    <div className="screen">
      <div className="screen-main">
        <header className="screen-head">
          <h1>Jobs</h1>
          <Tabs active="postings" />
        </header>
        <div className="filters filter-row" role="search">
          <input className="search" type="search" placeholder="Search title or company" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search all saved postings" />
          <input className="search narrow" type="search" placeholder="Location, such as Vancouver" value={where}
            onChange={(e) => setWhere(e.target.value)} aria-label="Location" />
          <label className="select">
            <span>Show</span>
            <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="open">Open postings</option>
              <option value="closed">Closed postings</option>
              <option value="all">All postings</option>
            </select>
          </label>
        </div>
        <p className="count-line">
          {data ? `${n(data.total)} postings, newest first. Every posting from every board is kept here, matched or not.`
            : "Loading saved postings"}
        </p>
        <ErrorNote error={postings.error} />
        {data && (data.rows.length ? (
          <Table data={data.rows} columns={columns} rowKey={(p) => p.key} sortable={false} />
        ) : (
          <Empty title="No saved postings fit this search" />
        ))}
        {data && pages > 1 && (
          <div className="pager">
            <button className="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="num">Page {n(page)} of {n(pages)}</span>
            <button className="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        )}
      </div>
    </div>
  );
}
