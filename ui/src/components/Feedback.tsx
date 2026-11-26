import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { toast } from "../live";
import { External, Icon } from "./bits";

// Report a problem or suggest a feature. Pathfinder holds no GitHub key and posts nothing itself: it
// fills in GitHub's own form and opens it in your browser, where you add screenshots and press Submit.
// Problems become issues (the bug_report.yml form); ideas become posts in Discussions, Ideas, where
// people upvote them and the most wanted rise to the top.

type Info = { repo: string; details: string; log: string };
type Kind = "problem" | "idea";

const MAX_URL = 7500; // GitHub refuses very long links; the log is trimmed first to fit

function url(base: string, params: Record<string, string>) {
  const q = Object.entries(params).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  return `${base}?${q}`;
}

function detailsBlock(info: Info, withLog: boolean, logLines?: string[]) {
  const log = withLog ? (logLines ?? info.log.split("\n")).join("\n").trim() : "";
  return info.details + (log ? `\n\nRecent log:\n${log}` : "");
}

/** The issue link, with the log shortened from its oldest lines until the link fits. */
export function problemUrl(info: Info, f: ProblemFields) {
  const base = `https://github.com/${info.repo}/issues/new`;
  let lines = f.withLog ? info.log.split("\n") : [];
  for (;;) {
    const link = url(base, {
      template: "bug_report.yml", labels: "bug", title: f.title.trim(),
      "what-happened": f.what.trim(), steps: f.steps.trim(), expected: f.expected.trim(),
      details: f.withDetails ? detailsBlock(info, f.withLog, lines) : "",
    });
    if (link.length <= MAX_URL || lines.length === 0) return link;
    lines = lines.slice(Math.ceil(lines.length / 4));
  }
}

export function ideaUrl(info: Info, f: IdeaFields) {
  const body = [`**The idea**\n${f.idea.trim()}`, f.why.trim() && `**Why it would help**\n${f.why.trim()}`]
    .filter(Boolean).join("\n\n");
  return url(`https://github.com/${info.repo}/discussions/new`, { category: "ideas", title: f.title.trim(), body });
}

type ProblemFields = { title: string; what: string; steps: string; expected: string; withDetails: boolean; withLog: boolean };
type IdeaFields = { title: string; idea: string; why: string };

function reportText(kind: Kind, info: Info, p: ProblemFields, i: IdeaFields) {
  if (kind === "idea") return `Idea: ${i.title}\n\n${i.idea}${i.why ? `\n\nWhy it would help:\n${i.why}` : ""}`;
  return [`Problem: ${p.title}`, `What happened:\n${p.what}`, p.steps && `Steps:\n${p.steps}`,
    p.expected && `What I expected:\n${p.expected}`, p.withDetails && detailsBlock(info, p.withLog)]
    .filter(Boolean).join("\n\n");
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied. Paste it into an email or message to Dixant.", "good");
  } catch {
    toast("Couldn't reach the clipboard. Select the text and press Ctrl+C instead.", "bad");
  }
}

