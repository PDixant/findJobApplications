import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { api, send, type AiStatus, type Fit, type Job, type JobDetailsData, type JobEdit, type Meta, type PostingPage,
  type Tailored } from "../api";
import { Drawer, Empty, ErrorNote, External, Icon, PillSelect, SearchInput, Tabs, useDismiss, usePopSide } from "../components/bits";
import { FitBadge, Flags, payRange, payText, payUnit, WORKPLACES } from "../components/Fit";
import { toast } from "../live";
import { Table, TableSkeleton } from "../components/Table";
import { ago, day, n, plural, when } from "../format";
import { loadPrefs, savePrefs, useEditJob, useJobs, useMeta, useSummary } from "../queries";
import { go, useRoute } from "../router";
import { useRunActions } from "../components/RunControls";

/** Jobs: your matches, or every saved posting. The tabs share one header so the line can slide. */
export function JobsScreen({ view }: { view: "jobs" | "postings" }) {
  const jobs = useJobs();
  const summary = useSummary();
  return (
    <>
      <header className="page-head with-tabs">
        <h1>Jobs</h1>
        <Tabs label="Jobs views" active={view} onPick={(v) => go(v)} items={[
          { id: "jobs", label: "Your matches", count: jobs.data ? n(jobs.data.filter((j) => j.open).length) : "" },
          { id: "postings", label: "All saved postings", count: summary.data ? n(summary.data.postings.open) : "" },
        ]} />
      </header>
      <hr className="character" style={{ marginBottom: 32 }} />
      {view === "jobs" ? <Matches /> : <Postings />}
    </>
  );
}

// ------------------------------------------------------------------ your matches

