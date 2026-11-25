import { useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { RESULT_ORDER, resultInfo, type Company } from "../api";
import { Drawer, Empty, ErrorNote, External } from "../components/bits";
import { Table } from "../components/Table";
import { ago, n, when } from "../format";
import { useCompanies, useJobs, useSummary } from "../queries";
import { useLive } from "../live";
import { AddCompanies, CompanyForm } from "./CompanyEditing";
import { useRunActions } from "../components/RunControls";
import { go, useRoute } from "../router";

function ResultBadge({ result }: { result: string }) {
  const info = resultInfo(result);
  return (
    <span className="result-badge" title={info.explain}>
      <span className={`swatch tone-${info.tone}`} aria-hidden="true" />
      {info.label}
    </span>
  );
}

function boardOf(c: Company) {
  if (c.found_on) return c.found_on === "website" ? "Careers site" : `${c.found_on}: ${c.found_slug}`;
  if (c.board && !["auto", ""].includes(c.board)) return c.slug ? `${c.board}: ${c.slug}` : c.board;
  return "";
}

const columns: ColumnDef<Company, any>[] = [ // eslint-disable-line @typescript-eslint/no-explicit-any
  { id: "company", header: "Company", accessorFn: (c) => c.company.toLowerCase(), size: 210,
    cell: (c) => <span className={`company-cell ${c.row.original.enabled ? "" : "is-off"}`}>{c.row.original.company}
      {c.row.original.pending && <span className="pending-mark" title="Edited during a run; applied when it saves">edited</span>}
      {c.row.original.tier && <span className={`tier tier-${c.row.original.tier}`}>{c.row.original.tier}</span>}</span> },
  { id: "region", header: "Region", accessorKey: "region", size: 160,
    cell: (c) => <span className="quiet">{c.row.original.region}</span> },
  { id: "score", header: "Score", accessorFn: (c) => Number(c.score) || -1, size: 70,
    cell: (c) => <span className="num">{c.row.original.score}</span> },
  { id: "result", header: "Last run", accessorFn: (c) => RESULT_ORDER.indexOf(c.result), size: 160,
    cell: (c) => <ResultBadge result={c.row.original.result} /> },
  { id: "board", header: "Board", accessorFn: (c) => boardOf(c),
    cell: (c) => <span className="quiet">{c.row.original.found_on ? boardOf(c.row.original) : ""}</span> },
  { id: "open", header: "Open postings", accessorFn: (c) => c.open ?? -1, size: 112,
    cell: (c) => <span className="num">{c.row.original.open ?? ""}</span> },
  { id: "matches", header: "Matches", accessorKey: "matches", size: 84,
    cell: (c) => <span className="num">{c.row.original.matches || ""}</span> },
  { id: "checked", header: "Checked", accessorFn: (c) => c.checked ?? "", size: 110,
    cell: (c) => <span className="quiet" title={when(c.row.original.checked)}>{ago(c.row.original.checked)}</span> },
];

export function Companies() {
  const companies = useCompanies();
  const route = useRoute();
  const result = route.params.get("result");
  const picked = route.params.get("c");
  const [text, setText] = useState("");
  const [adding, setAdding] = useState(false);
  const summary = useSummary();
  const live = useLive();
  const locked = live.run?.status === "running" || Boolean(summary.data?.busy_elsewhere);
  const actions = useRunActions();
  const missing = (companies.data ?? []).filter((c) => c.enabled && ["NOT FOUND", "NO JOBS READ"].includes(c.result)).length;
  const [selected, setSelected] = useState<string | null>(picked);
  useEffect(() => setSelected(picked), [picked]);

  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const c of companies.data ?? []) m.set(c.result, (m.get(c.result) ?? 0) + 1);
    return m;
  }, [companies.data]);

  const shown = useMemo(() => {
    const words = text.toLowerCase().split(/\s+/).filter(Boolean);
    return (companies.data ?? []).filter((c) => {
      if (result !== null && c.result !== result) return false;
      const hay = `${c.company} ${c.region} ${c.hubs} ${c.notes} ${c.careers_url} ${boardOf(c)}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }, [companies.data, result, text]);

  const company = companies.data?.find((c) => c.company === selected) ?? null;
  const setResult = (r: string | null) => go("companies", r === null ? {} : { result: r });

  return (
    <div className={`screen with-drawer ${company ? "drawer-open" : ""}`}>
      <div className="screen-main">
        <header className="screen-head">
          <h1>Companies</h1>
          <div className="head-actions">
            {!adding && <button className="button primary" onClick={() => setAdding(true)}>Add companies</button>}
            <button className="button" disabled={locked} onClick={() => actions.start("career_pages")}
              title="Searches the web for the careers page or job board of companies with no board found or nothing readable (one search each), then reads them.">
              Find careers pages{missing ? ` (${n(missing)})` : ""}
            </button>
            <button className="button" disabled={locked} onClick={() => actions.start("scores")}
              title="Updates stock metrics and the 0 to 100 company scores.">Refresh scores</button>
          </div>
          <p className="lede">Every company is checked on every run. This is what happened to each one last time.</p>
        </header>
        {adding && <AddCompanies disabled={locked} onClose={() => setAdding(false)} />}
        <div className="filters" role="search">
          <input className="search" type="search" placeholder="Search name, region, notes or board" value={text}
            onChange={(e) => setText(e.target.value)} aria-label="Search companies" />
          <div className="result-filter" role="group" aria-label="Last run result">
            <button className={result === null ? "seg is-on" : "seg"} aria-pressed={result === null} onClick={() => setResult(null)}>
              All <span className="num">{n(companies.data?.length ?? 0)}</span>
            </button>
            {RESULT_ORDER.filter((r) => counts.get(r)).map((r) => (
              <button key={r} className={result === r ? "seg is-on" : "seg"} aria-pressed={result === r}
                onClick={() => setResult(r)} title={resultInfo(r).explain}>
                <span className={`swatch tone-${resultInfo(r).tone}`} aria-hidden="true" />
                {resultInfo(r).label} <span className="num">{n(counts.get(r) ?? 0)}</span>
              </button>
            ))}
          </div>
        </div>
        <p className="count-line">
          {companies.data ? `Showing ${n(shown.length)} of ${n(companies.data.length)}` : "Loading companies"}
          {result !== null && <span className="quiet"> {resultInfo(result).explain}</span>}
        </p>
        <ErrorNote error={companies.error} />
        {companies.data && !shown.length ? (
          <Empty title="No companies fit this search" />
        ) : (
          <Table data={shown} columns={columns} rowKey={(c) => c.company} selected={selected}
            onOpen={(c) => setSelected(c.company)} initialSort={[{ id: "score", desc: true }]} flexColumn="board" />
        )}
      </div>
      <Drawer open={Boolean(company)} onClose={() => setSelected(null)} title={company?.company ?? ""}>
        {company && <CompanyDetails c={company} locked={locked} />}
      </Drawer>
    </div>
  );
}

function CompanyDetails({ c, locked }: { c: Company; locked: boolean }) {
  const { start } = useRunActions();
  const jobs = useJobs();
  const theirs = (jobs.data ?? []).filter((j) => j.company === c.company);
  const info = resultInfo(c.result);
  const metrics = [
    c.market_cap_b && `market cap $${c.market_cap_b}B`,
    c.stock_12m_pct && `stock ${c.stock_12m_pct}% over 12 months`,
    c.revenue_growth_pct && `revenue growth ${c.revenue_growth_pct}%`,
  ].filter(Boolean);
  return (
    <div className="details">
      <div className="result-callout" data-tone={info.tone}>
        <ResultBadge result={c.result} />
        <p>{c.why || info.explain}</p>
        {c.checked && <p className="quiet">Checked {when(c.checked)}</p>}
      </div>
      <dl className="facts">
        <div><dt>Region</dt><dd>{c.region || "Not set"}{c.hubs && c.hubs !== c.region ? ` (hubs: ${c.hubs})` : ""}</dd></div>
        <div><dt>Board</dt><dd>{c.found_on ? boardOf(c) : c.board && c.board !== "auto"
          ? `${boardOf(c)} (set in your company list, not found there)` : "None found yet"}</dd></div>
        {c.careers_url && <div><dt>Careers page</dt><dd><External href={c.careers_url}>{c.careers_url}</External></dd></div>}
        <div><dt>Open postings</dt><dd>{c.open ?? "Not read"}</dd></div>
        <div><dt>Score</dt><dd>{c.score ? `${c.score} (tier ${c.tier})` : "Not scored"}</dd></div>
        {(c.type || c.stage) && <div><dt>Type</dt><dd>{[c.type, c.stage, c.ticker].filter(Boolean).join(", ")}</dd></div>}
        {metrics.length > 0 && <div><dt>Metrics</dt><dd>{metrics.join(", ")}</dd></div>}
        {c.domain && <div><dt>Email domain</dt><dd>{c.domain}</dd></div>}
        {c.notes && <div><dt>Notes</dt><dd>{c.notes}</dd></div>}
        <div><dt>Enabled</dt><dd>{c.enabled ? "Yes" : "No"}</dd></div>
      </dl>
      <h3>Your matches here</h3>
      {theirs.length ? (
        <ul className="plain-list">
          {theirs.map((j) => (
            <li key={j.key}>
              <External href={j.url}>{j.title}</External>
              <span className="quiet"> {j.region}{j.open ? "" : ", closed"}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="quiet">No matches from this company yet.</p>
      )}
      {c.enabled && !c.from_config && (
        <p className="actions">
          <button className="button" disabled={locked} onClick={() => start("check", c.company)}>Check this company now</button>
        </p>
      )}
      <CompanyForm c={c} locked={locked} />
    </div>
  );
}
