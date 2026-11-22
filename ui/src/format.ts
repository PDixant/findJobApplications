const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const dayTimeFmt = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});
const num = new Intl.NumberFormat();

export const n = (value: number | null | undefined) => num.format(value ?? 0);

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return `today ${timeFmt.format(d)}`;
  if (d.toDateString() === yesterday.toDateString()) return `yesterday ${timeFmt.format(d)}`;
  return dayTimeFmt.format(d);
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dayFmt.format(d);
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "";
  const min = Math.round(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? "1 day ago" : `${d} days ago`;
}

export function minutes(fromIso: string, toIso?: string | null): string {
  const ms = (toIso ? new Date(toIso).getTime() : Date.now()) - new Date(fromIso).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min ${String(s % 60).padStart(2, "0")} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export const hex = (c: string | null | undefined) => (c ? `#${c.replace(/^#/, "")}` : undefined);

export function plural(count: number, one: string, many = `${one}s`) {
  return `${n(count)} ${count === 1 ? one : many}`;
}
