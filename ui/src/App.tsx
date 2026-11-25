import { Fragment, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, TOKEN } from "./api";
import { External, Icon, Logo } from "./components/bits";
import { APP_NAME, AUTHOR, REPO_URL } from "./brand";
import { Welcome } from "./components/Welcome";
import { RunControls } from "./components/RunControls";
import { UpdateBeforeRun, UpdateNotice } from "./components/Update";
import { applyPaint, applyTheme, currentPaint, onPaintChange, type PaintChoice, type ThemeChoice } from "./theme";
import { n } from "./format";
import { connect, dismissToast, setRun, useLive } from "./live";
import { useCompanies, useMeta, useSummary } from "./queries";
import { go, useRoute, type Screen } from "./router";
import { Companies } from "./screens/Companies";
import { Dashboard } from "./screens/Dashboard";
import { JobsScreen } from "./screens/Jobs";
import { Settings } from "./screens/Settings";
import { Activity } from "./screens/Activity";
import { Outreach, useOutreach } from "./screens/Outreach";
import { Pipeline, usePipeline } from "./screens/Pipeline";

// One paint per screen. Structural is the default; the application journey wears Nori; Settings is ink.
// A paint chosen in Settings, Appearance replaces this and is worn on every screen.
const PAINT: Partial<Record<Screen, string>> = { pipeline: "nori", outreach: "nori", settings: "sumi" };
const TITLES: Record<Screen, string> = {
  dashboard: "Today", jobs: "Jobs", postings: "Jobs", pipeline: "Pipeline", outreach: "Outreach",
  companies: "Companies", activity: "Activity", settings: "Settings",
};

type NavItem = { screen: Screen; label: string; count?: number; also?: Screen };

