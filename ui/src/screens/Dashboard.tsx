import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Job, type Meta, type ScheduleStatus } from "../api";
import { BoardMap } from "../components/BoardMap";
import { Chip, ErrorNote, External, Meter } from "../components/bits";
import { phaseText } from "../components/RunControls";
import { ago, minutes, n, plural, when } from "../format";
import { useLive } from "../live";
import { PREVIOUS_VISIT, useCompanies, useJobs, useMeta, useSummary } from "../queries";
import { go } from "../router";

export function Dashboard() {
  const summary = useSummary();
  const jobs = useJobs();
  const companies = useCompanies();
  const meta = useMeta();
  const live = useLive();
  const run = live.run;
  const running = run?.status === "running";
  const busyElsewhere = Boolean(summary.data?.busy_elsewhere);

  const since = PREVIOUS_VISIT ?? new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const { fresh, latest } = useMemo(() => {
    const open = (jobs.data ?? []).filter((j) => j.open);
    const byFound = [...open].sort((a, b) => (b.found ?? "").localeCompare(a.found ?? ""));
    return { fresh: byFound.filter((j) => (j.found ?? "") > since), latest: byFound.slice(0, 8) };
  }, [jobs.data, since]);

  const schedule = useQuery({ queryKey: ["schedule", live.version], queryFn: () => api<ScheduleStatus>("schedule") });
  const s = summary.data;
  const sch = schedule.data;
  const nextLine = sch?.enabled && !sch.paused && sch.next ? ` Next automatic run ${when(sch.next)}.`
    : sch?.paused ? " Automatic runs are paused." : "";
  const lastRunLine = s?.last_run
    ? `Last run started ${when(s.last_run)}. ${plural(s.jobs.open, "open match", "open matches")} in your tracker.${nextLine}`
    : "No runs yet. Press Run now to read every company's job board.";

  let headline: string;
  if (running) headline = phaseText(run);
  else if (busyElsewhere) headline = "A run is going in a terminal or the scheduled task";
  else if (fresh.length)
    headline = `${plural(fresh.length, "new match", "new matches")} ${PREVIOUS_VISIT ? "since you last looked" : "in the last day"}`;
  else headline = PREVIOUS_VISIT ? "Nothing new since you last looked" : "No new matches in the last day";

  return (
    <div className="screen dashboard">
      <header className="dash-head">
        <h1 className="headline">{headline}</h1>
        {running ? <RunDetails /> : <p className="lede">{lastRunLine}</p>}
        <ErrorNote error={summary.error} />
      </header>

      {companies.data && (
        <BoardMap
          companies={companies.data}
          live={live.boards}
          reading={running && run.phase === "boards"}
          onPickResult={(r) => go("companies", { result: r })}
          onPickCompany={(c) => go("companies", { c })}
        />
      )}

      <div className="dash-columns">
        <section className="panel" aria-labelledby="matches-title">
          <div className="panel-head">
            <h2 id="matches-title">
              {!fresh.length ? "Latest matches" : PREVIOUS_VISIT ? "New since you last looked" : "New in the last day"}
            </h2>
            <button className="text-button" onClick={() => go("jobs")}>
              All matches
            </button>
          </div>
          <MatchList jobs={fresh.length ? fresh.slice(0, 12) : latest} meta={meta.data} />
          {fresh.length > 12 && (
            <button className="text-button" onClick={() => go("jobs")}>
              {plural(fresh.length - 12, "more new match", "more new matches")} in Jobs
            </button>
          )}
        </section>

        <section className="panel side" aria-labelledby="month-title">
          <h2 id="month-title">This month</h2>
          {s && (
            <>
              <Meter
                label={`Web searches (${s.usage.search.provider})`}
                used={s.usage.search.used}
                limit={s.usage.search.limit}
                note={s.usage.search.key_set ? undefined : "No search key set, so new-company search is off."}
              />
              <Meter
                label="Hunter credits"
                used={s.usage.hunter.used}
                limit={s.usage.hunter.limit}
                note={s.usage.hunter.key_set ? undefined : "No Hunter key set."}
              />
              <dl className="facts">
                <div>
                  <dt>Postings saved</dt>
                  <dd className="num">{n(s.postings.open)} open</dd>
                </div>
                <div>
                  <dt>Companies watched</dt>
                  <dd className="num">{n(s.companies.enabled)}</dd>
                </div>
                <div>
                  <dt>Matches found in the last day</dt>
                  <dd className="num">{n(s.jobs.new_24h)}</dd>
                </div>
                {s.schedule.runs_per_day ? (
                  <div>
                    <dt>Web searches for new companies</dt>
                    <dd>{s.schedule.runs_per_day === 1 ? "Once a day" : `${s.schedule.runs_per_day} a day`}</dd>
                  </div>
                ) : null}
              </dl>
            </>
          )}
        </section>
      </div>

    </div>
  );
}

function useTick(ms: number) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => setTick((x) => x + 1), ms);
    return () => window.clearInterval(t);
  }, [ms]);
}

function RunDetails() {
  const { run } = useLive();
  useTick(1000);
  if (!run) return null;
  const share = run.total ? run.done / run.total : 0;
  return (
    <div className="run-details">
      <div className="progress" aria-hidden="true">
        <div style={{ width: `${run.phase === "boards" ? Math.max(1, share * 100) : 0}%` }} />
      </div>
      <p className="lede">
        {run.label}, started {minutes(run.started)} ago.
        {run.last_company && run.phase === "boards" ? ` Just read ${run.last_company}.` : ""}
        {run.failed_boards ? ` ${plural(run.failed_boards, "board")} not read so far.` : ""}
        {run.rate_limited.length ? ` Paused by ${[...new Set(run.rate_limited)].slice(0, 3).join(", ")}.` : ""}
      </p>
      {run.new.length > 0 && (
        <ul className="run-new">
          {run.new.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MatchList({ jobs, meta }: { jobs: Job[]; meta?: Meta }) {
  if (!jobs.length) {
    return <p className="quiet">No open matches yet. They appear here after a run finds them.</p>;
  }
  const regionColor = new Map(meta?.regions.map((r) => [r.name, r.color]) ?? []);
  return (
    <ul className="match-list">
      {jobs.map((j) => (
        <li key={j.key}>
          <div className="match-main">
            <External href={j.url}>{j.title}</External>
            <span className="match-company">
              {j.company}
              {j.tier && <span className={`tier tier-${j.tier}`}>{j.tier}</span>}
            </span>
          </div>
          <div className="match-meta">
            <Chip color={regionColor.get(j.region)}>{j.region}</Chip>
            {j.level === "Senior/Lead" && <Chip color={meta?.colors.level["Senior/Lead"]}>Senior/Lead</Chip>}
            <span className="quiet" title={j.found ? `Found ${when(j.found)}` : undefined}>
              {j.posted ? `posted ${ago(j.posted)}` : `found ${ago(j.found)}`}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
