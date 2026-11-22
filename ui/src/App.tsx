import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, TOKEN, type UpdateInfo } from "./api";
import { External } from "./components/bits";
import { Welcome } from "./components/Welcome";
import { RunBanner } from "./components/RunBanner";
import { RunControls } from "./components/RunControls";
import { n } from "./format";
import { connect, dismissToast, setRun, useLive } from "./live";
import { useCompanies, useMeta, useSummary } from "./queries";
import { go, useRoute, type Screen } from "./router";
import { Companies } from "./screens/Companies";
import { Dashboard } from "./screens/Dashboard";
import { Jobs, Postings } from "./screens/Jobs";
import { Settings } from "./screens/Settings";
import { Activity } from "./screens/Activity";
import { Outreach, useOutreach } from "./screens/Outreach";
import { Pipeline, usePipeline } from "./screens/Pipeline";

export function App() {
  const route = useRoute();
  const client = useQueryClient();
  const live = useLive();
  const summary = useSummary();
  const companies = useCompanies();
  const meta = useMeta();
  const outreach = useOutreach();
  const pipeline = usePipeline();
  const setup = useQuery({ queryKey: ["setup"], queryFn: () => api<{ needed: boolean; companies: number }>("setup") });
  const update = useQuery({ queryKey: ["update"], queryFn: () => api<UpdateInfo>("update"), staleTime: Infinity });
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem("jw.updateDismissed") ?? ""; } catch { return ""; }
  });
  const [welcomed, setWelcomed] = useState(false);

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
    { screen: "pipeline", label: "Pipeline", count: pipeline.data?.follow_ups.length || undefined },
    { screen: "outreach", label: "Outreach",
      count: outreach.data?.companies.filter((c) => ["to_write", "follow_up", "drafts"].includes(c.stage)).length || undefined },
    { screen: "companies", label: "Companies", count: companies.data?.length },
    { screen: "activity", label: "Activity" },
    { screen: "settings", label: "Settings" },
  ];
  const u = update.data;

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
        {/* The Dashboard shows the run in full itself */}
        {route.screen !== "dashboard" && <RunBanner busyElsewhere={busyElsewhere} />}
        {u?.newer && u.latest !== dismissed && (
          <p className="banner update">
            Job Watcher {u.latest} is out (you have {u.current}).{" "}
            {u.url && <External href={u.url}>{u.installed ? "Download the installer" : "See what's new"}</External>}
            {!u.installed && " Then update your folder (git pull) and run Install Job Watcher again."}{" "}
            <button className="text-button" onClick={() => {
              setDismissed(u.latest ?? "");
              try { localStorage.setItem("jw.updateDismissed", u.latest ?? ""); } catch { /* storage unavailable */ }
            }}>Not now</button>
          </p>
        )}
        {meta.data?.settings_problem && (
          <p className="banner">
            config.toml has a problem: {meta.data.settings_problem}{" "}
            {route.screen !== "settings" && <button className="text-button" onClick={() => go("settings")}>Open Settings</button>}
          </p>
        )}
        {route.screen === "dashboard" && <Dashboard />}
        {route.screen === "jobs" && <Jobs />}
        {route.screen === "postings" && <Postings />}
        {route.screen === "companies" && <Companies />}
        {route.screen === "settings" && <Settings />}
        {route.screen === "outreach" && <Outreach />}
        {route.screen === "pipeline" && <Pipeline />}
        {route.screen === "activity" && <Activity />}
      </main>
      {setup.data?.needed && !welcomed && (
        <Welcome companies={setup.data.companies} onDone={() => setWelcomed(true)} />
      )}

      <div className="toasts" aria-live="polite">
        {live.toasts.map((t) => (
          <div key={t.id} className={`toast tone-${t.tone}`}>
            <span>{t.text}</span>
            {t.action && (
              <button className="toast-action" onClick={() => { t.action!.run(); dismissToast(t.id); }}>
                {t.action.label}
              </button>
            )}
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
