import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, send, upload, type AiStatus } from "../api";
import { Confirm, Icon, Meter } from "../components/bits";
import { n } from "../format";
import { toast } from "../live";

async function act(fn: () => Promise<unknown>, done?: string) {
  try {
    await fn();
    if (done) toast(done, "good");
  } catch (e) {
    toast(e instanceof Error ? e.message : "That didn't go through. Try again.", "bad");
  }
}

function Item({ done, children }: { done: boolean; children: React.ReactNode }) {
  return <li className={done ? "is-done" : ""}><Icon name={done ? "check" : "dash"} />{children}</li>;
}

/** Claude's monthly use, what it needs, and a button to score every open match. */
export function ClaudeHelp() {
  const client = useQueryClient();
  const ai = useQuery({ queryKey: ["ai"], queryFn: () => api<AiStatus>("ai"), refetchInterval: (q) => (q.state.data?.scoring ? 5000 : false) });
  const a = ai.data;
  if (!a) return null;
  const score = () => act(async () => {
    const r = await send<{ queued: number }>("jobs/fit/unscored", "POST");
    toast(r.queued ? `Scoring ${n(r.queued)} matches. They fill in as Claude finishes each one.` : "Every open match is already scored.", "info");
    client.invalidateQueries({ queryKey: ["ai"] });
  });
  return (
    <section className="card settings-section" aria-labelledby="sec-claude">
      <h2 id="sec-claude">What Claude can see and use</h2>
      <div style={{ marginTop: 16 }}>
        <Meter label="Claude requests this month" used={a.used} limit={a.limit || 0}
          note={a.key_set ? undefined : "No Anthropic key yet. Add it under Keys."} />
      </div>
      <ul className="checklist">
        <Item done={a.key_set}>{a.key_set ? "Anthropic key saved" : "No Anthropic key yet"}</Item>
        <Item done={a.resume_tex_found}>
          {a.resume_tex_found ? `Base resume found (${a.resume_tex})` : `No ${a.resume_tex} in the Job Watcher folder, so tailoring is off`}
        </Item>
        <Item done={a.rules_found}>{a.rules_found ? "Your resume rules found" : "No rules file, so the built-in resume rules are used"}</Item>
        <Item done={Boolean(a.latex)}>
          {a.latex ? `LaTeX found (${a.latex}), so tailored resumes come with a PDF` : "No LaTeX found. Install MiKTeX or Tectonic for PDFs; until then you get the .tex"}
        </Item>
      </ul>
      <p className="hint">Each request sends one posting, your resume and about_me.md to Anthropic. Nothing else leaves your computer.</p>
      {a.key_set && a.unscored > 0 && (
        <div className="card-foot">
          <button className="button" onClick={score} disabled={a.scoring}>
            {a.scoring ? "Scoring now" : `Score ${n(a.unscored)} open ${a.unscored === 1 ? "match" : "matches"}`}
          </button>
        </div>
      )}
    </section>
  );
}

/** Import LinkedIn's Connections.csv so each company shows who you know there. */
export function Connections() {
  const client = useQueryClient();
  const data = useQuery({ queryKey: ["connections"], queryFn: () => api<{ total: number; companies: Record<string, number> }>("connections") });
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const top = Object.entries(data.data?.companies ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 6);
  async function pick(f: File | undefined) {
    if (!f) return;
    setBusy(true);
    await act(async () => {
      const r = await upload<{ saved: number }>("connections/import", f);
      toast(`Saved ${n(r.saved)} connections.`, "good");
    });
    setBusy(false);
    if (file.current) file.current.value = "";
    client.invalidateQueries({ queryKey: ["connections"] });
    client.invalidateQueries({ queryKey: ["jobs"] });
  }
  return (
    <section className="card settings-section" aria-labelledby="sec-connections">
      <h2 id="sec-connections">People you know</h2>
      <p className="hint">
        On LinkedIn, open Settings, Data privacy, Get a copy of your data, and pick Connections. LinkedIn emails you a
        Connections.csv within about 10 minutes. Import it here and every match shows who you know at that company.
        It stays on this computer.
      </p>
      <p style={{ marginTop: 16 }}>
        {data.data?.total
          ? `${n(data.data.total)} connections saved. You know people at ${n(Object.keys(data.data.companies).length)} of your companies${top.length ? `: ${top.map(([c, k]) => `${c} (${k})`).join(", ")}` : ""}.`
          : "No connections imported yet."}
      </p>
      <div className="card-foot">
        <input ref={file} type="file" accept=".csv" hidden onChange={(e) => pick(e.target.files?.[0])} />
        <button className="button" onClick={() => file.current?.click()} disabled={busy}>
          {data.data?.total ? "Import a newer Connections.csv" : "Import Connections.csv"}
        </button>
        {Boolean(data.data?.total) && (
          <Confirm label="Remove them" yes="Remove connections"
            question={`Remove all ${n(data.data?.total ?? 0)} connections from this computer? You'd import the file again to bring them back.`}
            onYes={() => act(async () => {
              await send("connections", "DELETE");
              client.invalidateQueries({ queryKey: ["connections"] });
              client.invalidateQueries({ queryKey: ["jobs"] });
            }, "Connections removed.")} />
        )}
      </div>
    </section>
  );
}

/** ntfy phone alerts. The topic is private, so it is saved with your keys, not in your settings file. */
export function PhoneAlerts() {
  const client = useQueryClient();
  const [made, setMade] = useState<string | null>(null);
  const suggest = () => act(async () => {
    const { topic } = await api<{ topic: string }>("alerts/topic");
    await send("keys/NTFY_TOPIC", "PUT", { value: topic });
    setMade(topic);
    client.invalidateQueries({ queryKey: ["keys"] });
    client.invalidateQueries({ queryKey: ["settings"] });
  }, "Topic saved with your keys.");
  return (
    <div className="field wide">
      <div className="field-label">
        <span>Alerts on your phone</span>
        <small>
          Install the free ntfy app on your phone, tap +, and subscribe to a topic name only you know. Anyone who knows
          the name can read the alerts, so use a long random one. It's kept with your keys, never in your settings file.
          Alerts carry only the company, title and link.
        </small>
      </div>
      {made && <p className="topic-made">Subscribe to this topic in the ntfy app: <code>{made}</code></p>}
      <div className="form-actions">
        <button className="button small" onClick={suggest}>Make me a private topic</button>
        <button className="button small" onClick={() => act(() => send("alerts/test", "POST"), "Test alert sent. Check your phone.")}>
          Send a test alert
        </button>
      </div>
    </div>
  );
}
