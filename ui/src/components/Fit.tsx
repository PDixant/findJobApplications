import type { Job, Pay, Verdict } from "../api";
import { Icon } from "./bits";

const VERDICT: Record<Verdict, { label: string; tone: string }> = {
  APPLY: { label: "Apply", tone: "good" },
  STRETCH: { label: "Stretch", tone: "warn" },
  SKIP: { label: "Skip", tone: "bad" },
};

/** Claude's verdict as the row's one badge, with the score beside it in tabular figures. */
export function FitBadge({ fit, score = true }: { fit: Job["fit"]; score?: boolean }) {
  if (!fit) return <span className="quiet">Not scored</span>;
  const v = VERDICT[fit.verdict] ?? VERDICT.STRETCH;
  return (
    <span className="fit-cell" title={fit.summary}>
      <span className={`badge ${v.tone}`}>{v.label}</span>
      {score && <span className="num">{fit.score}</span>}
    </span>
  );
}

export const verdictWord = (v: Verdict | undefined) => (v ? VERDICT[v]?.label : "");

const short = (x: number) => (x >= 1000 ? `${Math.round(x / 1000)}k` : String(x));

/** "$135k–$165k". The currency and "a year" or "an hour" go on the line below. */
export function payRange(pay: Pay | null | undefined): string {
  if (!pay) return "";
  return pay.min === pay.max ? `$${short(pay.min)}` : `$${short(pay.min)}–$${short(pay.max)}`;
}
export const payUnit = (pay: Pay) => `${pay.currency} ${pay.period === "hour" ? "an hour" : "a year"}`;
export function payText(pay: Pay | null | undefined): string {
  return pay ? `${payRange(pay)} ${payUnit(pay)}` : "";
}

const WORKPLACE: Record<string, string> = { remote: "Remote", hybrid: "Hybrid", "on-site": "On-site" };

/** Small facts read from the posting, as one quiet line under the title. Warnings carry an icon. */
export function Flags({ job }: { job: Job }) {
  const plain: string[] = [];
  const good: string[] = [];
  const warn: { text: string; title?: string }[] = [];
  if (job.level === "Senior/Lead") plain.push("Senior/Lead");
  if (job.workplace) plain.push(WORKPLACE[job.workplace]);
  if (job.connections) good.push(`${job.connections} ${job.connections === 1 ? "person" : "people"} you know`);
  if (job.years) {
    if (job.years_over) warn.push({ text: `Asks ${job.years}+ yrs`, title: "More years of experience than you set in Settings" });
    else plain.push(`${job.years}+ yrs`);
  }
  for (const a of job.work_auth ?? []) warn.push({ text: a.label });
  if (job.relocation) good.push("Relocation help");
  if (job.reposted) plain.push("Reposted");
  if (job.long_open) plain.push(`Open ${job.open_days} days`);
  if (job.tailored) good.push("Resume tailored");
  if (!plain.length && !good.length && !warn.length) return null;
  return (
    <span className="flags-line">
      {warn.map((w) => <span key={w.text} className="warn" title={w.title}><Icon name="alert" size={13} />{w.text}</span>)}
      {[...plain, ...good].length > 0 && <span>{[...plain, ...good].join(" · ")}</span>}
    </span>
  );
}

export const WORKPLACES = WORKPLACE;
