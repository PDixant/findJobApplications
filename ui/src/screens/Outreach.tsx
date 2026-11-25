import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, send, type Draft, type OutreachCompany, type OutreachData, type Person } from "../api";
import { Confirm, Empty, ErrorNote, External, Icon, Skeleton } from "../components/bits";
import { useClaudeConsent } from "../components/ClaudeConsent";
import { ago, day, n, plural } from "../format";
import { toast } from "../live";
import { go, useRoute } from "../router";
import { useJobs } from "../queries";

const STAGES: { id: OutreachCompany["stage"]; title: string }[] = [
  { id: "follow_up", title: "Follow-ups due" },
  { id: "to_write", title: "To write" },
  { id: "drafts", title: "Drafts to send" },
  { id: "sent", title: "Waiting for a reply" },
  { id: "replied", title: "Replied" },
];

export const useOutreach = () => useQuery({ queryKey: ["outreach"], queryFn: () => api<OutreachData>("outreach") });

export function Outreach() {
  const data = useOutreach();
  const route = useRoute();
  const picked = route.params.get("c");
  const companies = data.data?.companies ?? [];
  const selected = companies.find((c) => c.company === picked) ?? companies.find((c) => c.stage !== "replied") ?? null;
  useEffect(() => {
    // Keep the company you're working on selected when the list re-sorts after writing or sending.
    if (selected && selected.company !== picked) {
      history.replaceState(null, "", `#/outreach?${new URLSearchParams({ c: selected.company })}`);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  }, [selected?.company, picked]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <header className="page-head">
        <h1>Outreach</h1>
        <p className="lede">
          Write to a recruiter and an engineering lead at companies you applied to. Claude drafts each email from your
          about-me file, your resume and the job. You review it, send it from your own mail app, and mark it sent.
          Nothing is sent for you.
        </p>
      </header>
      <hr className="character" style={{ marginBottom: 24 }} />
      <ErrorNote error={data.error} />
      {data.data && <Accounts data={data.data} />}
      {!data.data ? <Skeleton height={320} style={{ borderRadius: 12 }} /> : !companies.length ? (
        <Empty title="Nothing to write yet"
          action={<button className="button primary" onClick={() => go("jobs")}>Open Jobs</button>}>
          In Jobs, set Outreach to Email on the jobs you want to write about. They show up here.
        </Empty>
      ) : (
        <div className="outreach-layout">
          <nav className="outreach-list" aria-label="Companies">
            {STAGES.map((s) => {
              const items = companies.filter((c) => c.stage === s.id);
              if (!items.length) return null;
              return (
                <section key={s.id}>
                  <h2 className="eyebrow">{s.title} <span className="num">{items.length}</span></h2>
                  {items.map((c) => (
                    <button key={c.company} className={`outreach-item ${selected?.company === c.company ? "is-on" : ""}`}
                      aria-current={selected?.company === c.company ? "true" : undefined}
                      onClick={() => go("outreach", { c: c.company })}>
                      {c.company}
                      <small>{summaryOf(c)}</small>
                    </button>
                  ))}
                </section>
              );
            })}
          </nav>
          {selected && data.data && <Workspace key={selected.company} c={selected} data={data.data} />}
        </div>
      )}
    </>
  );
}

function summaryOf(c: OutreachCompany) {
  const open = c.drafts.filter((d) => d.status === "draft" || d.status === "opened").length;
  if (c.stage === "drafts") return plural(open, "draft");
  if (c.stage === "follow_up") return `sent ${ago(c.jobs.find((j) => j.outreach_sent)?.outreach_sent)}`;
  if (c.stage === "sent") return `sent ${ago(c.drafts.filter((d) => d.sent).pop()?.sent)}`;
  return plural(c.jobs.length, "job");
}

function Accounts({ data }: { data: OutreachData }) {
  return (
    <div className="accounts">
      <span>{data.hunter.key_set ? `${n(data.hunter.used)} of ${n(data.hunter.limit)} Hunter credits used this month` : "No Hunter key, so finding people is off"}</span>
      <span>{data.writer.key_set ? `Drafts written by ${data.writer.model}` : "No Anthropic key, so drafts come from your Claude agent"}</span>
      {(!data.hunter.key_set || !data.writer.key_set) && (
        <button className="text-button" onClick={() => go("settings", { tab: "keys" })}>Add keys in Settings</button>
      )}
    </div>
  );
}

function useRefresh() {
  const client = useQueryClient();
  return () => {
    client.invalidateQueries({ queryKey: ["outreach"] });
    client.invalidateQueries({ queryKey: ["jobs"] });
  };
}

async function attempt<T>(fn: () => Promise<T>, done?: string): Promise<T | undefined> {
  try {
    const out = await fn();
    if (done) toast(done, "good");
    return out;
  } catch (e) {
    toast(e instanceof Error ? e.message : "That didn't go through. Try again.", "bad");
    return undefined;
  }
}

function Workspace({ c, data }: { c: OutreachCompany; data: OutreachData }) {
  const jobs = useJobs();
  const refresh = useRefresh();
  const followUp = c.stage === "follow_up";
  const open = c.drafts.filter((d) => d.status === "draft" || d.status === "opened");
  const sent = c.drafts.filter((d) => d.status === "sent" || d.status === "replied");
  const recipients: Person[] = useMemo(() => {
    if (followUp) {
      const seen = new Set<string>();
      return sent.filter((d) => !d.follow_up && !seen.has(d.to_email) && seen.add(d.to_email)).map((d) => ({
        name: d.to_name, position: d.position, email: d.to_email, kind: d.kind, executive: false, confidence: 0,
        verification: "", linkedin: "",
      }));
    }
    return c.contacts?.people ?? [];
  }, [c, followUp, sent]);
  const [chosen, setChosen] = useState<string[]>([]);
  useEffect(() => setChosen(followUp ? recipients.map((p) => p.email) : c.contacts?.suggested ?? []), [c.company, c.contacts, followUp]); // eslint-disable-line react-hooks/exhaustive-deps
  const [busy, setBusy] = useState("");
  const consent = useClaudeConsent();

  async function findPeople() {
    setBusy("find");
    await attempt(() => send("outreach/contacts", "POST", { company: c.company }));
    setBusy("");
    refresh();
  }
  async function write() {
    if (!(await consent.ensure())) return;
    setBusy("write");
    const made = await attempt(() => send<Draft[]>("outreach/write", "POST", { company: c.company, emails: chosen, follow_up: followUp }));
    setBusy("");
    if (made) toast(`${plural(made.length, "draft")} written. Review ${made.length === 1 ? "it" : "them"} below.`, "good");
    refresh();
  }

  const canWrite = followUp || c.stage === "to_write" || (c.stage === "drafts" && !open.length);
  const known = Math.max(0, ...(jobs.data ?? []).filter((j) => j.company === c.company).map((j) => j.connections));
  const toggle = (email: string, on: boolean) => setChosen(on ? [...chosen, email] : chosen.filter((x) => x !== email));
  return (
    <section className="card workspace" aria-labelledby="ws-title">
      {consent.dialog}
      <header className="card-head" style={{ alignItems: "flex-end" }}>
        <h2 id="ws-title">{c.company}</h2>
        {c.stage !== "replied" && sent.length > 0 && (
          <button className="button small" onClick={() => attempt(() => send("outreach/replied", "POST", { company: c.company }), "Marked as replied.").then(refresh)}>
            They replied
          </button>
        )}
      </header>
      <ul className="job-lines">
        {c.jobs.map((j) => (
          <li key={j.key}>
            <External href={j.url}>{j.title}</External>{" "}
            <span className="quiet">{j.region}{j.applied ? `, applied ${day(j.applied)}` : `, ${j.status}`}{j.outreach ? `, ${j.outreach}` : ""}</span>
          </li>
        ))}
      </ul>
      {known > 0 && (
        <p className="known-note">
          You know {plural(known, "person", "people")} at {c.company} on LinkedIn. A referral usually beats a cold email;
          open a {c.company} match in Jobs to see who.
        </p>
      )}

      {canWrite && !followUp && (
        <div className="workspace-section">
          <h3>Who to write to</h3>
          {!c.contacts ? (
            <div className="write-row" style={{ marginTop: 0 }}>
              <button className="button" onClick={findPeople} disabled={busy !== "" || !data.hunter.key_set}>
                {busy === "find" ? "Finding people" : `Find people at ${c.company}`}
              </button>
              <span className="hint">Uses one Hunter credit. The result is kept for 60 days.</span>
            </div>
          ) : recipients.length ? (
            <>
              <ul className="person-list">
                {recipients.map((p) => (
                  <li key={p.email}>
                    <label className={`person-choice ${chosen.includes(p.email) ? "is-on bracketed" : ""}`}>
                      <input type="checkbox" checked={chosen.includes(p.email)} onChange={(e) => toggle(p.email, e.target.checked)} />
                      <span className="person">
                        <strong>{p.name}</strong>
                        <span className="secondary">{p.position || "Role unknown"}</span>
                        <span className="person-tags">
                          <span>{p.kind}</span>
                          {p.executive && <span className="badge warn" title="Executives at large companies rarely answer job emails">Executive</span>}
                          <span>{p.email}, {p.verification}</span>
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
              <p className="hint" style={{ marginTop: 12 }}>Picked for you: a recruiter first, then an engineering leader. Executives are left out unless nobody else was found.</p>
            </>
          ) : (
            <p className="hint">Hunter found nobody at {c.contacts.domain || c.company}{c.contacts.error ? ` (${c.contacts.error})` : ""}. Try LinkedIn:</p>
          )}
          <p className="linkedin-links">
            {c.linkedin.map((url, i) => <External key={url} href={url}>{i ? "Engineering managers on LinkedIn" : "Recruiters on LinkedIn"}</External>)}
          </p>
        </div>
      )}
      {followUp && (
        <div className="workspace-section">
          <h3>Follow up with</h3>
          <ul className="person-list">
            {recipients.map((p) => (
              <li key={p.email}>
                <label className={`person-choice ${chosen.includes(p.email) ? "is-on bracketed" : ""}`}>
                  <input type="checkbox" checked={chosen.includes(p.email)} onChange={(e) => toggle(p.email, e.target.checked)} />
                  <span className="person"><strong>{p.name || p.email}</strong> <span className="secondary">{p.position}</span></span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
      {canWrite && recipients.length > 0 && (
        <div className="write-row">
          {data.writer.key_set ? (
            <button className="button primary" onClick={write} disabled={!chosen.length || busy !== ""}>
              {busy === "write" ? "Claude is writing, about 20 seconds" : `Write ${plural(chosen.length, followUp ? "follow-up" : "draft")} with Claude`}
            </button>
          ) : (
            <NoWriter data={data} />
          )}
        </div>
      )}

      {open.length > 0 && (
        <div className="workspace-section">
          <h3>Drafts</h3>
          {open.map((d, i) => <DraftForm key={d.id} d={d} gmail={data.gmail} first={i === 0} />)}
        </div>
      )}
      {sent.length > 0 && (
        <details className="workspace-section sent-list">
          <summary>Sent ({sent.length})</summary>
          <ul>
            {sent.map((d) => (
              <li key={d.id}>{d.subject} <span className="quiet">to {d.to_name || d.to_email}, {day(d.sent)}{d.status === "replied" ? ", replied" : ""}</span></li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function NoWriter({ data }: { data: OutreachData }) {
  const refresh = useRefresh();
  return (
    <div>
      <p className="hint">
        Add an Anthropic key in Settings to write drafts here. Or make a packet for your Claude agent, then load the
        emails.json it writes.
      </p>
      <div className="form-actions" style={{ marginTop: 16 }}>
        <button className="button" onClick={() => attempt(() => send<{ folder: string }>("outreach/packet", "POST")).then((r) => r && toast(`Packet ready in ${r.folder}. Give your Claude agent its INSTRUCTIONS.md.`, "good"))}>
          Make a packet
        </button>
        {data.packets.filter((p) => p.has_emails).slice(0, 3).map((p) => (
          <button key={p.folder} className="text-button" onClick={() => attempt(() => send("outreach/import", "POST", { folder: p.folder }), `Loaded drafts from ${p.name}.`).then(refresh)}>
            Load {p.name}
          </button>
        ))}
      </div>
    </div>
  );
}

function DraftForm({ d, gmail, first }: { d: Draft; gmail: boolean; first: boolean }) {
  const refresh = useRefresh();
  const [subject, setSubject] = useState(d.subject);
  const [body, setBody] = useState(d.body);
  const changed = subject !== d.subject || body !== d.body;
  async function save() {
    if (changed) await attempt(() => send(`outreach/drafts/${d.id}`, "PATCH", { subject, body }));
  }
  async function act(path: string, done: string) {
    await save();
    await attempt(() => send(`outreach/drafts/${d.id}/${path}`, "POST"), done);
    refresh();
  }
  const copy = (text: string, what: string) => navigator.clipboard?.writeText(text).then(() => toast(`${what} copied.`, "good"));
  return (
    <article className="draft" aria-label={`Draft to ${d.to_name || d.to_email}`}>
      <p className="draft-to">
        To <strong>{d.to_name || d.to_email}</strong> <span className="quiet">&lt;{d.to_email}&gt;{d.position ? `, ${d.position}` : ""}</span>
        {d.follow_up ? <span className="badge">Follow-up</span> : d.status === "opened" ? <span className="badge info">Opened in mail</span> : null}
      </p>
      <label className="field-stack">Subject<input value={subject} onChange={(e) => setSubject(e.target.value)} onBlur={save} /></label>
      <label className="field-stack">Email
        <textarea value={body} rows={Math.min(16, Math.max(7, body.split("\n").length + 2))} onChange={(e) => setBody(e.target.value)} onBlur={save} />
      </label>
      <p className="hint">
        {n(body.trim().split(/\s+/).length)} words.{d.attach ? ` Attaches ${d.attach.split("/").pop()}; check it's attached before you send.` : ""}
      </p>
      {d.linkedin_note && (
        <p className="linkedin-note"><span className="quiet">LinkedIn note:</span> {d.linkedin_note}{" "}
          <button className="text-button" onClick={() => copy(d.linkedin_note, "LinkedIn note")}>Copy</button></p>
      )}
      <div className="form-actions">
        <button className={first ? "button primary" : "button"} onClick={() => act("open", "Opened in your mail app as a draft.")}>Open in mail app <Icon name="external" /></button>
        {gmail && <button className="button" onClick={() => act("gmail", "Saved to Gmail Drafts.")}>Gmail draft</button>}
        <button className="button" onClick={() => copy(`Subject: ${subject}\n\n${body}`, "Email")}>Copy</button>
        <button className="button" onClick={() => act("sent", "Marked sent. A follow-up is suggested if there's no reply.")}>I sent it</button>
        <Confirm label="Delete" question={`Delete this draft to ${d.to_name || d.to_email}? You can't undo this.`} yes="Delete draft"
          onYes={() => attempt(() => send(`outreach/drafts/${d.id}`, "DELETE"), "Draft deleted.").then(refresh)} />
      </div>
    </article>
  );
}
