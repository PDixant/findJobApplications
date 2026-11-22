import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { send, upload, type Company, type ImportPreview } from "../api";
import { toast } from "../live";
import { n } from "../format";

const BOARDS = ["auto", "greenhouse", "ashby", "lever", "workday", "smartrecruiters", "workable", "website", "other"];
const BOARD_HELP: Record<string, string> = {
  auto: "Detect the job board on the next run",
  website: "Read the company's own careers page",
  other: "Its own careers site; try reading the careers page",
};

type Form = Pick<Company, "board" | "slug" | "careers_url" | "domain" | "region" | "priority" | "notes"> & { enabled: boolean };

/** The edit form in a company's details panel. */
export function CompanyForm({ c, locked }: { c: Company; locked: boolean }) {
  const client = useQueryClient();
  const initial = (): Form => ({
    board: c.board || "auto", slug: c.slug, careers_url: c.careers_url, domain: c.domain,
    region: c.region, priority: c.priority, notes: c.notes, enabled: c.enabled,
  });
  const [form, setForm] = useState<Form>(initial);
  const [saving, setSaving] = useState(false);
  useEffect(() => setForm(initial()), [c.company]); // eslint-disable-line react-hooks/exhaustive-deps
  const start = initial();
  const changed = (Object.keys(form) as (keyof Form)[]).filter((k) => form[k] !== start[k]);
  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }));

  async function save() {
    setSaving(true);
    try {
      const body = Object.fromEntries(changed.map((k) => [k, form[k]]));
      const r = await send<{ queued: boolean; detail?: string }>(
        `companies?name=${encodeURIComponent(c.company)}`, "PATCH", body);
      toast(r.queued ? r.detail ?? "Saved. Applied when the run finishes." : `Saved ${c.company}.`, "good");
      await client.invalidateQueries({ queryKey: ["companies"] });
    } catch (e) {
      toast(e instanceof Error ? e.message : "Not saved.", "bad");
    } finally {
      setSaving(false);
    }
  }

  if (c.from_config) {
    return <p className="hint">This company is listed in config.toml (extra_companies), so edit it there.</p>;
  }
  const needsSlug = !["auto", "website", "other"].includes(form.board);
  return (
    <form className="company-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <h3>Edit</h3>
      <label className="check">
        <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        Watch this company
      </label>
      <label>
        <span>Board</span>
        <select value={form.board} onChange={(e) => set({ board: e.target.value })}>
          {BOARDS.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
        {BOARD_HELP[form.board] && <small>{BOARD_HELP[form.board]}</small>}
      </label>
      {needsSlug && (
        <label>
          <span>Board name (slug)</span>
          <input value={form.slug} onChange={(e) => set({ slug: e.target.value })} placeholder="as in boards.greenhouse.io/slug" />
        </label>
      )}
      <label>
        <span>Careers page</span>
        <input value={form.careers_url} onChange={(e) => set({ careers_url: e.target.value })} placeholder="https://" />
      </label>
      <div className="form-row">
        <label>
          <span>Region</span>
          <input value={form.region} onChange={(e) => set({ region: e.target.value })} />
        </label>
        <label className="narrow">
          <span>Priority</span>
          <input value={form.priority} inputMode="numeric" onChange={(e) => set({ priority: e.target.value })} />
        </label>
      </div>
      <label>
        <span>Email domain</span>
        <input value={form.domain} onChange={(e) => set({ domain: e.target.value })} placeholder="company.com" />
      </label>
      <label>
        <span>Notes</span>
        <textarea rows={2} value={form.notes} onChange={(e) => set({ notes: e.target.value })} />
      </label>
      <div className="form-actions">
        <button className="button primary" type="submit" disabled={!changed.length || saving}>Save changes</button>
        {changed.length > 0 && <button className="text-button" type="button" onClick={() => setForm(initial())}>Undo</button>}
      </div>
      {locked && changed.length > 0 && <p className="hint">A run is going, so this is applied when it saves.</p>}
      {(changed.includes("board") || changed.includes("slug") || changed.includes("careers_url")) && (
        <p className="hint">A new board or careers page is checked fresh on the next run.</p>
      )}
    </form>
  );
}

/** Add a company from a link, or import a spreadsheet with a preview first. */
export function AddCompanies({ disabled, onClose }: { disabled: boolean; onClose: () => void }) {
  const client = useQueryClient();
  const [link, setLink] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function add() {
    setBusy(true);
    try {
      const r = await send<{ message: string }>("companies/add", "POST", { link, name });
      toast(r.message, "good");
      setLink("");
      setName("");
      await client.invalidateQueries({ queryKey: ["companies"] });
    } catch (e) {
      toast(e instanceof Error ? e.message : "Not added.", "bad");
    } finally {
      setBusy(false);
    }
  }

  async function choose(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try {
      setPreview(await upload<ImportPreview>(`companies/import?filename=${encodeURIComponent(file.name)}`, file));
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not read that file.", "bad");
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function confirm() {
    if (!preview) return;
    setBusy(true);
    try {
      const r = await send<{ added: number; updated: number }>(`companies/import/${preview.id}`, "POST");
      toast(`Imported: ${n(r.added)} new, ${n(r.updated)} filled in.`, "good");
      setPreview(null);
      await client.invalidateQueries({ queryKey: ["companies"] });
    } catch (e) {
      toast(e instanceof Error ? e.message : "Not imported.", "bad");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel add-panel" aria-label="Add companies">
      <div className="panel-head">
        <h2>Add companies</h2>
        <button className="text-button" onClick={onClose}>Close</button>
      </div>
      {disabled && <p className="hint">A run is going. Adding and importing work again when it finishes.</p>}
      <div className="add-row">
        <form className="add-link" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <label>
            <span>A job posting, job board or careers page link</span>
            <input value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://jobs.lever.co/company/..." />
          </label>
          <label className="narrow-wide">
            <span>Name (optional)</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button className="button primary" type="submit" disabled={!link.trim() || busy || disabled}>Add company</button>
        </form>
        <div className="add-import">
          <span>Or a spreadsheet with a Company column</span>
          <input ref={fileInput} type="file" accept=".xlsx,.csv" hidden onChange={(e) => choose(e.target.files?.[0])} />
          <button className="button" onClick={() => fileInput.current?.click()} disabled={busy || disabled}>
            Choose .xlsx or .csv
          </button>
        </div>
      </div>
      {preview && (
        <div className="import-preview">
          <p>
            <strong>{preview.filename}</strong>: {n(preview.rows)} rows read. {n(preview.added.length)} new
            {preview.added.length === 1 ? " company" : " companies"}, {n(preview.updated.length)} you already watch
            would get missing details filled in. Nothing is saved until you import.
          </p>
          {preview.added.length > 0 && (
            <ul className="plain-list preview-list">
              {preview.added.slice(0, 60).map((r) => (
                <li key={r.company}>
                  <strong>{r.company}</strong> <span className="quiet">{r.region}{r.board !== "auto" ? `, ${r.board}` : ""}</span>
                </li>
              ))}
              {preview.added.length > 60 && <li className="quiet">and {n(preview.added.length - 60)} more</li>}
            </ul>
          )}
          <div className="form-actions">
            <button className="button primary" onClick={confirm}
              disabled={busy || disabled || !(preview.added.length || preview.updated.length)}>
              Import {n(preview.added.length + preview.updated.length)}
            </button>
            <button className="text-button" onClick={() => setPreview(null)}>Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}
