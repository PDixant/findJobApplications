import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Job, type ScheduleStatus } from "../api";
import { CompanyStatus } from "../components/BoardMap";
import { Empty, ErrorNote, Icon, Meter, Skeleton } from "../components/bits";
import { ago, fromRun, n, plural, when, withinDay } from "../format";
import { useLive } from "../live";
import { useCompanies, useJobs, useSummary } from "../queries";
import { go } from "../router";
import { FitBadge } from "../components/Fit";
import { useRunActions } from "../components/RunControls";
import { usePipeline } from "./Pipeline";

/** A number that counts to its new value in 360 ms, in tabular figures so nothing jitters. */
function useCountTo(value: number | undefined) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    if (value == null) return;
    const start = from.current;
    from.current = value;
    if (start == null || start === value || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(value);
      return;
    }
    const t0 = performance.now();
    let frame = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / 360);
      const eased = 1 - Math.pow(1 - k, 3);
      setShown(Math.round(start + (value - start) * eased));
      if (k < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return shown;
}

export function Dashboard() {
  const summary = useSummary();
  const jobs = useJobs();
  const companies = useCompanies();
  const live = useLive();
  const schedule = useQuery({ queryKey: ["schedule", live.version], queryFn: () => api<ScheduleStatus>("schedule") });
  const s = summary.data;
  const sch = schedule.data;

  // Your latest run, and the rest of the last day, kept apart: run at night, run again in the
  // morning, and the morning's finds sit on top.
  const lastRun = s?.last_run ?? null;
  const { latestRun, earlier, latest } = useMemo(() => {
    const open = (jobs.data ?? []).filter((j) => j.open);
    const byFound = [...open].sort((a, b) => (b.found ?? "").localeCompare(a.found ?? ""));
    const latestRun = lastRun ? byFound.filter((j) => fromRun(j.found, lastRun)) : [];
    const earlier = byFound.filter((j) => withinDay(j.found) && !latestRun.includes(j));
    return { latestRun, earlier, latest: byFound.slice(0, 8) };
  }, [jobs.data, lastRun]);
  const openCount = useCountTo(s?.jobs.open);
  const dayTotal = latestRun.filter((j) => withinDay(j.found)).length + earlier.length;
  const next = sch?.enabled && !sch.paused && sch.next ? `Next automatic run ${when(sch.next)}`
    : sch?.paused ? "Automatic runs are paused" : sch && !sch.enabled ? "Automatic runs are off" : "";

  return (
    <div className="dashboard">
      <section className="hero" aria-label="Your matches">
        <div className="hero-figure">
          <span className="eyebrow">Open matches</span>
          {s ? <span className="t-figure">{n(openCount ?? s.jobs.open)}</span> : <Skeleton width={180} height={64} />}
          <p className="hero-change">
            {!jobs.data || !s ? <Skeleton width={360} height={24} />
              : !lastRun ? "No runs yet. Press Run now to check every company."
              : <>
                  {latestRun.length ? <><span className="up">▲ +{n(latestRun.length)}</span> from your last run, {when(lastRun)}.</>
                    : <>Nothing new from your last run, {when(lastRun)}.</>}
                  {" "}<span className="quiet">{plural(dayTotal, "new match", "new matches")} in the last day.</span>
                </>}
          </p>
        </div>
        {next && <div className="hero-meta"><span>{next}</span></div>}
      </section>
      <hr className="character" />
      <ErrorNote error={summary.error} />

      {companies.data && (
        <CompanyStatus companies={companies.data}
          onPickResult={(r) => go("companies", { result: r })}
          onPickCompany={(c) => go("companies", { c })} />
      )}
      {!companies.data && <Skeleton height={180} style={{ borderRadius: 12 }} />}

      <div className="dash-grid">
        <section className="card" aria-labelledby="matches-title">
          <header className="card-head">
            <h2 id="matches-title">{latestRun.length || earlier.length ? "New matches" : "Latest matches"}</h2>
            <button className="text-button" onClick={() => go("jobs")}>All matches</button>
          </header>
          {!jobs.data ? <MatchList jobs={[]} loading /> : latestRun.length || earlier.length ? (
            <>
              {lastRun && (
                <MatchGroup title="From your last run" when={when(lastRun)} jobs={latestRun} limit={10}
                  empty="Nothing new this time. Everything below was already here." />
              )}
              {earlier.length > 0 && (
                <MatchGroup title="Earlier in the last day" jobs={earlier} limit={latestRun.length ? 6 : 10} />
              )}
            </>
          ) : <MatchList jobs={latest} loading={false} />}
        </section>

        <div className="side-stack">
          <NextUp jobs={jobs.data ?? []} />
          <section className="card" aria-labelledby="month-title">
            <header className="card-head"><h2 id="month-title">This month</h2></header>
            {s ? (
              <>
                <Meter label={`Web searches (${s.usage.search.provider})`} used={s.usage.search.used} limit={s.usage.search.limit}
                  note={s.usage.search.key_set ? undefined : "No search key, so finding new companies is off."} />
                <Meter label="Hunter credits" used={s.usage.hunter.used} limit={s.usage.hunter.limit}
                  note={s.usage.hunter.key_set ? undefined : "No Hunter key yet."} />
                <dl className="facts tight" style={{ marginTop: 24 }}>
                  <div><dt>Postings saved</dt><dd>{n(s.postings.open)} open</dd></div>
                  <div><dt>Companies watched</dt><dd>{n(s.companies.enabled)}</dd></div>
                  <div><dt>Matches found, last 24 h</dt><dd>{n(s.jobs.new_24h)}</dd></div>
                  {s.schedule.runs_per_day ? (
                    <div><dt>Web searches</dt><dd>{s.schedule.runs_per_day === 1 ? "Once a day" : `${s.schedule.runs_per_day} a day`}</dd></div>
                  ) : null}
                </dl>
              </>
            ) : <Skeleton height={160} />}
          </section>
        </div>
      </div>
    </div>
  );
}

