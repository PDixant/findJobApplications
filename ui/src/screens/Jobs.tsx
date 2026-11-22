import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { api, type Job, type JobEdit, type Meta, type PostingPage } from "../api";
import { Chip, Drawer, Empty, ErrorNote, External } from "../components/bits";
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
  closed: boolean;
};
const NO_FILTERS: Filters = { text: "", regions: [], level: "", status: "", outreach: "", closed: false };

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

function Select({ label, value, options, onChange }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void;
}) {
  return (
    <label className="select">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Any</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
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
      id: "company", header: "Company", accessorFn: (j) => j.company.toLowerCase(), size: 150,
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
      cell: (c) => <External href={c.row.original.url}>{c.row.original.title}</External>,
    },
    {
      id: "region", header: "Region", accessorKey: "region", size: 150,
      cell: (c) => <Chip color={regionColor.get(c.row.original.region)}>{c.row.original.region}</Chip>,
    },
    {
      id: "level", header: "Level", accessorKey: "level", size: 100,
      cell: (c) => c.row.original.level === "Unlabelled" ? <span className="quiet">Unlabelled</span>
        : <Chip color={meta?.colors.level[c.row.original.level]}>{c.row.original.level}</Chip>,
    },
    {
      id: "stack", header: "Stack match", accessorFn: (j) => j.stack.length, size: 140,
      cell: (c) => <span className="quiet">{c.row.original.stack.join(", ")}</span>,
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
      <dl className="facts">
        <div><dt>Location</dt><dd>{job.locations.join("; ") || "Not listed"}</dd></div>
        <div><dt>Posted</dt><dd>{job.posted ? `${when(job.posted)} (${ago(job.posted)})` : "No date on the posting"}</dd></div>
        <div><dt>Found</dt><dd>{when(job.found)}</dd></div>
        <div><dt>Stack match</dt><dd>{job.stack.join(", ") || "None"}</dd></div>
        <div><dt>Board</dt><dd>{job.provider}</dd></div>
      </dl>
      <p className="actions">
        <External href={job.url}>Open the posting</External>
      </p>
      <h3>Description</h3>
      {description.isLoading && <p className="quiet">Loading the description</p>}
      <ErrorNote error={description.error} />
      {description.data && (description.data.text
        ? <div className="description">{tidy(description.data.text)}</div>
        : <p className="quiet">The description was not downloaded. Open the posting to read it.</p>)}
    </div>
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
