import { useEffect, useState } from "react";
import { useLive } from "../live";
import { minutes, plural } from "../format";
import { PHASES, phaseText, useRunActions } from "./RunControls";

/** How long the rest of the boards should take, once enough are read to judge. */
function timeLeft(done: number, total: number, since: number): string {
  if (done < 20 || done >= total) return "";
  const left = ((Date.now() - since) / done) * (total - done);
  const mins = Math.round(left / 60000);
  return mins < 1 ? "under a minute left" : `about ${mins} min left`;
}

/** Across the top of every screen while a run is going, whoever started it (a button here or your
 *  schedule), with how far it has got and a way to stop it. */
export function RunBanner({ busyElsewhere }: { busyElsewhere: boolean }) {
  const { run, boardsStartedAt } = useLive();
  const { cancel, busy } = useRunActions();
  const running = run?.status === "running";
  const [, tick] = useState(0);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => tick((t) => t + 1), 1000); // the elapsed time keeps moving
    return () => window.clearInterval(timer);
  }, [running]);

  if (!running) {
    if (!busyElsewhere) return null;
    return (
      <div className="run-banner" role="status">
        <div className="run-banner-text">
          <strong>A run is going in a terminal or the scheduled task</strong>
          <span className="run-banner-meta">Its progress shows there. This window refreshes when it finishes.</span>
        </div>
        <div className="run-banner-bar" aria-hidden="true">
          <div className="is-indeterminate" />
        </div>
      </div>
    );
  }

  const total = run.phase === "boards" && run.total ? run.total : 0;
  const pct = total ? Math.min(100, Math.floor((run.done / total) * 100)) : null;
  const since = boardsStartedAt ?? new Date(run.started).getTime();
  const details = [
    run.source === "schedule" ? "Scheduled run" : run.label,
    `${minutes(run.started)} so far`,
    total ? timeLeft(run.done, total, since) : "",
    run.new.length ? `${plural(run.new.length, "new match", "new matches")} so far` : "",
  ].filter(Boolean);

  return (
    <div className="run-banner" role="status" aria-live="polite">
      <div className="run-banner-text">
        <strong>{run.cancelled ? PHASES.cancelling : phaseText(run)}</strong>
        {pct != null && !run.cancelled && <span className="run-banner-pct num">{pct}%</span>}
        <span className="run-banner-meta">{details.join(" · ")}</span>
      </div>
      <button className="button" onClick={cancel} disabled={busy || run.cancelled}>
        {run.cancelled ? "Stopping" : "Cancel run"}
      </button>
      <div
        className="run-banner-bar"
        role="progressbar"
        aria-label="Run progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct ?? undefined}
      >
        <div
          className={pct == null || run.cancelled ? "is-indeterminate" : undefined}
          style={pct == null || run.cancelled ? undefined : { width: `${Math.max(2, pct)}%` }}
        />
      </div>
    </div>
  );
}
