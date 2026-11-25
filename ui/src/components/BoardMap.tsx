import { useMemo, useState } from "react";
import { RESULT_ORDER, resultInfo, type Company, type Run } from "../api";
import { n, plural } from "../format";
import { useLive } from "../live";
import { loadPrefs, savePrefs } from "../queries";
import { Icon, LineProgress, useTip } from "./bits";
import { phaseText, timeLeft } from "./RunControls";

type Props = {
  companies: Company[];
  onPickResult: (result: string) => void;
  onPickCompany: (company: string) => void;
};

const SKIPPED = ["MANUAL", "OFF"]; // never read on a run

/** Your companies, simply: one bar of what happened on the last run, or how far this run has got.
 *  "Show every company" opens the full map, one square per company. */
export function CompanyStatus({ companies, onPickResult, onPickCompany }: Props) {
  const { run, boards, boardsStartedAt } = useLive();
  const running = run?.status === "running";
  const reading = running && run.phase === "boards";
  const [open, setOpen] = useState(() => loadPrefs("companyMap", { open: false }).open);
  const toggle = () => setOpen((o) => { savePrefs("companyMap", { open: !o }); return !o; });

  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of companies) m.set(c.result, (m.get(c.result) ?? 0) + 1);
    return m;
  }, [companies]);
  const watched = companies.filter((c) => c.enabled).length;
  const read = counts.get("OK") ?? 0;

  return (
    <section className="card status-card" aria-labelledby="status-title">
      <header className="card-head">
        <h2 id="status-title">{running ? phaseText(run) : "Your companies"}</h2>
        <button className="text-button" aria-expanded={open} onClick={toggle}>
          {open ? "Show less" : "Show every company"}
        </button>
        <p className="status-sub">
          {running ? (run.source === "schedule" ? "Your scheduled run" : run.label)
            : `${plural(watched, "company", "companies")} watched. The last run read ${plural(read, "job board")}.`}
        </p>
      </header>

      {running ? <RunProgress run={run} boards={boards} since={boardsStartedAt} reading={reading} />
        : <StackBar counts={counts} total={companies.length} onPick={onPickResult} />}

      {open && <CompanyMap companies={companies} live={boards} reading={reading} onPick={onPickCompany} />}
    </section>
  );
}

/** The last run, as one bar: each part is a result, as wide as the companies it covers. */
function StackBar({ counts, total, onPick }: { counts: Map<string, number>; total: number; onPick: (r: string) => void }) {
  const tip = useTip();
  const parts = RESULT_ORDER.filter((r) => counts.get(r));
  const body = (r: string) => {
    const info = resultInfo(r);
    const c = counts.get(r) ?? 0;
    return <><strong>{info.label}: {plural(c, "company", "companies")} ({Math.round((c / Math.max(1, total)) * 100)}%)</strong>{info.explain}</>;
  };
  return (
    <>
      <div className="stack-bar" aria-hidden="true" onMouseLeave={tip.hide}>
        {parts.map((r) => (
          <button key={r} tabIndex={-1} style={{ flexGrow: counts.get(r) ?? 0, flexBasis: 0 }}
            onMouseMove={(e) => tip.show(e, body(r))} onClick={() => onPick(r)}>
            <span className={`tone-${resultInfo(r).tone}`} />
          </button>
        ))}
      </div>
      <ul className="legend" aria-label="What happened on the last run">
        {parts.map((r) => (
          <li key={r}>
            <button onClick={() => onPick(r)} title={resultInfo(r).explain}>
              <span className={`swatch tone-${resultInfo(r).tone}`} aria-hidden="true" />
              {resultInfo(r).label} <span className="num">{n(counts.get(r) ?? 0)}</span>
            </button>
          </li>
        ))}
      </ul>
      {tip.node}
    </>
  );
}

