import { useSyncExternalStore } from "react";

export type Screen = "dashboard" | "jobs" | "postings" | "pipeline" | "outreach" | "companies" | "activity" | "settings";
const SCREENS: Screen[] = ["dashboard", "jobs", "postings", "pipeline", "outreach", "companies", "activity", "settings"];

export type Route = { screen: Screen; params: URLSearchParams };

function parse(): Route {
  const [path, query = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
  const screen = (SCREENS as string[]).includes(path) ? (path as Screen) : "dashboard";
  return { screen, params: new URLSearchParams(query) };
}

let current = parse();
const listeners = new Set<() => void>();
window.addEventListener("hashchange", () => {
  current = parse();
  listeners.forEach((l) => l());
});

export function useRoute(): Route {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
  );
}

export function go(screen: Screen, params: Record<string, string> = {}) {
  const query = new URLSearchParams(params).toString();
  window.location.hash = `#/${screen}${query ? `?${query}` : ""}`;
}
