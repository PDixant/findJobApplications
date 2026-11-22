import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, send, type KeysData, type RegionValue, type SettingsData } from "../api";
import { Chips, KeyRow, Regions } from "../screens/Settings";
import { useRunActions } from "./RunControls";
import { n } from "../format";
import { toast } from "../live";

type Draft = {
  titles: string[]; skip_titles: string[]; must_mention: string[]; region: RegionValue[]; times: string[];
  startup: boolean;
};

const STEPS = ["Welcome", "Jobs", "Places", "Skills", "Keys", "Schedule"];

/** First run: the few choices that make Job Watcher yours, then the first run. */
export function Welcome({ companies, onDone }: { companies: number; onDone: () => void }) {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("settings") });
  const keys = useQuery({ queryKey: ["keys"], queryFn: () => api<KeysData>("keys") });
  const { start } = useRunActions();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);

  if (settings.data && !draft) {
    const v = (section: string, key: string) =>
      settings.data!.sections.find((s) => s.id === section)?.fields.find((f) => f.key === key)?.value;
    setDraft({
      titles: v("jobs", "titles") as string[], skip_titles: v("jobs", "skip_titles") as string[],
      must_mention: v("jobs", "must_mention") as string[], region: v("places", "region") as RegionValue[],
      times: v("schedule", "times") as string[], startup: true,
    });
  }
  if (!draft) return null;
  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });

  async function finish() {
    setSaving(true);
    try {
      await send("settings", "PUT", { changes: {
        jobs: { titles: draft!.titles, skip_titles: draft!.skip_titles, must_mention: draft!.must_mention },
        places: { region: draft!.region }, schedule: { times: draft!.times, enabled: true } } });
      try {
        await send("startup", "POST", { on: draft!.startup });
      } catch {
        /* not available on this computer */
      }
      await send("setup/done", "POST");
      client.invalidateQueries();
      onDone();
      await start("full");
      toast("Your first run has started. It reads every company once, so it takes a while.", "good");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not save.", "bad");
    } finally {
      setSaving(false);
    }
  }

  const page = [
    <>
      <h2>Find fresh jobs before everyone else</h2>
      <p>Job Watcher reads the job boards of {n(companies)} companies straight from the source, several times a day,
        and keeps the postings that fit you. A few choices make it yours; you can change any of them later in Settings.</p>
    </>,
    <>
      <h2>Which jobs?</h2>
      <label className="wizard-field"><span>A job title must contain one of these</span>
        <Chips value={draft.titles} onChange={(titles) => set({ titles })} label="job titles" /></label>
      <label className="wizard-field"><span>Skip titles with</span>
        <Chips value={draft.skip_titles} onChange={(skip_titles) => set({ skip_titles })} label="skip titles" /></label>
    </>,
    <>
      <h2>Where?</h2>
      <p className="hint">A job must be in one of these regions. Each region lists the places that count.</p>
      <Regions value={draft.region} onChange={(region) => set({ region })} />
    </>,
    <>
      <h2>Your skills</h2>
      <label className="wizard-field"><span>The description must mention one of these. Leave it empty to accept any.</span>
        <Chips value={draft.must_mention} onChange={(must_mention) => set({ must_mention })} label="skills" /></label>
    </>,
    <>
      <h2>Keys (optional)</h2>
      <p className="hint">Each adds something; skip any of them. Serper finds companies you don't watch yet (2,500 free
        searches), Hunter finds recruiters (50 free a month), and Anthropic writes recruiter emails.</p>
      {keys.data?.keys.filter((k) => ["SERPER_API_KEY", "HUNTER_API_KEY", "ANTHROPIC_API_KEY"].includes(k.name))
        .map((k) => <KeyRow key={k.name} k={k} />)}
    </>,
    <>
      <h2>When should it check?</h2>
      <label className="wizard-field"><span>Run at these times each day (24-hour)</span>
        <Chips value={draft.times} onChange={(times) => set({ times })} label="run times" /></label>
      <label className="check"><input type="checkbox" checked={draft.startup} onChange={(e) => set({ startup: e.target.checked })} />
        Start Job Watcher when I sign in, so it checks even when I haven't opened it</label>
    </>,
  ];

  return (
    <div className="wizard-backdrop" role="dialog" aria-modal="true" aria-label="Set up Job Watcher">
      <div className="wizard">
        <ol className="wizard-steps" aria-label="Steps">
          {STEPS.map((s, i) => <li key={s} className={i === step ? "is-on" : i < step ? "is-done" : ""}>{s}</li>)}
        </ol>
        <div className="wizard-body">{page[step]}</div>
        <div className="wizard-actions">
          {step > 0 && <button className="text-button" onClick={() => setStep(step - 1)}>Back</button>}
          <button className="text-button skip" onClick={async () => { await send("setup/done", "POST"); onDone(); }}>
            Skip setup
          </button>
          {step < STEPS.length - 1 ? (
            <button className="button primary" onClick={() => setStep(step + 1)}>{step === 0 ? "Get started" : "Next"}</button>
          ) : (
            <button className="button primary" onClick={finish} disabled={saving || !draft.titles.length || !draft.region.length}>
              Save and run the first check
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