/** The Feedback button in the top bar, and its dialog. */
export function FeedbackButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="text-button feedback-button" onClick={() => setOpen(true)}>Feedback</button>
      {open && createPortal(<FeedbackDialog onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

function FeedbackDialog({ onClose }: { onClose: () => void }) {
  const query = useQuery({ queryKey: ["feedback"], queryFn: () => api<Info>("feedback"), staleTime: 0, retry: false });
  // without the app details (the app couldn't read them) a report still works, just without them
  const info = { ...query, data: query.data ?? (query.isError ? { repo: "PDixant/findJobApplications", details: "", log: "" } : undefined) };
  const [kind, setKind] = useState<Kind>("problem");
  const [p, setP] = useState<ProblemFields>({ title: "", what: "", steps: "", expected: "", withDetails: true, withLog: true });
  const [i, setI] = useState<IdeaFields>({ title: "", idea: "", why: "" });
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, kind]);

  const ready = kind === "problem" ? Boolean(p.title.trim() && p.what.trim()) : Boolean(i.title.trim() && i.idea.trim());
  const link = useMemo(() => {
    if (!info.data || !ready) return "";
    return kind === "problem" ? problemUrl(info.data, p) : ideaUrl(info.data, i);
  }, [info.data, ready, kind, p, i]);
  const repo = info.data?.repo ?? "PDixant/findJobApplications";

  return (
    <div className="scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal feedback" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
        <h2 id="feedback-title">{kind === "problem" ? "Report a problem" : "Suggest a feature"}</h2>
        <div className="segmented" role="group" aria-label="What kind of feedback">
          <button type="button" aria-pressed={kind === "problem"} onClick={() => setKind("problem")}>Something's wrong</button>
          <button type="button" aria-pressed={kind === "idea"} onClick={() => setKind("idea")}>I have an idea</button>
        </div>

        {kind === "problem" ? (
          <div className="feedback-fields">
            <label>In a few words
              <input ref={first} value={p.title} maxLength={120} placeholder="Run stops at 'Finishing up'"
                onChange={(e) => setP({ ...p, title: e.target.value })} />
            </label>
            <label>What happened
              <textarea rows={3} value={p.what} placeholder="What you saw, and any message on screen"
                onChange={(e) => setP({ ...p, what: e.target.value })} />
            </label>
            <label><span>How to make it happen <span className="optional">optional</span></span>
              <textarea rows={3} value={p.steps} placeholder={"1. Open Settings\n2. Press Save\n3. ..."}
                onChange={(e) => setP({ ...p, steps: e.target.value })} />
            </label>
            <label><span>What you expected <span className="optional">optional</span></span>
              <textarea rows={2} value={p.expected} onChange={(e) => setP({ ...p, expected: e.target.value })} />
            </label>
            <label className="check">
              <input type="checkbox" checked={p.withDetails} onChange={(e) => setP({ ...p, withDetails: e.target.checked })} />
              Include Pathfinder's version and my system
            </label>
            <label className="check">
              <input type="checkbox" checked={p.withDetails && p.withLog} disabled={!p.withDetails}
                onChange={(e) => setP({ ...p, withLog: e.target.checked })} />
              Include the last lines of the log
            </label>
            {p.withDetails && info.data && (
              <details className="feedback-preview">
                <summary>See exactly what's included</summary>
                <pre>{detailsBlock(info.data, p.withLog)}</pre>
                <p className="hint">Keys, passwords and your user folder's name are taken out. The log can name companies and job titles from your runs.</p>
              </details>
            )}
            <p className="hint feedback-next">
              <Icon name="info" size={16} /> Next, GitHub opens in your browser with this filled in. Add screenshots or files there:
              drag them into any box, or paste a screenshot with Ctrl+V (Windows+Shift+S takes one). Then press Submit.
              You need a free GitHub account.
            </p>
          </div>
        ) : (
          <div className="feedback-fields">
            <label>The idea, in a few words
              <input ref={first} value={i.title} maxLength={120} placeholder="Filter jobs by salary"
                onChange={(e) => setI({ ...i, title: e.target.value })} />
            </label>
            <label>What it would do
              <textarea rows={3} value={i.idea} onChange={(e) => setI({ ...i, idea: e.target.value })} />
            </label>
            <label><span>How it would help you <span className="optional">optional</span></span>
              <textarea rows={3} value={i.why} onChange={(e) => setI({ ...i, why: e.target.value })} />
            </label>
            <p className="hint feedback-next">
              <Icon name="info" size={16} /> Ideas go to Pathfinder's Ideas board on GitHub, where anyone can upvote them. The most
              wanted rise to the top. Before posting, <External href={`https://github.com/${repo}/discussions/categories/ideas?discussions_q=is%3Aopen+category%3AIdeas+sort%3Atop`}>
              see what others have asked for</External>: if yours is there, an upvote counts too.
            </p>
          </div>
        )}

        <div className="card-foot feedback-foot">
          {link ? (
            <External href={link} className="button primary">Continue on GitHub</External>
          ) : (
            <button type="button" className="button primary" disabled>Continue on GitHub</button>
          )}
          <button type="button" className="text-button" disabled={!ready || !info.data}
            onClick={() => info.data && copy(reportText(kind, info.data, p, i))}
            title="No GitHub account? Copy it and send it to Dixant another way">
            Copy instead
          </button>
          <button type="button" className="text-button" onClick={onClose}>Cancel</button>
        </div>
        {info.isError && <p className="hint">Couldn't read the app details. You can still continue; they just won't be included.</p>}
      </div>
    </div>
  );
}
