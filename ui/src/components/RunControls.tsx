import { useRef, useState } from "react";
import { api, type Run, type RunMode } from "../api";
import { setRun, toast, useLive } from "../live";
import { n, plural, when } from "../format";
import { Icon, Jewels, LineProgress, useDismiss } from "./bits";

export const PHASES: Record<string, string> = {
  starting: "Starting",
  search: "Searching the web for new companies",
  career_pages: "Looking up careers pages",
  metrics: "Updating company scores",
  boards: "Reading job boards",
  postings: "Saving the postings",
  filter: "Finding your matches",
  export: "Writing all_postings.csv",
  save: "Saving your tracker",
  cancelling: "Stopping after the boards being read now",
};

/** What each step is called while it happens and once it's done. */
const STEP: Record<string, { doing: string; done: string }> = {
  search: { doing: "Searching the web for new companies", done: "Searched the web for new companies" },
  career_pages: { doing: "Looking up careers pages", done: "Looked up careers pages" },
  metrics: { doing: "Updating company scores", done: "Updated company scores" },
  boards: { doing: "Reading job boards", done: "Checked the job boards" },
  postings: { doing: "Saving the postings the boards listed", done: "Saved the postings" },
  filter: { doing: "Finding your matches", done: "Found your matches" },
  export: { doing: "Writing all_postings.csv", done: "Wrote all_postings.csv" },
  save: { doing: "Saving your tracker", done: "Saved your tracker" },
};
/** Steps after the boards: the run can't be stopped any more, it is finishing up. */
export const FINISHING = ["postings", "filter", "export", "save"];

export type Step = { id: string; label: string; state: "done" | "doing" | "todo" };

/** The run's steps in order: the ones it has been through, then the ones still to come. */
export function runSteps(run: Run, seen: string[]): Step[] {
  const tail = run.mode === "refilter" ? ["filter", "export", "save"] : run.mode === "scores" ? ["metrics"]
    : ["boards", "postings", "filter", "export", "save"];
  const shown = (s: string) => s in STEP && (s !== "metrics" || run.mode === "scores");
  const current = run.phase === "cancelling" ? "boards" : run.phase;
  const order = [...seen.filter(shown)];
  if (shown(current) && !order.includes(current)) order.push(current);
  for (const s of tail) if (!order.includes(s)) order.push(s);
  const at = order.indexOf(current);
  return order.map((id, i) => ({
    id,
    state: at === -1 ? "todo" : i < at ? "done" : i === at ? "doing" : "todo",
    label: i < at && id === "boards" && run.total ? `Checked ${n(run.total)} job boards` : i < at ? STEP[id].done : STEP[id].doing,
  }));
}

export function phaseText(run: Run) {
  if (run.cancelled && !FINISHING.includes(run.phase)) return PHASES.cancelling;
  return PHASES[run.phase] ?? "Working";
}

/** The steps as a short list: done ones ticked, the current one lit, the rest waiting. */
export function RunSteps({ steps }: { steps: Step[] }) {
  return (
    <ol className="run-steps" aria-label="Steps of this run">
      {steps.map((s) => (
        <li key={s.id} className={`run-step is-${s.state}`} aria-current={s.state === "doing" ? "step" : undefined}>
          <span className="run-step-mark">{s.state === "doing" ? <Jewels lit /> : <Icon name={s.state === "done" ? "check" : "dash"} />}</span>
          {s.label}
        </li>
      ))}
    </ol>
  );
}

/** How long the rest of the boards should take, once enough are read to judge. */
export function timeLeft(done: number, total: number, since: number | null): string {
  if (!since || done < 20 || done >= total) return "";
  const left = ((Date.now() - since) / done) * (total - done);
  const mins = Math.round(left / 60000);
  return mins < 1 ? "under a minute left" : `about ${mins} min left`;
}

export function useRunActions() {
  const [busy, setBusy] = useState(false);
  async function start(mode: RunMode, company?: string) {
    setBusy(true);
    try {
      setRun(await api<Run>("runs", { method: "POST", body: JSON.stringify({ mode, company }) }));
    } catch (e) {
      toast(e instanceof Error ? e.message : "The run didn't start.", "bad");
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    setBusy(true);
    try {
      setRun(await api<Run>("runs/current", { method: "DELETE" }));
    } catch (e) {
      toast(e instanceof Error ? e.message : "The run didn't stop.", "bad");
    } finally {
      setBusy(false);
    }
  }
  return { start, cancel, busy };
}

const OTHER_RUNS: { mode: RunMode; label: string; what: string }[] = [
  { mode: "refilter", label: "Re-filter saved postings", what: "Applies your current settings to postings already saved. Makes no requests." },
  { mode: "search", label: "Search the web, then run", what: "Looks for companies you don't watch yet, then reads every board." },
  { mode: "since", label: "Run and list what's new", what: "Reads every board, then lists what appeared since your last check." },
];

/** The run, low in the side nav on every screen: start one, or see how far it has got and stop it. */
export function RunControls({ lastRun, busyElsewhere }: { lastRun: string | null; busyElsewhere: boolean }) {
  const { run, boardsStartedAt, phases } = useLive();
  const { start, cancel, busy } = useRunActions();
  const [menu, setMenu] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useDismiss(box, menu, () => setMenu(false));
  const running = run?.status === "running";

  if (running) {
    const boards = run.phase === "boards" && run.total ? run.total : 0;
    const finishing = FINISHING.includes(run.phase);
    const steps = runSteps(run, phases);
    const at = steps.findIndex((s) => s.state === "doing");
    const note = boards
      ? [`${n(run.done)} of ${n(boards)}`, timeLeft(run.done, boards, boardsStartedAt)].filter(Boolean).join(", ")
      : at >= 0 ? `Step ${at + 1} of ${steps.length}` : run.source === "schedule" ? "Your scheduled run" : run.label;
    return (
      <div className="run-box" role="status" aria-live="polite">
        <div className="run-box-status">
          <span className="run-box-phase">{phaseText(run)}</span>
          <span className="run-box-note num">{note}</span>
          {run.new.length > 0 && <span className="run-box-note">{plural(run.new.length, "new match", "new matches")} so far</span>}
        </div>
        <LineProgress share={boards && !run.cancelled ? run.done / boards : null} label="Run progress" />
        {finishing ? (
          <span className="run-box-note">The boards are read. Finishing up, which can't be stopped now.</span>
        ) : (
          <button className="button small wide" onClick={cancel} disabled={busy || run.cancelled}>
            {run.cancelled ? "Stopping" : "Cancel run"}
          </button>
        )}
      </div>
    );
  }

  if (busyElsewhere) {
    return (
      <div className="run-box" role="status">
        <div className="run-box-status">
          <span className="run-box-phase">A run is going elsewhere</span>
          <span className="run-box-note">In a terminal or the scheduled task. This window refreshes when it ends.</span>
        </div>
        <LineProgress share={null} label="A run elsewhere" />
      </div>
    );
  }

  return (
    <div className="run-box" ref={box}>
      <span className="run-box-note">{lastRun ? `Last run ${when(lastRun)}` : "No runs yet"}</span>
      <button className="button paint wide" onClick={() => start("full")} disabled={busy}>Run now</button>
      <div className="run-more">
        <button className="text-button" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>Other ways to run</button>
        {menu && (
          <div className="popover" role="menu">
            {OTHER_RUNS.map((r) => (
              <button key={r.mode} className="menu-item" role="menuitem" disabled={busy}
                onClick={() => { setMenu(false); start(r.mode); }}>
                {r.label}<small>{r.what}</small>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
