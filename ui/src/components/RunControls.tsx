import { useState } from "react";
import { api, type Run, type RunMode } from "../api";
import { setRun, toast, useLive } from "../live";
import { n, when } from "../format";

export const PHASES: Record<string, string> = {
  starting: "Starting",
  search: "Searching the web for new companies",
  career_pages: "Looking for careers pages",
  metrics: "Updating company scores",
  boards: "Reading job boards",
  filter: "Filtering saved postings",
  save: "Saving your tracker",
  cancelling: "Stopping after the boards being read now",
};

export function phaseText(run: Run) {
  if (run.phase === "boards" && run.total) return `Reading job boards: ${n(run.done)} of ${n(run.total)}`;
  return PHASES[run.phase] ?? "Working";
}

export function useRunActions() {
  const [busy, setBusy] = useState(false);
  async function start(mode: RunMode) {
    setBusy(true);
    try {
      setRun(await api<Run>("runs", { method: "POST", body: JSON.stringify({ mode }) }));
    } catch (e) {
      toast(e instanceof Error ? e.message : "The run did not start.", "bad");
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    setBusy(true);
    try {
      setRun(await api<Run>("runs/current", { method: "DELETE" }));
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not stop the run.", "bad");
    } finally {
      setBusy(false);
    }
  }
  return { start, cancel, busy };
}

/** Run controls in the side rail, so a run can be started or stopped from any screen. */
export function RunControls({ lastRun, busyElsewhere }: { lastRun: string | null; busyElsewhere: boolean }) {
  const { run } = useLive();
  const { start, cancel, busy } = useRunActions();
  const running = run?.status === "running";
  const share = running && run.total ? run.done / run.total : 0;

  if (running) {
    return (
      <div className="rail-run is-running">
        <p className="rail-run-phase">{phaseText(run)}</p>
        <div className="rail-progress" aria-hidden="true">
          <div style={{ width: `${Math.max(3, share * 100)}%` }} />
        </div>
        {run.new.length > 0 && <p className="rail-run-note">{n(run.new.length)} new so far</p>}
        <button className="button quiet-on-dark" onClick={cancel} disabled={busy || run.cancelled}>
          {run.cancelled ? "Stopping" : "Cancel run"}
        </button>
      </div>
    );
  }

  return (
    <div className="rail-run">
      {busyElsewhere ? (
        <p className="rail-run-phase">A run is going in a terminal or the scheduled task.</p>
      ) : (
        <p className="rail-run-note">{lastRun ? `Last run started ${when(lastRun)}` : "No runs yet"}</p>
      )}
      <button className="button primary-on-dark" onClick={() => start("full")} disabled={busy || busyElsewhere}>
        Run now
      </button>
      <button
        className="link-on-dark"
        onClick={() => start("refilter")}
        disabled={busy || busyElsewhere}
        title="Applies your current settings to the postings already saved. Makes no requests."
      >
        Re-filter saved postings
      </button>
      <button
        className="link-on-dark"
        onClick={() => start("since")}
        disabled={busy || busyElsewhere}
        title="Reads every board, then lists what appeared since your last check."
      >
        Run and list since last check
      </button>
    </div>
  );
}