type Filters = {
  text: string; regions: string[]; level: string; status: string; outreach: string; fit: string; workplace: string;
  only: string; closed: boolean;
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

function Matches() {
  const jobs = useJobs();
  const meta = useMeta();
  const route = useRoute();
  const picked = route.params.get("job");
  const [filters, setFilters] = useState<Filters>(() => loadPrefs("jobFilters", NO_FILTERS));
  const [selected, setSelected] = useState<string | null>(picked);
  useEffect(() => { if (picked) setSelected(picked); }, [picked]);
  useEffect(() => savePrefs("jobFilters", filters), [filters]);
  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const { start, busy } = useRunActions();

  const shown = useMemo(() => {
    const words = filters.text.toLowerCase().split(/\s+/).filter(Boolean);
    return (jobs.data ?? []).filter((j) => {
      if (!filters.closed && !j.open && j.key !== picked) return false;
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
  }, [jobs.data, filters, picked]);

  const editJob = useEditJob();
  const onEdit = editJob.mutate;
  const columns = useMemo(() => jobColumns(meta.data, (key, changes) => onEdit({ key, changes })), [meta.data, onEdit]);
  const job = jobs.data?.find((j) => j.key === selected) ?? null;
  const filtered = JSON.stringify({ ...filters, closed: false }) !== JSON.stringify(NO_FILTERS);
  const close = () => {
    setSelected(null);
    if (picked) history.replaceState(null, "", "#/jobs");
  };

  return (
    <>
      <div className="toolbar" role="search">
        <SearchInput value={filters.text} onChange={(text) => set({ text })} label="Search your matches"
          placeholder="Title, company, place or stack" />
        <RegionFilter regions={meta.data?.regions.map((r) => r.name) ?? []} value={filters.regions} onChange={(regions) => set({ regions })} />
        <PillSelect label="Fit" value={filters.fit} onChange={(fit) => set({ fit })} options={Object.keys(FIT_CHOICES)} labels={FIT_CHOICES} />
        <PillSelect label="Status" value={filters.status} onChange={(status) => set({ status })} options={meta.data?.statuses ?? []} />
        <PillSelect label="Outreach" value={filters.outreach} onChange={(outreach) => set({ outreach })}
          options={["None", ...(meta.data?.outreach ?? [])]} />
        <MoreFilters filters={filters} set={set} />
        <span className="toolbar-end">
          {jobs.data && <span className="num">{n(shown.length)} of {n(filters.closed ? jobs.data.length : jobs.data.filter((j) => j.open).length)}</span>}
          {filtered && <button className="text-button" onClick={() => setFilters({ ...NO_FILTERS, closed: filters.closed })}>Clear filters</button>}
        </span>
      </div>
      <ErrorNote error={jobs.error} />
      {!jobs.data ? <TableSkeleton /> : shown.length === 0 ? (
        jobs.data.length ? (
          <Empty title="Nothing fits these filters"
            action={<button className="button" onClick={() => setFilters({ ...NO_FILTERS, closed: filters.closed })}>Clear filters</button>}>
            Clear a filter, or turn on Show closed under More.
          </Empty>
        ) : (
          <Empty title="No matches yet"
            action={<button className="button primary" onClick={() => start("full")} disabled={busy}>Run now</button>}>
            Matches appear here after a run finds postings that fit your settings.
          </Empty>
        )
      ) : (
        <Table label="Your matches" data={shown} columns={columns} rowKey={(j) => j.key} selected={selected}
          onOpen={(j) => setSelected(j.key)} initialSort={[{ id: "found", desc: true }]}
          rowClass={(j) => (j.open ? "" : "is-closed")} />
      )}
      <Drawer open={Boolean(job)} onClose={close} title={job?.title ?? ""}
        sub={job ? [job.company, job.region, job.posted ? `posted ${ago(job.posted)}` : `found ${ago(job.found)}`].join(" · ") : null}
        foot={job && <JobFoot job={job} meta={meta.data} onEdit={(changes) => onEdit({ key: job.key, changes })} />}>
        {job && <JobDetails job={job} onEdit={(changes) => onEdit({ key: job.key, changes })} />}
      </Drawer>
    </>
  );
}

/** Regions are several at once, so they get their own small menu of checkboxes. */
function RegionFilter({ regions, value, onChange }: { regions: string[]; value: string[]; onChange: (v: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useDismiss(box, open, () => setOpen(false));
  const side = usePopSide(box, open);
  return (
    <div className={`filter-pop ${side}`} ref={box}>
      <span className={`pill-select ${value.length ? "is-set" : ""}`}>
        Region: <b>{value.length === 0 ? "Any" : value.length === 1 ? value[0] : `${value.length} regions`}</b>
        <button className="pill-hit" aria-expanded={open} aria-label="Region" onClick={() => setOpen((o) => !o)} />
      </span>
      {open && (
        <div className="popover" role="group" aria-label="Regions">
          <div className="checks">
            {regions.map((r) => (
              <label key={r} className="check">
                <input type="checkbox" checked={value.includes(r)}
                  onChange={(e) => onChange(e.target.checked ? [...value, r] : value.filter((x) => x !== r))} />
                {r}
              </label>
            ))}
          </div>
          {value.length > 0 && <button className="text-button" style={{ alignSelf: "flex-start" }} onClick={() => onChange([])}>Any region</button>}
        </div>
      )}
    </div>
  );
}

function MoreFilters({ filters, set }: { filters: Filters; set: (p: Partial<Filters>) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useDismiss(box, open, () => setOpen(false));
  const count = [filters.level, filters.workplace, filters.only].filter(Boolean).length + (filters.closed ? 1 : 0);
  const side = usePopSide(box, open);
  return (
    <div className={`filter-pop ${side}`} ref={box}>
      <span className={`pill-select ${count ? "is-set" : ""}`}>
        More{count ? <b>: {count}</b> : null}
        <button className="pill-hit" aria-expanded={open} aria-label="More filters" onClick={() => setOpen((o) => !o)} />
      </span>
      {open && (
        <div className="popover" role="group" aria-label="More filters">
          <label className="field-stack">Level
            <select value={filters.level} onChange={(e) => set({ level: e.target.value })}>
              <option value="">Any</option>
              {["Senior/Lead", "Mid", "Unlabelled"].map((o) => <option key={o}>{o}</option>)}
            </select>
          </label>
          <label className="field-stack">Where you work
            <select value={filters.workplace} onChange={(e) => set({ workplace: e.target.value })}>
              <option value="">Any</option>
              {Object.entries(WORKPLACES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="field-stack">Only
            <select value={filters.only} onChange={(e) => set({ only: e.target.value })}>
              <option value="">Any</option>
              {Object.keys(ONLY).map((o) => <option key={o}>{o}</option>)}
            </select>
          </label>
          <label className="switch">
            <input type="checkbox" checked={filters.closed} onChange={(e) => set({ closed: e.target.checked })} />
            Show closed postings
          </label>
        </div>
      )}
    </div>
  );
}

function StatusSelect({ job, meta, onEdit, quiet }: { job: Job; meta?: Meta; onEdit: (c: JobEdit) => void; quiet?: boolean }) {
  return (
    <select className={quiet ? "inline-select" : undefined} value={job.status} aria-label={`Status for ${job.title}`}
      onChange={(e) => onEdit({ status: e.target.value })}>
      {(meta?.statuses ?? [job.status]).map((s) => <option key={s} value={s}>{s}</option>)}
    </select>
  );
}

function OutreachSelect({ job, meta, onEdit, quiet }: { job: Job; meta?: Meta; onEdit: (c: JobEdit) => void; quiet?: boolean }) {
  return (
    <select className={quiet ? `inline-select ${job.outreach ? "" : "is-empty"}` : undefined} value={job.outreach}
      aria-label={`Outreach for ${job.title}`} onChange={(e) => onEdit({ outreach: e.target.value })}>
      <option value="">{quiet ? "None" : "No outreach"}</option>
      {(meta?.outreach ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
    </select>
  );
}

function jobColumns(meta: Meta | undefined, onEdit: (key: string, c: JobEdit) => void): ColumnDef<Job, any>[] { // eslint-disable-line @typescript-eslint/no-explicit-any
  return [
    {
      id: "found", header: "Found", accessorFn: (j) => j.found ?? "", size: 88,
      cell: (c) => <span className="quiet num" title={when(c.row.original.found)}>{day(c.row.original.found)}</span>,
    },
    {
      id: "title", header: "Title", accessorFn: (j) => j.title.toLowerCase(),
      cell: (c) => (
        <span className="cell-stack">
          <span className="cell-main wraps"><External href={c.row.original.url}>{c.row.original.title}</External></span>
          <Flags job={c.row.original} />
        </span>
      ),
    },
    {
      id: "company", header: "Company", accessorFn: (j) => j.company.toLowerCase(), size: 180,
      cell: (c) => (
        <span className="cell-stack" title={c.row.original.score ? `Company score ${c.row.original.score} (tier ${c.row.original.tier})` : undefined}>
          <span className="cell-main">{c.row.original.company}</span>
          <span className="cell-sub">{c.row.original.region}</span>
        </span>
      ),
    },
    {
      id: "fit", header: "Fit", accessorFn: (j) => j.fit?.score ?? -1, size: 124, sortDescFirst: true,
      cell: (c) => <FitBadge fit={c.row.original.fit} />,
    },
    {
      id: "pay", header: "Pay", accessorFn: (j) => j.pay?.max ?? -1, size: 120, sortDescFirst: true, meta: { num: true },
      cell: (c) => {
        const pay = c.row.original.pay;
        return pay ? (
          <span className="cell-stack" title={pay.text}>
            <span className="cell-main num">{payRange(pay)}</span>
            <span className="cell-sub">{payUnit(pay)}</span>
          </span>
        ) : null;
      },
    },
    {
      id: "status", header: "Status", accessorKey: "status", size: 128,
      cell: (c) => <StatusSelect quiet job={c.row.original} meta={meta} onEdit={(ch) => onEdit(c.row.original.key, ch)} />,
    },
    {
      id: "outreach", header: "Outreach", accessorKey: "outreach", size: 120,
      cell: (c) => <OutreachSelect quiet job={c.row.original} meta={meta} onEdit={(ch) => onEdit(c.row.original.key, ch)} />,
    },
  ];
}

/** The sheet's foot: the one thing to do next, anchored low, and where the application stands. */
function JobFoot({ job, meta, onEdit }: { job: Job; meta?: Meta; onEdit: (c: JobEdit) => void }) {
  return (
    <>
      <External href={job.url} className="button primary">Open the posting <Icon name="external" /></External>
      <StatusSelect job={job} meta={meta} onEdit={onEdit} />
      <OutreachSelect job={job} meta={meta} onEdit={onEdit} />
    </>
  );
}

function JobDetails({ job, onEdit }: { job: Job; onEdit: (c: JobEdit) => void }) {
  const [notes, setNotes] = useState(job.notes);
  useEffect(() => setNotes(job.notes), [job.key, job.notes]);
  const notesChanged = notes !== job.notes;
  const description = useQuery({
    queryKey: ["description", job.key],
    queryFn: () => api<{ text: string | null }>(`description?key=${encodeURIComponent(job.key)}`),
    staleTime: Infinity,
  });
  return (
    <div className="details">
      <FitPanel job={job} />
      <section className="sheet-section" aria-label="About the job">
        <dl className="facts">
          {job.pay && <div><dt>Pay</dt><dd>{payText(job.pay)} <span className="quiet">({job.pay.text})</span></dd></div>}
          {job.workplace && <div><dt>Where you work</dt><dd>{WORKPLACES[job.workplace]}</dd></div>}
          {job.years != null && (
            <div><dt>Experience</dt><dd>Asks for {job.years}+ years{job.years_over ? ", more than you set in Settings" : ""}</dd></div>
          )}
          {job.relocation != null && <div><dt>Relocation</dt><dd>{job.relocation ? "Help offered" : "Not offered"}</dd></div>}
          {(job.work_auth ?? []).length > 0 && <div><dt>Work permit</dt><dd>{job.work_auth!.map((a) => a.label).join(", ")}</dd></div>}
          {(job.reposted || job.long_open) && (
            <div><dt>History</dt><dd>{[job.reposted && "Posted here before under the same title",
              job.long_open && `Open for ${job.open_days} days`].filter(Boolean).join(". ")}</dd></div>
          )}
          <div><dt>Location</dt><dd>{job.locations.join("; ") || "Not listed"}</dd></div>
          <div><dt>Level</dt><dd>{job.level}</dd></div>
          <div><dt>Posted</dt><dd>{job.posted ? `${when(job.posted)} (${ago(job.posted)})` : "No date on the posting"}</dd></div>
          <div><dt>Found</dt><dd>{when(job.found)}</dd></div>
          <div><dt>Stack match</dt><dd>{job.stack.join(", ") || "None"}</dd></div>
          {job.score && <div><dt>Company score</dt><dd>{job.score} (tier {job.tier})</dd></div>}
          <div><dt>Board</dt><dd>{job.provider}</dd></div>
          {!job.open && <div><dt>Closed</dt><dd>{day(job.closed)}</dd></div>}
        </dl>
      </section>
      <section className="sheet-section" aria-labelledby="notes-title">
        <h3 id="notes-title">Your notes</h3>
        <textarea value={notes} rows={3} aria-labelledby="notes-title" placeholder="Who referred you, the salary they mentioned, the next step"
          onChange={(e) => setNotes(e.target.value)} onBlur={() => notesChanged && onEdit({ notes })} />
        {notesChanged && <p className="hint" style={{ marginTop: 8 }}>Saved when you click outside the box.</p>}
      </section>
      <ResumePanel job={job} />
      <PeoplePanel job={job} />
      <section className="sheet-section" aria-labelledby="desc-title">
        <h3 id="desc-title">Description</h3>
        {description.isLoading && <p className="hint">Loading the description</p>}
        <ErrorNote error={description.error} />
        {description.data && (description.data.text
          ? <div className="reading">{tidy(description.data.text)}</div>
          : <p className="hint">The description wasn't downloaded. Open the posting to read it.</p>)}
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ Claude and people you know

const REQ: Record<string, { icon: string; word: string }> = {
  MET: { icon: "check", word: "Have it" }, PARTIAL: { icon: "dash", word: "Partly" }, MISSING: { icon: "close", word: "Missing" },
};

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
      toast(e instanceof Error ? e.message : "Claude couldn't do that. Try again in a minute.", "bad");
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
    <section className="sheet-section" aria-labelledby="fit-title">
      <div className="fit-head">
        <div>
          <p className="eyebrow" id="fit-title">Fit for you</p>
          {job.fit ? (
            <p className="fit-score"><span className="t-display-m">{job.fit.score}</span><FitBadge fit={job.fit} score={false} /></p>
          ) : <p className="t-title-s" style={{ marginTop: 8 }}>Not scored yet</p>}
        </div>
        <button className="button small" onClick={run} disabled={busy || (ai.data ? !ai.data.key_set : false)}>
          {busy ? "Reading the posting" : fit ? "Score again" : "Score my fit"}
        </button>
      </div>
      {fit ? (
        <>
          <p className="t-body-s secondary">{fit.summary}</p>
          {fit.requirements.length > 0 && (
            <ul className="reqs">
              {fit.requirements.map((r) => (
                <li key={r.text} className={`req req-${r.status.toLowerCase()}`}>
                  <Icon name={REQ[r.status].icon} />
                  <span>{r.text}</span>
                  <span className="req-word">{REQ[r.status].word}</span>
                </li>
              ))}
            </ul>
          )}
          {fit.gaps.length > 0 && <p className="hint" style={{ marginTop: 16 }}>Gaps: {fit.gaps.join("; ")}</p>}
        </>
      ) : (
        <p className="hint">
          {ai.data && !ai.data.key_set
            ? "Add your Anthropic key in Settings, under Keys, and Claude scores how well each match fits your resume."
            : "Claude reads the posting and your resume and says Apply, Stretch or Skip, with what you have and what's missing."}
        </p>
      )}
    </section>
  );
}

function ResumePanel({ job }: { job: Job }) {
  const details = useDetails(job);
  const ai = useAi();
  const { busy, run } = useClaude(job, "tailor");
  const t = details.data?.tailored;
  const open = (path: string, folder = false) =>
    send("files/open", "POST", { path, folder }).catch((e) => toast(e instanceof Error ? e.message : "That file didn't open.", "bad"));
  const ready = ai.data?.key_set && ai.data.resume_tex_found;
  return (
    <section className="sheet-section" aria-labelledby="resume-title">
      <h3 id="resume-title">Tailored resume</h3>
      {t ? (
        <>
          <p className="t-body-s secondary">Made {new Date(t.at).toLocaleDateString()}. {t.notes}</p>
          <div className="file-actions">
            {t.pdf && <button className="button small" onClick={() => open(t.pdf!)}>Open the PDF</button>}
            <button className="button small" onClick={() => open(t.tex)}>Open the .tex</button>
            <button className="text-button" onClick={() => open(t.tex, true)}>Show in folder</button>
          </div>
          {t.problem && <p className="hint" style={{ marginTop: 16 }}>{t.problem}</p>}
          {t.numbers_to_find.length > 0 && (
            <div className="numbers" style={{ marginTop: 16 }}>
              <p className="hint">Numbers that would make it stronger, if you have them:</p>
              <ul>{t.numbers_to_find.map((x) => <li key={x}>{x}</li>)}</ul>
            </div>
          )}
          {t.pdf && <p className="hint" style={{ marginTop: 16 }}>Outreach attaches this PDF for {job.company}.</p>}
        </>
      ) : (
        <p className="hint">
          {ai.data && !ai.data.resume_tex_found
            ? `Put your base LaTeX resume in the Job Watcher folder as ${ai.data.resume_tex} and Claude rewrites it for this posting.`
            : "Claude rewrites your base LaTeX resume for this posting, using only facts already in it, and saves a .tex (and a PDF when LaTeX is installed)."}
        </p>
      )}
      <div className="file-actions">
        <button className="button small" onClick={run} disabled={busy || !ready}>
          {busy ? "Writing your resume, about a minute" : t ? "Tailor again" : "Tailor my resume"}
        </button>
      </div>
    </section>
  );
}

function PeoplePanel({ job }: { job: Job }) {
  const details = useDetails(job);
  const people = details.data?.people ?? [];
  if (!job.connections || !people.length) return null;
  return (
    <section className="sheet-section" aria-labelledby="people-title">
      <h3 id="people-title">People you know at {job.company}</h3>
      <ul className="people-list">
        {people.map((p) => (
          <li key={p.url || p.first + p.last}>
            {p.url ? <External href={p.url}>{p.first} {p.last}</External> : `${p.first} ${p.last}`}
            <span className="quiet"> {p.position}</span>
          </li>
        ))}
      </ul>
      <p className="hint" style={{ marginTop: 16 }}>A referral from someone you know beats a cold application. Ask before the recruiter email.</p>
    </section>
  );
}

const tidy = (text: string) => text.replace(/[ \t ]{2,}/g, " ").replace(/\n{3,}/g, "\n\n").trim();

// ------------------------------------------------------------------ all postings

function Postings() {
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
    { id: "seen", header: "First seen", size: 104,
      cell: (c) => <span className="quiet num" title={when(c.row.original.first_seen)}>{day(c.row.original.first_seen)}</span> },
    { id: "posted", header: "Posted", size: 96, cell: (c) => <span className="quiet num">{day(c.row.original.posted)}</span> },
    { id: "company", header: "Company", size: 180, cell: (c) => <span className="cell-main">{c.row.original.company}</span> },
    { id: "title", header: "Title",
      cell: (c) => <span className="cell-main"><External href={c.row.original.url}>{c.row.original.title}</External></span> },
    { id: "where", header: "Location", size: 240, cell: (c) => <span className="quiet">{c.row.original.locations.join("; ")}</span> },
    { id: "tracked", header: "Tracker", size: 104, cell: (c) => (c.row.original.tracked ? <span className="badge">Tracked</span> : null) },
  ], []);

  return (
    <>
      <div className="toolbar" role="search">
        <SearchInput value={q} onChange={setQ} label="Search all saved postings" placeholder="Title or company" />
        <SearchInput value={where} onChange={setWhere} label="Location" placeholder="Location, such as Vancouver" />
        <div className="segmented" role="group" aria-label="Which postings">
          {[["open", "Open"], ["closed", "Closed"], ["all", "All"]].map(([v, label]) => (
            <button key={v} aria-pressed={status === v} onClick={() => { setStatus(v); setPage(1); }}>{label}</button>
          ))}
        </div>
        <span className="toolbar-end">{data && <span className="num">{plural(data.total, "posting")}, newest first</span>}</span>
      </div>
      <p className="hint" style={{ marginBottom: 16 }}>Every posting from every board is kept here, matched or not.</p>
      <ErrorNote error={postings.error} />
      {!data ? <TableSkeleton /> : data.rows.length ? (
        <Table label="All saved postings" data={data.rows} columns={columns} rowKey={(p) => p.key} sortable={false} />
      ) : (
        <Empty small title="No saved postings fit this search">Try fewer words, or a nearby place.</Empty>
      )}
      {data && pages > 1 && (
        <div className="pager">
          <button className="button small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="num">Page {n(page)} of {n(pages)}</span>
          <button className="button small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </>
  );
}