export function App() {
  const route = useRoute();
  const paint = useSyncExternalStore(onPaintChange, currentPaint);
  const client = useQueryClient();
  const live = useLive();
  const summary = useSummary();
  const companies = useCompanies();
  const meta = useMeta();
  const outreach = useOutreach();
  const pipeline = usePipeline();
  const setup = useQuery({ queryKey: ["setup"], queryFn: () => api<{ needed: boolean; companies: number }>("setup") });
  const [welcomed, setWelcomed] = useState(false);
  const content = useRef<HTMLElement>(null);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    if (TOKEN) connect();
  }, []);

  // Your light or dark choice (the page usually arrives with it already; this covers the dev server).
  const appearance = useQuery({ queryKey: ["appearance"], queryFn: () => api<{ theme: ThemeChoice; paint: PaintChoice }>("appearance") });
  useEffect(() => {
    if (appearance.data) { applyTheme(appearance.data.theme); applyPaint(appearance.data.paint ?? "screen"); }
  }, [appearance.data]);

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

  // A new screen starts at its top.
  useEffect(() => {
    if (content.current) content.current.scrollTop = 0;
  }, [route.screen]);

  if (!TOKEN) {
    return (
      <div className="no-token">
        <h1>Open {APP_NAME} from its window</h1>
        <p>This page only works inside the window that <code>python app.py</code> opens.</p>
      </div>
    );
  }

  const groups: { label: string; items: NavItem[] }[] = [
    { label: "Find", items: [
      { screen: "dashboard", label: "Today" },
      { screen: "jobs", label: "Jobs", count: summary.data?.jobs.open, also: "postings" },
      { screen: "companies", label: "Companies", count: companies.data?.length },
    ] },
    { label: "Apply", items: [
      { screen: "pipeline", label: "Pipeline", count: pipeline.data?.follow_ups.length || undefined },
      { screen: "outreach", label: "Outreach",
        count: outreach.data?.companies.filter((c) => ["to_write", "follow_up", "drafts"].includes(c.stage)).length || undefined },
    ] },
    { label: "Manage", items: [
      { screen: "activity", label: "Activity" },
      { screen: "settings", label: "Settings" },
    ] },
  ];
  const running = live.run?.status === "running";

  return (
    <div className="shell" data-paint={paint === "screen" ? PAINT[route.screen] : paint}>
      <aside className="sidenav">
        <div className="brand">
          <Logo size={32} lit={running || busyElsewhere} />
          <span className="brand-text">
            <span className="brand-name">{APP_NAME}</span>
            <External href={REPO_URL} className="brand-by">by {AUTHOR}</External>
          </span>
        </div>
        <SideNav groups={groups} screen={route.screen} />
        <RunControls lastRun={summary.data?.last_run ?? null} busyElsewhere={busyElsewhere} />
        {!live.connected && <p className="offline-note" style={{ marginTop: 12 }}><Icon name="alert" />Reconnecting to the app</p>}
      </aside>

      <main className="content" ref={content} onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 48)}>
        <header className={`topbar ${scrolled ? "is-scrolled" : ""}`}>
          <span className="topbar-title" aria-hidden={!scrolled}>{TITLES[route.screen]}</span>
          <div className="topbar-end"><UpdateNotice /></div>
        </header>
        <div className="page">
          {meta.data?.settings_problem && (
            <div className="notice bad" role="alert">
              <Icon name="alert" size={20} />
              <div>
                <span>Your settings file (my_settings.toml) has a problem: {meta.data.settings_problem}</span>
                {route.screen !== "settings" && <button className="text-button" onClick={() => go("settings")}>Open Settings</button>}
              </div>
            </div>
          )}
          {route.screen === "dashboard" && <Dashboard />}
          {(route.screen === "jobs" || route.screen === "postings") && <JobsScreen view={route.screen} />}
          {route.screen === "companies" && <Companies />}
          {route.screen === "settings" && <Settings />}
          {route.screen === "outreach" && <Outreach />}
          {route.screen === "pipeline" && <Pipeline />}
          {route.screen === "activity" && <Activity />}
        </div>
      </main>

      {setup.data?.needed && !welcomed && <Welcome companies={setup.data.companies} onDone={() => setWelcomed(true)} />}

      <UpdateBeforeRun />
      <div className="toasts" aria-live="polite">
        {live.toasts.map((t) => (
          <div key={t.id} className={`toast tone-${t.tone}`} role={t.tone === "bad" ? "alert" : "status"}>
            {t.tone === "bad" && <span className="toast-mark"><Icon name="alert" /></span>}
            <span>{t.text}</span>
            {t.action && (
              <button className="toast-action" onClick={() => { t.action!.run(); dismissToast(t.id); }}>{t.action.label}</button>
            )}
            <button className="icon-button" onClick={() => dismissToast(t.id)} aria-label="Dismiss"><Icon name="close" /></button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** The side nav. The active line slides to the screen you pick before the screen changes. */
function SideNav({ groups, screen }: { groups: { label: string; items: NavItem[] }[]; screen: Screen }) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [y, setY] = useState<number | null>(null);
  const active = groups.flatMap((g) => g.items).find((i) => i.screen === screen || i.also === screen)?.screen;
  useLayoutEffect(() => {
    const el = active ? refs.current[active] : null;
    setY(el ? el.offsetTop + 10 : null);
  }, [active]);
  return (
    <nav className="nav" aria-label="Screens">
      {groups.map((g) => (
        <Fragment key={g.label}>
          <p className="nav-label eyebrow">{g.label}</p>
          {g.items.map((item) => {
            const on = item.screen === active;
            return (
              <button key={item.screen} ref={(el) => { refs.current[item.screen] = el; }}
                className={on ? "nav-item is-active" : "nav-item"} aria-current={on ? "page" : undefined}
                onClick={() => go(item.screen)}>
                <span>{item.label}</span>
                {item.count != null && <span className="num">{n(item.count)}</span>}
              </button>
            );
          })}
        </Fragment>
      ))}
      {y != null && <span className="nav-light" style={{ transform: `translateY(${y}px)` }} aria-hidden="true" />}
    </nav>
  );
}
