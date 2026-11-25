import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, send, type KeysData, type RegionValue, type SettingsData } from "../api";
import { Chips, KeyRow, Regions } from "../screens/Settings";
import { useRunActions } from "./RunControls";
import { External, Logo, Mesh } from "./bits";
import { APP_NAME, AUTHOR, REPO_URL } from "../brand";
import { n } from "../format";
import { toast } from "../live";

type Draft = {
  titles: string[]; skip_titles: string[]; must_mention: string[]; region: RegionValue[]; times: string[];
  startup: boolean;
};

const STEPS = ["Welcome", "Jobs", "Places", "Skills", "Keys", "Schedule"];

/** First run: the few choices that make Pathfinder yours, then the first run. */
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
      toast(e instanceof Error ? e.message : "Your choices weren't saved. Try again.", "bad");
    } finally {
      setSaving(false);
    }
  }

  const page = [
    <>
      <h2>Find fresh jobs before everyone else</h2>
      <p>Pathfinder reads the job boards of {n(companies)} companies straight from the source, several times a day,
        and keeps the postings that fit you. A few choices make it yours. You can change any of them later in Settings.</p>
    </>,
    <>
      <h2 className="small">Which jobs?</h2>
      <p>A job title has to contain one of your titles, and none of the words you skip.</p>
      <div className="field-stack">Job titles
        <Chips value={draft.titles} onChange={(titles) => set({ titles })} label="job titles" /></div>
      <div className="field-stack">Skip titles with
        <Chips value={draft.skip_titles} onChange={(skip_titles) => set({ skip_titles })} label="skip titles" /></div>
    </>,
    <>
      <h2 className="small">Where?</h2>
      <p>A job has to be in one of these regions. Each region lists the places that count.</p>
      <div className="field-stack"><Regions value={draft.region} onChange={(region) => set({ region })} /></div>
    </>,
    <>
      <h2 className="small">Your skills</h2>
      <p>The description has to mention one of these. Leave it empty to accept any stack.</p>
      <div className="field-stack">Skills
        <Chips value={draft.must_mention} onChange={(must_mention) => set({ must_mention })} label="skills" /></div>
    </>,
    <>
      <h2 className="small">Keys, if you have them</h2>
      <p>Each one adds something, and you can skip any of them. Serper finds companies you don't watch yet (2,500 free
        searches), Hunter finds recruiters (50 free a month), and Anthropic writes recruiter emails.</p>
      <div>
        {keys.data?.keys.filter((k) => ["SERPER_API_KEY", "HUNTER_API_KEY", "ANTHROPIC_API_KEY"].includes(k.name))
          .map((k) => <KeyRow key={k.name} k={k} />)}
      </div>
    </>,
    <>
      <h2 className="small">When should it check?</h2>
      <p>Pathfinder runs at these times each day while it's open. Use 24-hour times, such as 08:00 or 17:30.</p>
      <div className="field-stack">Run at
        <Chips value={draft.times} onChange={(times) => set({ times })} label="run times" /></div>
      <label className="switch">
        <input type="checkbox" checked={draft.startup} onChange={(e) => set({ startup: e.target.checked })} />
        Start Pathfinder when I sign in, so it checks even when I haven't opened it
      </label>
    </>,
  ];

  return (
    <div className="welcome" role="dialog" aria-modal="true" aria-label="Set up Pathfinder">
      <div className="welcome-band"><Mesh /></div>
      <div className="welcome-inner">
        <p className="welcome-brand">
          <Logo size={40} />
          <span className="brand-text">
            <span className="brand-name">{APP_NAME}</span>
            <External href={REPO_URL} className="brand-by">by {AUTHOR}</External>
          </span>
        </p>
        <ol className="welcome-steps" aria-label="Steps" style={{ listStyle: "none", padding: 0 }}>
          {STEPS.map((s, i) => (
            <li key={s} className={`welcome-step ${i === step ? "is-on" : i < step ? "is-done" : ""}`} aria-current={i === step ? "step" : undefined}>{s}</li>
          ))}
        </ol>
        <div className="welcome-body enter" key={step}>{page[step]}</div>
        <div className="welcome-actions">
          <button className="text-button skip" onClick={async () => { await send("setup/done", "POST"); onDone(); }}>Skip setup</button>
          {step > 0 && <button className="button" onClick={() => setStep(step - 1)}>Back</button>}
          {step < STEPS.length - 1 ? (
            <button className="button primary" onClick={() => setStep(step + 1)}>{step === 0 ? "Get started" : "Next"}</button>
          ) : (
            <button className="button paint" onClick={finish} disabled={saving || !draft.titles.length || !draft.region.length}>
              Save and run the first check
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
