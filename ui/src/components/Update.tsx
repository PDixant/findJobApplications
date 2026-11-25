import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, send, type UpdateInfo, type UpdateResult } from "../api";
import { External } from "./bits";
import { toast } from "../live";

const THREE_HOURS = 3 * 60 * 60 * 1000;

/** Whether GitHub has a newer Job Watcher. The server asks GitHub at most every few hours. */
export function useUpdate() {
  return useQuery({ queryKey: ["update"], queryFn: () => api<UpdateInfo>("update"), refetchInterval: THREE_HOURS,
    staleTime: THREE_HOURS });
}

/** Install the update; the app closes and opens again on the new version. */
export function useApplyUpdate() {
  const client = useQueryClient();
  const [state, setState] = useState<"idle" | "working" | "restarting" | "reopen">("idle");
  async function run() {
    setState("working");
    try {
      const r = await send<UpdateResult>("update/apply", "POST");
      setState(r.restart ? "restarting" : "reopen");
      client.invalidateQueries({ queryKey: ["update"] });
    } catch (e) {
      setState("idle");
      toast(e instanceof Error ? e.message : "The update didn't work.", "bad");
    }
  }
  return { state, run };
}

export const changeCount = (u: UpdateInfo) =>
  u.behind ? `${u.behind} ${u.behind === 1 ? "change" : "changes"}` : "a new version";

/** The full-window note while Job Watcher restarts on the new version. */
export function UpdatingNote({ state }: { state: string }) {
  if (state !== "restarting" && state !== "reopen") return null;
  return (
    <div className="updating-note" role="status">
      <div className="panel">
        <h2>{state === "restarting" ? "Updated. Opening the new version..." : "Updated"}</h2>
        <p>{state === "restarting"
          ? "Job Watcher closes and opens again in a few seconds. Your settings and jobs are just as you left them."
          : "Close Job Watcher and open it again to use the new version."}</p>
      </div>
    </div>
  );
}

/** Settings: the version you have, what's new, and the Update button. */
export function UpdatesPanel() {
  const client = useQueryClient();
  const update = useUpdate();
  const apply = useApplyUpdate();
  const [checking, setChecking] = useState(false);
  const u = update.data;
  if (!u) return null;
  async function checkNow() {
    setChecking(true);
    try {
      client.setQueryData(["update"], await api<UpdateInfo>("update?force=true"));
    } finally {
      setChecking(false);
    }
  }
  const have = u.kind === "installed" ? `Version ${u.current}`
    : `Version ${u.current}${u.commit ? ` (${u.commit}${u.date ? `, ${new Date(u.date).toLocaleDateString()}` : ""})` : ""}`;
  return (
    <section className="panel settings-section" aria-labelledby="sec-updates">
      <h2 id="sec-updates">Updates</h2>
      <p>{have}. {u.newer ? `There's a newer version: ${changeCount(u)}.` : "You have the latest version."}</p>
      {u.problem && <p className="hint">{u.problem}</p>}
      {u.newer && u.changes.length > 0 && (
        <ul className="update-changes">
          {u.changes.slice(0, 10).map((c) => (
            <li key={c.sha + c.message}>
              {c.message || "Changes"}
              {c.date && <span className="quiet"> {new Date(c.date).toLocaleDateString()}</span>}
            </li>
          ))}
          {u.changes.length > 10 && <li className="quiet">and {u.changes.length - 10} more</li>}
        </ul>
      )}
      <p className="file-actions">
        {u.newer && u.can_update && (
          <button className="button primary" onClick={apply.run} disabled={apply.state !== "idle"}>
            {apply.state === "working" ? "Updating..." : "Update now"}
          </button>
        )}
        {u.newer && !u.can_update && u.url && <External href={u.url}>Download the new version</External>}
        <button className="button" onClick={checkNow} disabled={checking}>{checking ? "Checking..." : "Check for updates"}</button>
        {u.url && u.newer && u.can_update && <External href={u.url}>See the changes on GitHub</External>}
      </p>
      <p className="hint">
        {u.kind === "git" ? "This folder is a git clone, so updating pulls the newest code from GitHub."
          : u.kind === "download" ? "Updating downloads the newest code from GitHub and puts it in this folder."
          : "New versions come as a new installer."}{" "}
        Your settings, company list, jobs and keys are never touched. Checked {new Date(u.checked).toLocaleString()}.
      </p>
      <UpdatingNote state={apply.state} />
    </section>
  );
}