/** What to do next: follow-ups that are due, and strong fits you haven't acted on. */
function NextUp({ jobs }: { jobs: Job[] }) {
  const pipeline = usePipeline();
  const best = jobs.filter((j) => j.open && j.fit?.verdict === "APPLY" && (j.status || "New") === "New")
    .sort((a, b) => (b.fit?.score ?? 0) - (a.fit?.score ?? 0)).slice(0, 4);
  const due = pipeline.data?.follow_ups ?? [];
  if (!best.length && !due.length) return null;
  return (
    <section className="card" aria-labelledby="todo-title">
      <header className="card-head"><h2 id="todo-title">Next up</h2></header>
      <ul className="rows">
        {due.length > 0 && (
          <li>
            <button className="row" onClick={() => go("pipeline")}>
              <span className="row-main"><span className="row-title">{plural(due.length, "follow-up")} due</span>
                <span className="row-sub">Applications and emails waiting on you</span></span>
              <Icon name="right" />
            </button>
          </li>
        )}
        {best.map((j) => (
          <li key={j.key}>
            <button className="row" onClick={() => go("jobs", { job: j.key })}>
              <span className="row-main"><span className="row-title">{j.title}</span><span className="row-sub">{j.company}</span></span>
              <span className="row-end"><span className="num secondary">{j.fit?.score}</span></span>
            </button>
          </li>
        ))}
      </ul>
      {best.length > 0 && <p className="hint" style={{ marginTop: 16 }}>Claude rates these Apply, and you haven't acted on them yet.</p>}
    </section>
  );
}

/** One group of new matches, under its own label, so your latest run stands apart from the rest of the day. */
function MatchGroup({ title, when: at, jobs, limit, empty }: {
  title: string; when?: string; jobs: Job[]; limit: number; empty?: string;
}) {
  return (
    <div className="match-group">
      <h3 className="group-label">
        <span>{title}</span>
        {at && <span className="quiet">{at}</span>}
        <span className="num">{n(jobs.length)}</span>
      </h3>
      {jobs.length ? <MatchList jobs={jobs.slice(0, limit)} loading={false} /> : <p className="hint group-empty">{empty}</p>}
      {jobs.length > limit && (
        <button className="text-button" style={{ marginTop: 8 }} onClick={() => go("jobs", { found: title.startsWith("From") ? "run" : "day" })}>
          {plural(jobs.length - limit, "more")} in Jobs
        </button>
      )}
    </div>
  );
}

function MatchList({ jobs, loading }: { jobs: Job[]; loading: boolean }) {
  const { start, busy } = useRunActions();
  if (loading) {
    return <div style={{ display: "grid", gap: 16 }}>{[0, 1, 2, 3].map((i) => <Skeleton key={i} height={40} />)}</div>;
  }
  if (!jobs.length) {
    return (
      <Empty small title="No open matches yet"
        action={<button className="button primary" onClick={() => start("full")} disabled={busy}>Run now</button>}>
        They appear here after a run finds postings that fit your settings.
      </Empty>
    );
  }
  return (
    <ul className="rows reveal">
      {jobs.map((j, i) => (
        <li key={j.key} style={{ ["--i" as string]: i }}>
          <button className="row" onClick={() => go("jobs", { job: j.key })}>
            <span className="row-main">
              <span className="row-title">{j.title}</span>
              <span className="row-sub">{[j.company, j.region, j.level === "Senior/Lead" ? "Senior/Lead" : ""].filter(Boolean).join(" · ")}</span>
            </span>
            <span className="row-end">
              {j.fit ? <FitBadge fit={j.fit} /> : null}
              <span className="row-sub" title={j.found ? `Found ${when(j.found)}` : undefined}>
                {j.posted ? `posted ${ago(j.posted)}` : `found ${ago(j.found)}`}
              </span>
            </span>
            <span className="row-chevron"><Icon name="right" /></span>
          </button>
        </li>
      ))}
    </ul>
  );
}
