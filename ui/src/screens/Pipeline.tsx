import { useMemo, useState, type DragEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, send, type Job, type Pipeline as PipelineData } from "../api";
import { ErrorNote, External, Skeleton, useTip } from "../components/bits";
import { FitBadge } from "../components/Fit";
import { day, n, plural } from "../format";
import { toast, useLive } from "../live";
import { useEditJob, useJobs, useMeta } from "../queries";
import { go } from "../router";

const LIMIT: Record<string, number> = { New: 30, Skipped: 12, Rejected: 12 };
const HELP: Record<string, string> = {
  New: "Open matches you haven't acted on. Best fit first.",
  Applied: "Waiting to hear back.",
  Interviewing: "In conversation.",
  Skipped: "Not for you.",
};

export function usePipeline() {
  const live = useLive();
  return useQuery({ queryKey: ["pipeline", live.version], queryFn: () => api<PipelineData>("pipeline") });
}

const since = (iso: string | null | undefined) =>
  iso ? Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)) : null;

export function Pipeline() {
  const jobs = useJobs();
  const meta = useMeta();
  const stats = usePipeline();
  const edit = useEditJob();
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const statuses = meta.data?.statuses ?? ["New", "Applied", "Interviewing", "Offer", "Rejected", "Skipped"];

  const columns = useMemo(() => {
    const out: Record<string, Job[]> = Object.fromEntries(statuses.map((s) => [s, []]));
    for (const j of jobs.data ?? []) {
      const s = j.status || "New";
      if (s === "New" && !j.open) continue; // closed before you acted on it
      (out[s] ??= []).push(j);
    }
    out.New?.sort((a, b) => (b.fit?.score ?? -1) - (a.fit?.score ?? -1) || (b.found ?? "").localeCompare(a.found ?? ""));
    for (const s of statuses.filter((x) => x !== "New")) {
      out[s]?.sort((a, b) => (b.applied ?? b.found ?? "").localeCompare(a.applied ?? a.found ?? ""));
    }
    return out;
  }, [jobs.data, statuses]);

  const move = (key: string, status: string) => {
    const job = jobs.data?.find((j) => j.key === key);
    if (job && job.status !== status) edit.mutate({ key, changes: { status } });
  };
  const drop = (status: string) => (e: DragEvent) => {
    e.preventDefault();
    const key = e.dataTransfer.getData("text/plain") || dragging;
    if (key) move(key, status);
    setDragging(null);
    setOver(null);
  };

  const p = stats.data;
  return (
    <>
      <header className="page-head">
        <h1>Pipeline</h1>
        <p className="lede">Where each application stands. Drag a card to another column, or pick from its menu.</p>
      </header>
      <hr className="character" style={{ marginBottom: 48 }} />
      <ErrorNote error={jobs.error ?? stats.error} />
      {p ? <Stats p={p} /> : <Skeleton height={96} style={{ marginBottom: 48 }} />}
      {p && p.follow_ups.length > 0 && <FollowUps p={p} />}
      <div className="board" role="list" aria-label="Applications by status">
        {statuses.map((status) => {
          const list = columns[status] ?? [];
          const limit = LIMIT[status] ?? Infinity;
          return (
            <section key={status} role="listitem" aria-labelledby={`col-${status}`}
              className={`board-col ${over === status ? "is-over" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setOver(status); }}
              onDragLeave={() => setOver((o) => (o === status ? null : o))}
              onDrop={drop(status)}>
              <header className="col-head">
                <h2 id={`col-${status}`}>{status}</h2>
                <span className="num">{n(list.length)}</span>
              </header>
              <p className="col-help">{HELP[status] ?? " "}</p>
              <ul className="cards">
                {list.slice(0, limit).map((j) => (
                  <li key={j.key} className={`pcard ${dragging === j.key ? "is-dragging" : ""}`} draggable
                    onDragStart={(e) => { e.dataTransfer.setData("text/plain", j.key); setDragging(j.key); }}
                    onDragEnd={() => { setDragging(null); setOver(null); }}>
                    <External href={j.url}>{j.title}</External>
                    <span className="pcard-company">{j.company}</span>
                    <div className="pcard-foot">
                      {j.fit && <FitBadge fit={j.fit} />}
                      <span>{cardAge(j)}</span>
                    </div>
                    <select className="inline-select" value={j.status || "New"} aria-label={`Move ${j.title}`}
                      onChange={(e) => move(j.key, e.target.value)}>
                      {statuses.map((s) => <option key={s} value={s}>{s === (j.status || "New") ? `In ${s}` : `Move to ${s}`}</option>)}
                    </select>
                  </li>
                ))}
              </ul>
              {list.length > limit && (
                <button className="text-button" style={{ marginTop: 12, marginLeft: 4 }} onClick={() => go("jobs")}>
                  {plural(list.length - limit, "more")} in Jobs
                </button>
              )}
            </section>
          );
        })}
      </div>
    </>
  );
}

function cardAge(j: Job) {
  if (j.status === "New" || !j.status) return j.posted ? `posted ${day(j.posted)}` : `found ${day(j.found)}`;
  const d = since(j.applied);
  return d == null ? "" : d === 0 ? "applied today" : `applied ${plural(d, "day")} ago`;
}

/** Low and wide: four numbers in the serif, then applications per week in the screen's paint. */
function Stats({ p }: { p: PipelineData }) {
  const tip = useTip();
  const max = Math.max(1, ...p.applied_weeks.map((w) => w.applied));
  const week = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return (
    <section className="stats" aria-label="Your numbers">
      <div className="stat"><span className="eyebrow">Applied</span><span className="t-display-m">{n(p.applied_total)}</span><small>in total</small></div>
      <div className="stat">
        <span className="eyebrow">Heard back</span>
        <span className="t-display-m">{p.response_rate == null ? "None" : `${p.response_rate}%`}</span>
        <small>{plural(p.heard_back, "reply", "replies")} so far</small>
      </div>
      <div className="stat"><span className="eyebrow">Interviewing</span><span className="t-display-m">{n(p.by_status.Interviewing ?? 0)}</span><small>right now</small></div>
      <div className="stat"><span className="eyebrow">Rated apply</span><span className="t-display-m">{n(p.fit.APPLY)}</span><small>open matches</small></div>
      <figure className="weeks">
        <figcaption className="eyebrow">Applications per week</figcaption>
        <div className="week-bars" onMouseLeave={tip.hide}>
          {p.applied_weeks.map((w) => (
            <div key={w.week} className="week"
              onMouseMove={(e) => tip.show(e, <><strong>{plural(w.applied, "application")}</strong>Week of {week(w.week)}</>)}>
              {w.applied > 0 && <div className="week-bar" style={{ height: `${(w.applied / max) * 100}%` }} />}
            </div>
          ))}
        </div>
        <div className="week-axis" aria-hidden="true">
          <span>{week(p.applied_weeks[0].week)}</span>
          <span>This week, {n(p.applied_weeks[p.applied_weeks.length - 1].applied)}</span>
        </div>
        <table className="sr-only">
          <caption>Applications per week</caption>
          <tbody>{p.applied_weeks.map((w) => <tr key={w.week}><th>{week(w.week)}</th><td>{w.applied}</td></tr>)}</tbody>
        </table>
        {tip.node}
      </figure>
    </section>
  );
}

function FollowUps({ p }: { p: PipelineData }) {
  const client = useQueryClient();
  const done = (key: string) => send(`jobs/followed-up?key=${encodeURIComponent(key)}`, "POST")
    .then(() => client.invalidateQueries({ queryKey: ["pipeline"] }))
    .catch((e) => toast(e instanceof Error ? e.message : "That didn't save.", "bad"));
  return (
    <section className="card follow-ups" aria-labelledby="follow-title">
      <header className="card-head"><h2 id="follow-title">Time to follow up</h2></header>
      <ul className="rows">
        {p.follow_ups.slice(0, 8).map((f) => (
          <li key={f.key + f.kind} className="row">
            <span className="row-main">
              <span className="row-title">{f.company} <span className="quiet" style={{ fontWeight: 400 }}>{f.title}</span></span>
              <span className="row-sub" style={{ whiteSpace: "normal" }}>
                {f.kind === "application"
                  ? `Applied ${plural(f.days, "day")} ago with no news. A short note to the recruiter helps.`
                  : `Emailed ${plural(f.days, "day")} ago with no reply. Outreach has a follow-up draft ready.`}
              </span>
            </span>
            {f.kind === "email"
              ? <button className="button small" onClick={() => go("outreach")}>Open Outreach</button>
              : <button className="button small" onClick={() => done(f.key)}>I followed up</button>}
          </li>
        ))}
      </ul>
    </section>
  );
}
