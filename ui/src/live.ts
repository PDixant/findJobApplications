// Live run progress and log lines, streamed from the server (Server-Sent Events).
import { useSyncExternalStore } from "react";
import { api, eventsUrl, type Run } from "./api";

export type BoardOutcome = "read" | "missing" | "paused" | "error";

type Toast = { id: number; text: string; tone: "good" | "bad" | "info"; action?: { label: string; run: () => void } };

type LiveState = {
  connected: boolean;
  run: Run | null;
  boards: Record<string, BoardOutcome>; // company -> what happened to its board, for the run in progress
  boardsStartedAt: number | null; // when this window saw board reading start, for "min left"
  phases: string[]; // the steps this run has been through, in order
  log: string[];
  toasts: Toast[];
  version: number; // bumps when a run ends, so screens refetch
};

let state: LiveState = {
  connected: false, run: null, boards: {}, boardsStartedAt: null, phases: [], log: [], toasts: [], version: 0,
};
const listeners = new Set<() => void>();
let toastId = 0;

function set(patch: Partial<LiveState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

export function useLive(): LiveState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}

/** A one-line note at the bottom of the window. It leaves after 4 s (6 s when it offers an action),
 *  except a problem, which stays until you close it. */
export function toast(text: string, tone: Toast["tone"] = "info", action?: Toast["action"]) {
  const id = ++toastId;
  set({ toasts: [...state.toasts, { id, text, tone, action }].slice(-3) });
  if (tone !== "bad") window.setTimeout(() => set({ toasts: state.toasts.filter((t) => t.id !== id) }), action ? 6000 : 4000);
}

export function dismissToast(id: number) {
  set({ toasts: state.toasts.filter((t) => t.id !== id) });
}

export function seedLog(lines: string[]) {
  if (!state.log.length) set({ log: lines });
}

export function replaceLog(lines: string[]) {
  set({ log: lines });
}

/** Apply a run snapshot from the server, unless this window already knows that run ended
 *  (an HTTP reply can arrive after the event that finished the run). */
export function setRun(run: Run | null) {
  const mine = state.run;
  if (run && mine && mine.id === run.id && mine.status !== "running" && run.status === "running") return;
  set({ run });
}

function refreshRun() {
  api<Run | null>("runs/current")
    .then((run) => run && set({ run }))
    .catch(() => undefined);
}

/** Progress for a run this window doesn't know about yet (it opened mid-run): ask for it, at most
 *  every few seconds. */
let lastCatchUp = 0;
function catchUp() {
  if (state.run?.status === "running" || Date.now() - lastCatchUp < 3000) return;
  lastCatchUp = Date.now();
  refreshRun();
}

type Message = { kind: string; [key: string]: unknown };

function finish(status: Run["status"], error = "") {
  if (state.run) set({ run: { ...state.run, status, error, finished: new Date().toISOString() } });
  set({ version: state.version + 1 });
  refreshRun(); // the server's record is the final word on how the run ended
}

function handle(m: Message) {
  const run = state.run;
  switch (m.kind) {
    case "hello":
      set({ connected: true, run: (m.run as Run | null) ?? state.run });
      break;
    case "log":
      set({ log: [...state.log, String(m.line)].slice(-600) });
      break;
    case "run_started": {
      set({ boards: {}, boardsStartedAt: null, phases: [] });
      const started = (m.run as Run | null | undefined) ?? null;
      if (!started) {
        refreshRun();
      } else {
        setRun(started);
        if (started.source === "schedule") {
          toast("Your scheduled run started. Its progress is at the bottom of the side bar.");
        }
      }
      break;
    }
    case "schedule_changed":
    case "job_changed":
      set({ version: state.version + 1 });
      break;
    case "phase":
      if (run) set({ run: { ...run, phase: String(m.name) } });
      if (!state.phases.includes(String(m.name))) set({ phases: [...state.phases, String(m.name)] });
      catchUp();
      break;
    case "boards_started":
      set({ boardsStartedAt: Date.now(), phases: state.phases.includes("boards") ? state.phases : [...state.phases, "boards"] });
      if (run) set({ run: { ...run, phase: "boards", total: Number(m.total), done: 0 } });
      catchUp();
      break;
    case "board_done":
      catchUp();
      set({
        boards: { ...state.boards, [String(m.company)]: (m.outcome as BoardOutcome | undefined) ?? (m.ok ? "read" : "error") },
        run: run
          ? {
              ...run,
              done: Number(m.done),
              total: Number(m.total),
              last_company: String(m.company),
              failed_boards: run.failed_boards + ((m.outcome ?? (m.ok ? "read" : "error")) === "error" ? 1 : 0),
            }
          : run,
      });
      break;
    case "rate_limited":
      if (run) set({ run: { ...run, rate_limited: [...run.rate_limited, String(m.site)].slice(-20) } });
      break;
    case "new_match":
      if (run) set({ run: { ...run, new: [...run.new, String(m.text)] } });
      break;
    case "run_cancelling":
      if (run) set({ run: { ...run, cancelled: true, phase: "cancelling" } });
      break;
    case "run_finished": {
      const n = Number(m.new ?? 0);
      const cancelled = run?.cancelled;
      finish(cancelled ? "cancelled" : "finished");
      toast(
        cancelled
          ? "Run stopped. Boards not read yet are read next run."
          : `Run finished: ${n} new ${n === 1 ? "match" : "matches"}.`,
        n > 0 ? "good" : "info",
      );
      break;
    }
    case "run_failed":
      finish("failed", String(m.error ?? ""));
      toast(`Run failed: ${m.error ?? "see the log"}`, "bad");
      break;
    case "run_skipped":
      finish("skipped", "Another run was going, so this one did not start.");
      toast("Another run was going (a terminal or the scheduled task), so this one did not start.", "bad");
      break;
  }
}

let source: EventSource | null = null;

export function connect() {
  if (source) return;
  source = new EventSource(eventsUrl());
  source.onmessage = (e) => {
    try {
      handle(JSON.parse(e.data));
    } catch {
      /* ignore a malformed message */
    }
  };
  source.onerror = () => set({ connected: false });
}
