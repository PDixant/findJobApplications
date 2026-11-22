import { useQuery } from "@tanstack/react-query";
import { api, type Company, type Job, type Meta, type Summary } from "./api";

export const useMeta = () => useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("meta"), staleTime: 60_000 });

export const useSummary = () =>
  useQuery({
    queryKey: ["summary"],
    queryFn: () => api<Summary>("summary"),
    // Poll faster while a terminal or the scheduled task is running, to see when it ends.
    refetchInterval: (q) => (q.state.data?.busy_elsewhere ? 10_000 : 60_000),
  });

export const useJobs = () => useQuery({ queryKey: ["jobs"], queryFn: () => api<Job[]>("jobs"), refetchInterval: 60_000 });

export const useCompanies = () =>
  useQuery({ queryKey: ["companies"], queryFn: () => api<Company[]>("companies"), refetchInterval: 120_000 });

// When you last looked at the app, so the Dashboard can show what is new since then.
const VISIT_KEY = "jw.lastVisit";
function readVisit(): string | null {
  try {
    const previous = localStorage.getItem(VISIT_KEY);
    localStorage.setItem(VISIT_KEY, new Date().toISOString());
    return previous;
  } catch {
    return null;
  }
}
export const PREVIOUS_VISIT = readVisit();

// Filters the window remembers between visits.
export function loadPrefs<T extends object>(key: string, initial: T): T {
  try {
    const saved = localStorage.getItem(`jw.${key}`);
    return saved ? { ...initial, ...JSON.parse(saved) } : initial;
  } catch {
    return initial;
  }
}

export function savePrefs(key: string, value: object) {
  try {
    localStorage.setItem(`jw.${key}`, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}
