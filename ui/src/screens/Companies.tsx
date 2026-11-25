import { useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { RESULT_ORDER, resultInfo, type Company } from "../api";
import { Drawer, Empty, ErrorNote, External, SearchInput } from "../components/bits";
import { Table, TableSkeleton } from "../components/Table";
import { ago, n, when } from "../format";
import { useCompanies, useJobs, useSummary } from "../queries";
import { useLive } from "../live";
import { AddCompanies, CompanyForm } from "./CompanyEditing";
import { useRunActions } from "../components/RunControls";
import { go, useRoute } from "../router";

function Result({ result }: { result: string }) {
  const info = resultInfo(result);
  return (
    <span className="result-cell" title={info.explain}>
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
  { id: "company", header: "Company", accessorFn: (c) => c.company.toLowerCase(), size: 240,
    cell: (c) => (
      <span className={`cell-stack ${c.row.original.enabled ? "" : "is-off"}`}>
        <span className="cell-main">{c.row.original.company}
          {c.row.original.pending && <span className="pending-mark" title="Edited during a run; saved when it finishes">edited</span>}</span>
        <span className="cell-sub">{c.row.original.region || "No region"}</span>
      </span>
    ) },
  { id: "score", header: "Score", accessorFn: (c) => Number(c.score) || -1, size: 88, meta: { num: true },
    cell: (c) => <span className="num" title={c.row.original.tier ? `Tier ${c.row.original.tier}` : undefined}>{c.row.original.score}</span> },
  { id: "result", header: "Last run", accessorFn: (c) => RESULT_ORDER.indexOf(c.result), size: 180,
    cell: (c) => <Result result={c.row.original.result} /> },
  { id: "board", header: "Board", accessorFn: (c) => boardOf(c),
    cell: (c) => <span className="quiet">{c.row.original.found_on ? boardOf(c.row.original) : ""}</span> },
  { id: "open", header: "Open postings", accessorFn: (c) => c.open ?? -1, size: 128, meta: { num: true },
    cell: (c) => <span className="num">{c.row.original.open ?? ""}</span> },
  { id: "matches", header: "Matches", accessorKey: "matches", size: 96, meta: { num: true },
    cell: (c) => <span className="num">{c.row.original.matches || ""}</span> },
  { id: "checked", header: "Checked", accessorFn: (c) => c.checked ?? "", size: 112,
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
    <>
      <header className="page-head">
        <h1>Companies</h1>
        <div className="head-actions">
          <button className="button" disabled={locked} onClick={() => actions.start("career_pages")}
            title="Searches the web for the careers page or job board of companies with no board found or nothing readable (one search each), then reads them.">
            Find careers pages{missing ? ` (${n(missing)})` : ""}
          </button>
          <button className="button" disabled={locked} onClick={() => actions.start("scores")}
            title="Updates stock metrics and the 0 to 100 company scores.">Refresh scores</button>
          {!adding && <button className="button primary" onClick={() => setAdding(true)}>Add companies</button>}
        </div>
        <p className="lede">Every company is checked on every run. This is what happened to each one last time.</p>
      </header>
      <hr className="character" style={{ marginBottom: 32 }} />
      {adding && <AddCompanies disabled={locked} onClose={() => setAdding(false)} />}
      <div className="toolbar" role="search">
        <SearchInput value={text} onChange={setText} label="Search companies" placeholder="Name, region, notes or board" />
        <div className="segmented" role="group" aria-label="Last run result">
          <button aria-pressed={result === null} onClick={() => setResult(null)}>
            All <span className="num">{n(companies.data?.length ?? 0)}</span>
          </button>
          {RESULT_ORDER.filter((r) => counts.get(r)).map((r) => (
            <button key={r} aria-pressed={result === r} onClick={() => setResult(r)} title={resultInfo(r).explain}>
              <span className={`swatch tone-${resultInfo(r).tone}`} aria-hidden="true" />
              {resultInfo(r).label} <span className="num">{n(counts.get(r) ?? 0)}</span>
            </button>
          ))}
        </div>
      </div>
      {result !== null && <p className="hint" style={{ marginBottom: 16 }}>{resultInfo(result).explain}</p>}
      <ErrorNote error={companies.error} />
      {!companies.data ? <TableSkeleton /> : !shown.length ? (
        <Empty small title="No companies fit this search">Try part of the name, or clear the result filter.</Empty>
      ) : (
        <Table label="Companies" data={shown} columns={columns} rowKey={(c) => c.company} selected={selected}
          onOpen={(c) => setSelected(c.company)} initialSort={[{ id: "score", desc: true }]} flexColumn="board" />
      )}
      <Drawer open={Boolean(company)} onClose={() => setSelected(null)} title={company?.company ?? ""}
        sub={company ? [company.region, company.score ? `score ${company.score}, tier ${company.tier}` : ""].filter(Boolean).join(" · ") : null}
        foot={company && company.enabled && !company.from_config ? (
          <button className="button primary" disabled={locked} onClick={() => actions.start("check", company.company)}>Check this company now</button>
        ) : null}>
        {company && <CompanyDetails c={company} locked={locked} />}
      </Drawer>
    </>
  );
}

function CompanyDetails({ c, locked }: { c: Company; locked: boolean }) {
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
      <div className="result-callout">
        <Result result={c.result} />
        <p className="secondary">{c.why || info.explain}</p>
        {c.checked && <p className="quiet">Checked {when(c.checked)}</p>}
      </div>
      <section className="sheet-section" aria-label="About the company">
        <dl className="facts">
          <div><dt>Region</dt><dd>{c.region || "Not set"}{c.hubs && c.hubs !== c.region ? ` (hubs: ${c.hubs})` : ""}</dd></div>
          <div><dt>Board</dt><dd>{c.found_on ? boardOf(c) : c.board && c.board !== "auto"
            ? `${boardOf(c)} (set in your company list, not found there)` : "None found yet"}</dd></div>
          {c.careers_url && <div><dt>Careers page</dt><dd><External href={c.careers_url}>{c.careers_url}</External></dd></div>}
          <div><dt>Open postings</dt><dd className="num">{c.open ?? "Not read"}</dd></div>
          {(c.type || c.stage) && <div><dt>Type</dt><dd>{[c.type, c.stage, c.ticker].filter(Boolean).join(", ")}</dd></div>}
          {metrics.length > 0 && <div><dt>Metrics</dt><dd>{metrics.join(", ")}</dd></div>}
          {c.domain && <div><dt>Email domain</dt><dd>{c.domain}</dd></div>}
          {c.notes && <div><dt>Notes</dt><dd>{c.notes}</dd></div>}
          <div><dt>Watching</dt><dd>{c.enabled ? "Yes" : "No"}</dd></div>
        </dl>
      </section>
      <section className="sheet-section" aria-labelledby="their-matches">
        <h3 id="their-matches">Your matches here</h3>
        {theirs.length ? (
          <ul className="rows">
            {theirs.map((j) => (
              <li key={j.key}>
                <button className="row" onClick={() => go("jobs", { job: j.key })}>
                  <span className="row-main"><span className="row-title">{j.title}</span>
                    <span className="row-sub">{j.region}{j.open ? "" : ", closed"}</span></span>
                </button>
              </li>
            ))}
          </ul>
        ) : <p className="hint">No matches from this company yet.</p>}
      </section>
      <section className="sheet-section" aria-labelledby="edit-title">
        <h3 id="edit-title">Edit</h3>
        <CompanyForm c={c} locked={locked} />
      </section>
    </div>
  );
}
