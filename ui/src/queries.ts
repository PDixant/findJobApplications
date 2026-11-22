import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, send, type Company, type Job, type JobEdit, type Meta, type Summary } from "./api";
import { toast } from "./live";

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

// ------------------------------------------------------------------ edits

/** Save a job's Status, Notes or Outreach. The screen updates at once; a refusal undoes it. */
export function useEditJob() {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: ({ key, changes }: { key: string; changes: JobEdit; quiet?: boolean }) =>
      send<JobEdit>(`jobs?key=${encodeURIComponent(key)}`, "PATCH", changes),
    onMutate: async ({ key, changes }) => {
      await client.cancelQueries({ queryKey: ["jobs"] });
      const before = client.getQueryData<Job[]>(["jobs"]);
      client.setQueryData<Job[]>(["jobs"], (jobs) => jobs?.map((j) => (j.key === key ? { ...j, ...changes } : j)));
      return { before };
    },
    onError: (e, _vars, ctx) => {
      if (ctx?.before) client.setQueryData(["jobs"], ctx.before);
      toast(e instanceof Error ? e.message : "The change was not saved.", "bad");
    },
    onSuccess: (_data, { key, changes, quiet }, ctx) => {
      // Status and Outreach save on one click, so offer a way back.
      const old = ctx?.before?.find((j) => j.key === key);
      if (quiet || !old || !("status" in changes || "outreach" in changes)) return;
      const what = "status" in changes ? `Status set to ${changes.status}` : `Outreach set to ${changes.outreach || "none"}`;
      const back: JobEdit = "status" in changes ? { status: old.status } : { outreach: old.outreach };
      toast(`${what} for ${old.title}.`, "info", { label: "Undo", run: () => undo({ key, changes: back, quiet: true }) });
    },
    onSettled: () => client.invalidateQueries({ queryKey: ["summary"] }),
  });
  const undo = mutation.mutate;
  return mutation;
}
