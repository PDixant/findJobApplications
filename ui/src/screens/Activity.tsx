import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type RunRecord } from "../api";
import { Empty, ErrorNote } from "../components/bits";
import { n, plural, when } from "../format";
import { replaceLog, seedLog, useLive } from "../live";
import { useSummary } from "../queries";

const SOURCE: Record<string, string> = {
  app: "from the app", schedule: "scheduled", tray: "from the tray", "command line": "from a terminal",
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
    <div className="screen activity">
      <header className="screen-head">
        <h1>Activity</h1>
        <p className="lede">Every run, whether from the app, the schedule or a terminal.</p>
      </header>
      <ErrorNote error={runs.error} />
      {runs.data && !runs.data.length && <Empty title="No runs recorded yet">Runs show up here once one finishes.</Empty>}
      {runs.data && runs.data.length > 0 && (
        <ol className="run-list">
          {runs.data.map((r) => <RunRow key={r.id} r={r} />)}
        </ol>
      )}
      <LogPanel lines={live.log} />
    </div>
  );
}

function RunRow({ r }: { r: RunRecord }) {
  const s = r.summary || {};
  const facts = [
    s.minutes != null ? `${s.minutes} min` : null,
    s.boards_read != null && !s.offline ? `${n(s.boards_read)} boards read` : null,
    s.boards_paused ? `${n(s.boards_paused)} paused` : null,
    s.new_matches ? plural(s.new_matches.length, "new match", "new matches") : null,
  ].filter(Boolean);
  const sites = Object.entries(s.by_site ?? {});
  return (
    <li className={`run-row status-${r.status}`}>
      <details>
        <summary>
          <span className="run-when">{when(r.started)}</span>
          <span className="run-label">{r.label} <span className="quiet">{SOURCE[r.source] ?? ""}</span></span>
          <span className="run-facts quiet">{r.status === "finished" ? facts.join(", ") : r.status === "running" ? "Running" : r.status}</span>
        </summary>
        <div className="run-more">
          {s.error && <p className="error-note">{s.error}</p>}
          {s.new_matches && s.new_matches.length > 0 && (
            <ul className="plain-list">{s.new_matches.map((m) => <li key={m}>{m}</li>)}</ul>
          )}
          {sites.length > 0 && <p className="quiet">Requests: {sites.map(([site, count]) => `${site} ${count}`).join(", ")}</p>}
          {s.open_matches != null && <p className="quiet">{plural(s.open_matches, "open match", "open matches")} in your tracker afterwards.</p>}
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
      <summary>Log</summary>
      <pre ref={box} className="log-lines" tabIndex={0}>{lines.length ? lines.join("\n") : "Nothing logged yet."}</pre>
    </details>
  );
}
