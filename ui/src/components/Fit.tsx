import type { Job, Pay, Verdict } from "../api";

const VERDICT: Record<Verdict, { label: string; tone: string }> = {
  APPLY: { label: "Apply", tone: "apply" },
  STRETCH: { label: "Stretch", tone: "stretch" },
  SKIP: { label: "Skip", tone: "skip" },
};

export function FitBadge({ fit, large }: { fit: Job["fit"]; large?: boolean }) {
  if (!fit) return <span className="quiet">Not scored</span>;
  const v = VERDICT[fit.verdict] ?? VERDICT.STRETCH;
  return (
    <span className={`fit fit-${v.tone} ${large ? "is-large" : ""}`} title={fit.summary}>
      {v.label} <span className="num">{fit.score}</span>
    </span>
  );
}

const short = (x: number) => (x >= 1000 ? `${Math.round(x / 1000)}k` : String(x));

export function payText(pay: Pay | null | undefined): string {
  if (!pay) return "";
  const range = pay.min === pay.max ? short(pay.min) : `${short(pay.min)}-${short(pay.max)}`;
  return `$${range}${pay.period === "hour" ? " an hour" : ""} ${pay.currency}`;
}

const WORKPLACE: Record<string, string> = { remote: "Remote", hybrid: "Hybrid", "on-site": "On-site" };

/** Small facts read from the posting, shown under a job's title. */
export function Flags({ job }: { job: Job }) {
  const flags: { text: string; tone: string; title?: string }[] = [];
  if (job.level === "Senior/Lead") flags.push({ text: "Senior/Lead", tone: "plain" });
  if (job.workplace) flags.push({ text: WORKPLACE[job.workplace], tone: "plain" });
  if (job.connections) flags.push({
    text: `${job.connections} ${job.connections === 1 ? "person" : "people"} you know`, tone: "good",
    title: "From your LinkedIn connections",
  });
  if (job.years) flags.push({
    text: `Asks ${job.years}+ yrs`, tone: job.years_over ? "warn" : "plain",
    title: job.years_over ? "Asks for more years of experience than you set in Settings" : "Years of experience the posting asks for",
  });
  for (const a of job.work_auth ?? []) flags.push({ text: a.label, tone: "warn" });
  if (job.relocation) flags.push({ text: "Relocation help", tone: "good" });
  if (job.reposted) flags.push({ text: "Reposted", tone: "plain", title: "The same title was posted here before" });
  if (job.long_open) flags.push({ text: `Open ${job.open_days} days`, tone: "plain", title: "Up for a long time; may be hard to fill or stale" });
  if (job.tailored) flags.push({ text: "Resume tailored", tone: "good" });
  if (!flags.length) return null;
  return (
    <span className="flags">
      {flags.map((f) => <span key={f.text} className={`flag flag-${f.tone}`} title={f.title}>{f.text}</span>)}
    </span>
  );
}

export const WORKPLACES = WORKPLACE;
