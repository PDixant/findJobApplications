import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type RunRecord } from "../api";
import { Empty, ErrorNote, Icon, Skeleton } from "../components/bits";
import { n, plural, when } from "../format";
import { replaceLog, seedLog, useLive } from "../live";
import { useSummary } from "../queries";

const SOURCE: Record<string, string> = {
  app: "From the app", schedule: "Scheduled", tray: "From the tray", "command line": "From a terminal",
};

export function Activity() {
  const live = useLive();
  const summary = useSummary();
  const busyElsewhere = Boolean(summary.data?.busy_elsewhere);
  const runs = useQuery({ queryKey: ["runs", live.version], queryFn: () => api<RunRecord[]>("runs?limit=60"),
    refetchInterval: busyElsewhere ? 10_000 : 60_000 });
  const logQuery = useQuery({ queryKey: ["log"], queryFn: () => api<{ lines: string[] }>("log?lines=400"),
    refetchInterval: busyElsewhere ? 10_000 : false });
  useEffect(() => {
    if (!logQuery.data) return;
    if (busyElsewhere) replaceLog(logQuery.data.lines);
    else seedLog(logQuery.data.lines);
  }, [logQuery.data, busyElsewhere]);

  return (
    <>
      <header className="page-head">
        <h1>Activity</h1>
        <p className="lede">Every run, whether it came from the app, the schedule or a terminal. Open one to see what it found.</p>
      </header>
      <hr className="character" style={{ marginBottom: 16 }} />
      <ErrorNote error={runs.error} />
      {!runs.data && <div style={{ display: "grid", gap: 24, padding: "16px 0 48px" }}>{[0, 1, 2, 3].map((i) => <Skeleton key={i} height={40} />)}</div>}
      {runs.data && !runs.data.length && (
        <Empty title="No runs yet">Runs show up here once one finishes. Press Run now in the side bar to start the first.</Empty>
      )}
      {runs.data && runs.data.length > 0 && (
        <ol className="run-list">
          {runs.data.map((r) => <RunRow key={r.id} r={r} />)}
        </ol>
      )}
      <LogPanel lines={live.log} />
    </>
  );
}

function RunRow({ r }: { r: RunRecord }) {
  const s = r.summary || {};
  const facts = [
    s.boards_read != null && !s.offline ? `${n(s.boards_read)} boards read` : null,
    s.new_matches ? plural(s.new_matches.length, "new match", "new matches") : null,
    s.minutes != null ? `${s.minutes} min` : null,
  ].filter(Boolean);
  const sites = Object.entries(s.by_site ?? {});
  const status = r.status === "finished" ? <span className="run-facts-r">{facts.join(", ")}</span>
    : r.status === "running" ? <span className="run-facts-r">Running</span>
    : r.status === "failed" ? <span className="run-facts-r bad"><Icon name="alert" />Failed</span>
    : <span className="run-facts-r">Stopped</span>;
  return (
    <li className="run-row">
      <details>
        <summary>
          <span className="run-when">{when(r.started)}</span>
          <span className="run-label">{r.label}<small>{SOURCE[r.source] ?? ""}</small></span>
          {status}
          <Icon name="down" />
        </summary>
        <div className="run-detail">
          {s.error && <p className="error-note"><Icon name="alert" />{s.error}</p>}
          {s.new_matches && s.new_matches.length > 0 && <ul>{s.new_matches.map((m) => <li key={m}>{m}</li>)}</ul>}
          {s.boards_paused ? <p>{plural(s.boards_paused, "board")} paused by a rate limit, read again next run.</p> : null}
          {sites.length > 0 && <p className="quiet">Requests: {sites.map(([site, count]) => `${site} ${n(count)}`).join(", ")}</p>}
          {s.open_matches != null && <p className="quiet">{plural(s.open_matches, "open match", "open matches")} in your tracker afterwards.</p>}
          {!s.error && !s.new_matches?.length && !sites.length && s.open_matches == null && <p className="quiet">Nothing else was recorded for this run.</p>}
        </div>
      </details>
    </li>
  );
}

export function LogPanel({ lines }: { lines: string[] }) {
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const el = box.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [lines]);
  return (
    <details className="log-panel">
      <summary>The log <Icon name="down" /></summary>
      <pre ref={box} className="log-lines" tabIndex={0}>{lines.length ? lines.join("\n") : "Nothing logged yet."}</pre>
    </details>
  );
}
