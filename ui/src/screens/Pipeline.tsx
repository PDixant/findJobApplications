import { useMemo, useState, type DragEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { api, send, type Job, type Pipeline as PipelineData } from "../api";
import { ErrorNote, External } from "../components/bits";
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
  Offer: "",
  Rejected: "",
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
    <div className="screen pipeline">
      <header className="screen-head">
        <h1>Pipeline</h1>
        <p className="lede">Where each application stands. Drag a card to another column, or pick from its menu.</p>
      </header>
      <ErrorNote error={jobs.error ?? stats.error} />
      {p && <Stats p={p} />}
      {p && p.follow_ups.length > 0 && <FollowUps p={p} />}
      <div className="board" role="list">
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
              {HELP[status] && <p className="col-help">{HELP[status]}</p>}
              <ul className="cards">
                {list.slice(0, limit).map((j) => (
                  <li key={j.key} className={`card ${dragging === j.key ? "is-dragging" : ""}`} draggable
                    onDragStart={(e) => { e.dataTransfer.setData("text/plain", j.key); setDragging(j.key); }}
                    onDragEnd={() => { setDragging(null); setOver(null); }}>
                    <External href={j.url}>{j.title}</External>
                    <span className="card-company">{j.company}</span>
                    <div className="card-foot">
                      {j.fit && <FitBadge fit={j.fit} />}
                      <span className="quiet">{cardAge(j)}</span>
                    </div>
                    <select className="card-move" value={j.status || "New"} aria-label={`Move ${j.title}`}
                      onChange={(e) => move(j.key, e.target.value)}>
                      {statuses.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </li>
                ))}
              </ul>
              {list.length > limit && (
                <button className="text-button" onClick={() => go("jobs")}>
                  {plural(list.length - limit, "more")} in Jobs
                </button>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

function cardAge(j: Job) {
  if (j.status === "New" || !j.status) return j.posted ? `posted ${day(j.posted)}` : `found ${day(j.found)}`;
  const d = since(j.applied);
  return d == null ? "" : d === 0 ? "applied today" : `applied ${plural(d, "day")} ago`;
}

function Stats({ p }: { p: PipelineData }) {
  const max = Math.max(1, ...p.applied_weeks.map((w) => w.applied));
  const week = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return (
    <section className="panel stats" aria-label="Your numbers">
      <div className="stat"><span className="stat-num num">{n(p.applied_total)}</span><span>applied</span></div>
      <div className="stat">
        <span className="stat-num num">{p.response_rate == null ? "None yet" : `${p.response_rate}%`}</span>
        <span>heard back ({n(p.heard_back)})</span>
      </div>
      <div className="stat"><span className="stat-num num">{n(p.by_status.Interviewing ?? 0)}</span><span>interviewing</span></div>
      <div className="stat"><span className="stat-num num">{n(p.fit.APPLY)}</span><span>open matches Claude says apply to</span></div>
      <figure className="weeks">
        <figcaption>Applications per week</figcaption>
        <div className="week-bars" aria-hidden="true">
          {p.applied_weeks.map((w) => (
            <div key={w.week} className="week" title={`Week of ${week(w.week)}: ${plural(w.applied, "application")}`}>
              {w.applied > 0 && <div className="week-bar" style={{ height: `${(w.applied / max) * 100}%` }} />}
            </div>
          ))}
        </div>
        <div className="week-axis" aria-hidden="true">
          <span>{week(p.applied_weeks[0].week)}</span>
          <span>This week</span>
        </div>
        <table className="sr-only">
          <caption>Applications per week</caption>
          <tbody>{p.applied_weeks.map((w) => <tr key={w.week}><th>{week(w.week)}</th><td>{w.applied}</td></tr>)}</tbody>
        </table>
      </figure>
    </section>
  );
}

function FollowUps({ p }: { p: PipelineData }) {
  const client = useQueryClient();
  const done = (key: string) => send(`jobs/followed-up?key=${encodeURIComponent(key)}`, "POST")
    .then(() => client.invalidateQueries({ queryKey: ["pipeline"] }))
    .catch((e) => toast(e instanceof Error ? e.message : "Not saved.", "bad"));
  return (
    <section className="panel follow-ups" aria-labelledby="follow-title">
      <h2 id="follow-title">Time to follow up</h2>
      <ul>
        {p.follow_ups.slice(0, 8).map((f) => (
          <li key={f.key + f.kind}>
            <strong>{f.company}</strong> <span className="quiet">{f.title}</span>
            <span className="follow-why">
              {f.kind === "application"
                ? `Applied ${plural(f.days, "day")} ago with no news. A short note to the recruiter helps.`
                : `Emailed ${plural(f.days, "day")} ago with no reply. Outreach has a follow-up draft ready.`}
            </span>
            {f.kind === "email"
              ? <button className="text-button" onClick={() => go("outreach")}>Open Outreach</button>
              : <button className="text-button" onClick={() => done(f.key)}>I followed up</button>}
          </li>
        ))}
      </ul>
    </section>
  );
}