/** A run in progress: the one number that matters (boards read), a line of light, and what's happening. */
function RunProgress({ run, boards, since, reading }: { run: Run; boards: Record<string, boolean>; since: number | null; reading: boolean }) {
  if (!reading || !run.total) {
    return (
      <>
        <LineProgress share={null} label={phaseText(run)} />
        {run.new.length > 0 && <NewSoFar lines={run.new} />}
      </>
    );
  }
  const failed = Object.values(boards).filter((ok) => !ok).length || run.failed_boards;
  const left = timeLeft(run.done, run.total, since);
  const paused = [...new Set(run.rate_limited)];
  return (
    <>
      <div className="run-figure">
        <span className="t-display-m">{n(run.done)}</span>
        <span className="of">of {plural(run.total, "job board")} read</span>
        <span className="pct">{Math.min(100, Math.floor((run.done / run.total) * 100))}%</span>
      </div>
      <LineProgress share={run.cancelled ? null : run.done / run.total} label="Job boards read" />
      <p className="run-facts">
        {run.last_company && <span>Just read {run.last_company}</span>}
        {left && <span>{left[0].toUpperCase() + left.slice(1)}</span>}
        {run.new.length > 0 && <span>{plural(run.new.length, "new match", "new matches")} so far</span>}
        {failed > 0 && <span className="bad"><Icon name="alert" />{plural(failed, "board")} not read</span>}
        {paused.length > 0 && <span>Paused by {paused.slice(0, 3).join(", ")}</span>}
      </p>
      {run.new.length > 0 && <NewSoFar lines={run.new} />}
    </>
  );
}

function NewSoFar({ lines }: { lines: string[] }) {
  return (
    <ul className="run-new" aria-label="New matches so far">
      {lines.slice(-5).map((line) => <li key={line}>{line}</li>)}
      {lines.length > 5 && <li className="quiet">and {n(lines.length - 5)} more</li>}
    </ul>
  );
}

/** Every company, one square each. Grouped by last run; during a run, A to Z, lighting up as each
 *  board is read. A board that failed is drawn as a ring, so it never depends on colour alone. */
function CompanyMap({ companies, live, reading, onPick }: {
  companies: Company[]; live: Record<string, boolean>; reading: boolean; onPick: (c: string) => void;
}) {
  const [hover, setHover] = useState<Company | null>(null);

  const groups = useMemo(() => {
    const byName = [...companies].sort((a, b) => a.company.localeCompare(b.company));
    if (reading) return [{ result: null as string | null, items: byName }];
    return RESULT_ORDER.map((r) => ({ result: r as string | null, items: byName.filter((c) => c.result === r) }))
      .filter((g) => g.items.length);
  }, [companies, reading]);

  function tone(c: Company) {
    if (!reading || SKIPPED.includes(c.result)) return resultInfo(c.result).tone;
    if (!(c.company in live)) return "pending";
    return live[c.company] ? "ok" : "error";
  }

  function note(c: Company | null) {
    if (!c) {
      const readNow = Object.keys(live).length;
      return reading ? `${plural(readNow, "board")} read so far this run. Point at a square to see the company.`
        : "Point at a square to see the company. Click it to open it in Companies.";
    }
    if (reading && c.company in live) return <><strong>{c.company}</strong>: {live[c.company] ? "read this run" : "not read this run (see the log in Activity)"}</>;
    if (reading && !SKIPPED.includes(c.result)) return <><strong>{c.company}</strong>: still to read</>;
    const info = resultInfo(c.result);
    const open = c.result === "OK" && c.open != null ? `, ${plural(c.open, "open posting")}` : "";
    return <><strong>{c.company}</strong>: {info.label}{open}. {c.why || info.explain}</>;
  }

  return (
    <div className="grid-wrap" onMouseLeave={() => setHover(null)}>
      {groups.map((g) => (
        <div key={g.result ?? "all"} className="grid-group">
          {g.result != null ? (
            <p className="grid-group-label">
              <span className={`swatch tone-${resultInfo(g.result).tone}`} aria-hidden="true" />
              {resultInfo(g.result).label} <span className="num">{n(g.items.length)}</span>
            </p>
          ) : (
            <p className="grid-group-label">
              <span className="swatch tone-ok" aria-hidden="true" />Read this run
              <span className="swatch tone-error" aria-hidden="true" style={{ marginLeft: 12 }} />Not read
              <span className="swatch tone-pending" aria-hidden="true" style={{ marginLeft: 12 }} />Still to read
              <span style={{ marginLeft: 12 }}>A to Z</span>
            </p>
          )}
          <div className={`company-grid ${reading ? "is-reading" : ""}`} role="list"
            aria-label={g.result != null ? `${resultInfo(g.result).label}: ${g.items.length} companies` : "Every company, A to Z"}>
            {g.items.map((c) => (
              <span key={c.company} role="listitem" aria-label={c.company} className={`cell tone-${tone(c)}`}
                onMouseEnter={() => setHover(c)} onClick={() => onPick(c.company)} />
            ))}
          </div>
        </div>
      ))}
      <p className="grid-note" aria-live="off">{note(hover)}</p>
    </div>
  );
}
