import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, send, type UpdateInfo, type UpdateResult } from "../api";
import { External, Jewels, Logo, useDismiss } from "./bits";
import { APP_NAME, AUTHOR, REPO_URL } from "../brand";
import { toast } from "../live";
import { go } from "../router";

const THREE_HOURS = 3 * 60 * 60 * 1000;
const DISMISSED = "jw.updateDismissed";

/** Whether GitHub has a newer Pathfinder. The server asks GitHub at most every few hours. */
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
      toast(e instanceof Error ? e.message : "The update didn't go through. Nothing changed.", "bad");
    }
  }
  return { state, run };
}

export const changeCount = (u: UpdateInfo) =>
  u.behind ? `${u.behind} ${u.behind === 1 ? "change" : "changes"}` : "a new version";

/** While Pathfinder restarts on the new version. */
export function UpdatingNote({ state }: { state: string }) {
  if (state !== "restarting" && state !== "reopen") return null;
  return (
    <div className="scrim" role="status">
      <div className="modal">
        <h2>{state === "restarting" ? "Updated" : "Updated. Open it again"}</h2>
        <p>{state === "restarting"
          ? "Pathfinder closes and opens on the new version in a few seconds. Your settings and jobs are as you left them."
          : "Close Pathfinder and open it again to use the new version."}</p>
        {state === "restarting" && <p style={{ marginTop: 24 }}><Jewels lit /></p>}
      </div>
    </div>
  );
}

/** In the top bar: a quiet pill when a new version is ready, with what changed and the button. */
export function UpdateNotice() {
  const update = useUpdate();
  const apply = useApplyUpdate();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(DISMISSED) ?? ""; } catch { return ""; }
  });
  const box = useRef<HTMLDivElement>(null);
  useDismiss(box, open, () => setOpen(false));
  const u = update.data;
  if (!u?.newer || u.latest === dismissed) return <UpdatingNote state={apply.state} />;
  const later = () => {
    setDismissed(u.latest ?? "");
    setOpen(false);
    try { localStorage.setItem(DISMISSED, u.latest ?? ""); } catch { /* storage unavailable */ }
  };
  return (
    <div ref={box} style={{ position: "relative" }}>
      <button className="update-pill" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="dot" aria-hidden="true" />New version ready
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="New version">
          <p className="t-title-s">Pathfinder has {changeCount(u)}</p>
          {u.changes.length > 0 && (
            <ul className="update-list">
              {u.changes.slice(0, 4).map((c) => <li key={c.sha + c.message}>{c.message || "Changes"}</li>)}
              {u.changes.length > 4 && <li>and {u.changes.length - 4} more</li>}
            </ul>
          )}
          <p className="hint" style={{ marginTop: 16 }}>Your settings, company list, jobs and keys stay as they are.</p>
          <div className="card-foot" style={{ marginTop: 20 }}>
            {u.can_update ? (
              <button className="button primary small" onClick={apply.run} disabled={apply.state !== "idle"}>
                {apply.state === "working" ? "Updating" : "Update now"}
              </button>
            ) : u.url ? <External href={u.url}>Download the new version</External> : null}
            <button className="text-button" onClick={() => { setOpen(false); go("settings", { tab: "updates" }); }}>What's new</button>
            <button className="text-button" onClick={later}>Not now</button>
          </div>
        </div>
      )}
      <UpdatingNote state={apply.state} />
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
    <section className="card settings-section" aria-labelledby="sec-updates">
      <h2 id="sec-updates">Updates</h2>
      <div className="about-app">
        <Logo size={48} />
        <div>
          <p className="t-title-s">{APP_NAME}</p>
          <p className="hint">{have}. Made by <External href={REPO_URL}>{AUTHOR}</External>; the code is on GitHub.</p>
        </div>
      </div>
      <p style={{ marginTop: 16 }}>{u.newer ? `There's a newer version: ${changeCount(u)}.` : "You have the latest version."}</p>
      {u.problem && <p className="hint" style={{ marginTop: 8 }}>{u.problem}</p>}
      {u.newer && u.changes.length > 0 && (
        <ul className="update-changes">
          {u.changes.slice(0, 10).map((c) => (
            <li key={c.sha + c.message}>
              <span>{c.message || "Changes"}</span>
              {c.date && <span className="quiet num">{new Date(c.date).toLocaleDateString()}</span>}
            </li>
          ))}
          {u.changes.length > 10 && <li className="quiet">and {u.changes.length - 10} more</li>}
        </ul>
      )}
      <div className="card-foot">
        {u.newer && u.can_update && (
          <button className="button primary" onClick={apply.run} disabled={apply.state !== "idle"}>
            {apply.state === "working" ? "Updating" : "Update now"}
          </button>
        )}
        {u.newer && !u.can_update && u.url && <External href={u.url}>Download the new version</External>}
        <button className="button" onClick={checkNow} disabled={checking}>{checking ? "Checking" : "Check for updates"}</button>
        {u.url && u.newer && u.can_update && <External href={u.url}>See the changes on GitHub</External>}
      </div>
      <p className="hint" style={{ marginTop: 24 }}>
        {u.kind === "git" ? "This folder is a git clone, so updating pulls the newest code from GitHub."
          : u.kind === "download" ? "Updating downloads the newest code from GitHub into this folder."
          : "New versions come as a new installer."}{" "}
        Your settings, company list, jobs and keys are never touched. Checked {new Date(u.checked).toLocaleString()}.
      </p>
      <UpdatingNote state={apply.state} />
    </section>
  );
}
