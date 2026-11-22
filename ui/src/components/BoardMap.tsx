import { useMemo, useState } from "react";
import { RESULT_ORDER, resultInfo, type Company } from "../api";
import { n } from "../format";

type Props = {
  companies: Company[];
  live: Record<string, boolean>;
  reading: boolean; // boards are being read right now
  onPickResult: (result: string) => void;
  onPickCompany: (company: string) => void;
};

/** One square per company, grouped by what happened on the last run. During a run the squares
 *  light up as each board is read, so you can watch every company get checked. */
export function BoardMap({ companies, live, reading, onPickResult, onPickCompany }: Props) {
  const [hover, setHover] = useState<Company | null>(null);

  const { ordered, counts } = useMemo(() => {
    const rank = (r: string) => {
      const i = RESULT_ORDER.indexOf(r);
      return i === -1 ? RESULT_ORDER.length : i;
    };
    const ordered = [...companies].sort(
      (a, b) => rank(a.result) - rank(b.result) || a.company.localeCompare(b.company),
    );
    const counts = new Map<string, number>();
    for (const c of ordered) counts.set(c.result, (counts.get(c.result) ?? 0) + 1);
    return { ordered, counts };
  }, [companies]);

  const readNow = Object.keys(live).length;
  const failedNow = Object.values(live).filter((ok) => !ok).length;

  function tone(c: Company) {
    if (!reading || c.result === "MANUAL" || c.result === "OFF") return resultInfo(c.result).tone;
    if (!(c.company in live)) return "pending";
    return live[c.company] ? "ok" : "error";
  }

  function readout(c: Company | null) {
    if (!c) {
      return reading
        ? `${n(readNow)} boards read so far this run${failedNow ? `, ${n(failedNow)} not read` : ""}. Point at a square to see the company.`
        : "Point at a square to see the company. Click it to open it in Companies.";
    }
    if (reading && c.company in live) {
      return `${c.company}: ${live[c.company] ? "read this run" : "not read this run (see the log)"}`;
    }
    const info = resultInfo(c.result);
    const detail = c.why || info.explain;
    const open = c.result === "OK" && c.open != null ? `, ${n(c.open)} open postings` : "";
    return `${c.company}: ${info.label}${open}. ${detail}`;
  }

  return (
    <section className="board-map" aria-label="Every company you watch">
      <div className="board-legend">
        {reading && (
          <>
            <span className="legend-item static"><span className="swatch tone-ok" aria-hidden="true" />
              <span className="legend-label">Read this run</span><span className="num">{n(readNow - failedNow)}</span></span>
            <span className="legend-item static"><span className="swatch tone-error" aria-hidden="true" />
              <span className="legend-label">Not read</span><span className="num">{n(failedNow)}</span></span>
            <span className="legend-item static"><span className="swatch tone-pending" aria-hidden="true" />
              <span className="legend-label">Still to read</span>
              <span className="num">{n(Math.max(0, companies.filter((c) => !["MANUAL", "OFF"].includes(c.result)).length - readNow))}</span></span>
          </>
        )}
        {!reading && RESULT_ORDER.filter((r) => counts.get(r)).map((r) => {
          const info = resultInfo(r);
          return (
            <button key={r} className="legend-item" onClick={() => onPickResult(r)} title={info.explain}>
              <span className={`swatch tone-${info.tone}`} aria-hidden="true" />
              <span className="legend-label">{info.label}</span>
              <span className="num">{n(counts.get(r) ?? 0)}</span>
            </button>
          );
        })}
      </div>
      <div
        className={`board-grid ${reading ? "is-reading" : ""}`}
        role="img"
        aria-label={`${n(companies.length)} companies. ${RESULT_ORDER.filter((r) => counts.get(r))
          .map((r) => `${resultInfo(r).label}: ${counts.get(r)}`)
          .join(". ")}.`}
        onMouseLeave={() => setHover(null)}
      >
        {ordered.map((c) => (
          <span
            key={c.company}
            className={`cell tone-${tone(c)}`}
            onMouseEnter={() => setHover(c)}
            onClick={() => onPickCompany(c.company)}
          />
        ))}
      </div>
      <p className="board-readout" aria-live="off">
        {readout(hover)}
      </p>
    </section>
  );
}
