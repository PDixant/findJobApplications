import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { TOKEN } from "./api";
import { RunControls } from "./components/RunControls";
import { n } from "./format";
import { connect, dismissToast, setRun, useLive } from "./live";
import { useCompanies, useMeta, useSummary } from "./queries";
import { go, useRoute, type Screen } from "./router";
import { Companies } from "./screens/Companies";
import { Dashboard } from "./screens/Dashboard";
import { Jobs, Postings } from "./screens/Jobs";

export function App() {
  const route = useRoute();
  const client = useQueryClient();
  const live = useLive();
  const summary = useSummary();
  const companies = useCompanies();
  const meta = useMeta();

  useEffect(() => {
    if (TOKEN) connect();
  }, []);

  // Take the run state from the server on first load (a run may already be going).
  const seeded = useRef(false);
  useEffect(() => {
    if (!seeded.current && summary.data) {
      seeded.current = true;
      if (summary.data.run && !live.run) setRun(summary.data.run);
    }
  }, [summary.data, live.run]);

  // A run ended here, or one in a terminal finished: refresh everything.
  const busyElsewhere = Boolean(summary.data?.busy_elsewhere);
  const wasBusy = useRef(busyElsewhere);
  useEffect(() => {
    if (live.version > 0) client.invalidateQueries();
  }, [live.version, client]);
  useEffect(() => {
    if (wasBusy.current && !busyElsewhere) client.invalidateQueries();
    wasBusy.current = busyElsewhere;
  }, [busyElsewhere, client]);

  if (!TOKEN) {
    return (
      <div className="no-token">
        <h1>Open Job Watcher from app.py</h1>
        <p>This page only works inside the window that <code>python app.py</code> opens.</p>
      </div>
    );
  }

  const nav: { screen: Screen; label: string; count?: number; also?: Screen }[] = [
    { screen: "dashboard", label: "Dashboard" },
    { screen: "jobs", label: "Jobs", count: summary.data?.jobs.open, also: "postings" },
    { screen: "companies", label: "Companies", count: companies.data?.length },
  ];

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <i /><i /><i /><i />
          </span>
          Job Watcher
        </div>
        <nav className="rail-nav" aria-label="Screens">
          {nav.map((item) => {
            const active = route.screen === item.screen || route.screen === item.also;
            return (
              <button key={item.screen} className={active ? "nav-item is-active" : "nav-item"}
                aria-current={active ? "page" : undefined} onClick={() => go(item.screen)}>
                <span>{item.label}</span>
                {item.count != null && <span className="num">{n(item.count)}</span>}
              </button>
            );
          })}
        </nav>
        <RunControls lastRun={summary.data?.last_run ?? null} busyElsewhere={busyElsewhere} />
        {!live.connected && <p className="rail-offline">Reconnecting to the app's server</p>}
      </aside>

      <main className="content">
        {meta.data?.settings_problem && (
          <p className="banner">config.toml has a problem: {meta.data.settings_problem}</p>
        )}
        {route.screen === "dashboard" && <Dashboard />}
        {route.screen === "jobs" && <Jobs />}
        {route.screen === "postings" && <Postings />}
        {route.screen === "companies" && <Companies />}
      </main>

      <div className="toasts" aria-live="polite">
        {live.toasts.map((t) => (
          <div key={t.id} className={`toast tone-${t.tone}`}>
            <span>{t.text}</span>
            <button className="icon-button" onClick={() => dismissToast(t.id)} aria-label="Dismiss">
              <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                <path d="M3.5 3.5l9 9m0-9l-9 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
