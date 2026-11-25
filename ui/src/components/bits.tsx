import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";

// ------------------------------------------------------------------ icons
// Kanso icons: 24 px grid, 1.5 px stroke, butt caps, mitred joins. They support labels, never replace them
// (except close, search and the link arrow).
const PATHS: Record<string, ReactNode> = {
  close: <path d="M6 6l12 12M18 6 6 18" />,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5 20 20" /></>,
  down: <path d="M6 9l6 6 6-6" />,
  up: <path d="M6 15l6-6 6 6" />,
  right: <path d="M9 6l6 6-6 6" />,
  external: <path d="M7 17 17 7M9 7h8v8" />,
  check: <path d="M5 12.5 9.5 17 19 7.5" />,
  dash: <path d="M6 12h12" />,
  alert: <><path d="M12 4 21 19.5H3Z" /><path d="M12 10v4.5M12 16.5V18" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5.5M12 7.5V9" /></>,
  people: <><circle cx="9" cy="8.5" r="3.5" /><path d="M3 19.5c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" /><path d="M16 5.5a3 3 0 0 1 0 6M18 14.5c1.8.6 3 2.4 3 5" /></>,
  filter: <path d="M4 6h16M7 12h10M10 18h4" />,
};

export function Icon({ name, size = 16, label }: { name: keyof typeof PATHS | string; size?: number; label?: string }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.5} strokeLinecap="butt" strokeLinejoin="miter" role={label ? "img" : undefined}
      aria-label={label} aria-hidden={label ? undefined : true}>
      {PATHS[name]}
    </svg>
  );
}

// ------------------------------------------------------------------ light

/** Four jewel dots. Still, they are the brand mark; lit, they are the loading light. */
export function Jewels({ lit = false }: { lit?: boolean }) {
  return <span className={`jewels ${lit ? "is-lit" : ""}`} aria-hidden="true"><i /><i /><i /><i /></span>;
}

/** Pathfinder's mark: two ink L-brackets around one jewel of paint, the job you were looking for,
 *  found. The same drawing as jobwatcher/assets/logo.svg, drawn in the theme's own paper and ink so it
 *  sits quietly in dark mode too. Lit, the jewel glows while a run goes. */
export function Logo({ size = 28, lit = false }: { size?: number; lit?: boolean }) {
  return (
    <svg className={`logo ${lit ? "is-lit" : ""}`} width={size} height={size} viewBox="0 0 256 256" aria-hidden="true">
      <rect className="logo-tile" x="0.5" y="0.5" width="255" height="255" rx="56" fill="#FDFBF2" stroke="#E1DFD7" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      <path className="logo-ink" d="M65 134V65H134M122 191H191V122" fill="none" stroke="#111314" strokeWidth="18" strokeLinecap="butt" strokeLinejoin="miter" />
      <circle className="logo-jewel" cx="128" cy="128" r="24" fill="#1C4286" />
    </svg>
  );
}

/** A 2 px line of light: how far a piece of work has got, or a sweep when that isn't known. */
export function LineProgress({ share, label }: { share: number | null; label: string }) {
  const pct = share == null ? null : Math.max(0, Math.min(100, share * 100));
  return (
    <div className={`line-progress ${pct == null ? "is-sweep" : ""}`} role="progressbar" aria-label={label}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct == null ? undefined : Math.round(pct)}>
      <span style={pct == null ? undefined : { width: `${Math.max(1, pct)}%` }} />
    </div>
  );
}

/** The Mesh: small L-shaped strokes that grow heavier and closer toward the right edge, after the
 *  LC 500 grille. Brand surfaces only (empty states, the welcome band), never behind reading text. */
export function Mesh() {
  const bands = useMemo(() => {
    const W = 480, H = 160, CELL = 8;
    const out = ["", "", ""];
    for (let x = 0; x < W; x += CELL) {
      const t = x / W;
      const band = t < 0.4 ? 0 : t < 0.72 ? 1 : 2;
      const arm = [3, 3.5, 4.5][band];
      for (let y = 0; y < H; y += CELL) {
        const hash = (((x * 73856093) ^ (y * 19349663)) >>> 0) % 1000 / 1000;
        if (hash < (1 + 2 * t * t) / 3) out[band] += `M${x + 2} ${y + 2}v${arm}h${arm}`;
      }
    }
    return out;
  }, []);
  return (
    <svg className="mesh" viewBox="0 0 480 160" preserveAspectRatio="xMaxYMid slice" aria-hidden="true">
      {bands.map((d, i) => <path key={i} d={d} fill="none" stroke="currentColor" strokeWidth={[0.75, 1, 1.5][i]} strokeLinecap="butt" strokeLinejoin="miter" />)}
    </svg>
  );
}

// ------------------------------------------------------------------ controls

/** Text tabs with the active line beneath; the line slides to the tab you pick. */
export function Tabs<T extends string>({ items, active, onPick, label }: {
  items: { id: T; label: string; count?: number | string }[]; active: T; onPick: (id: T) => void; label: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [x, setX] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = refs.current[active];
    if (el) setX(el.offsetLeft);
  }, [active, items.length]);
  return (
    <nav className="tabs" aria-label={label}>
      {items.map((t) => (
        <button key={t.id} ref={(el) => { refs.current[t.id] = el; }} className={t.id === active ? "tab is-active" : "tab"}
          aria-current={t.id === active ? "page" : undefined} onClick={() => onPick(t.id)}>
          {t.label}{t.count != null && t.count !== "" && <span className="num">{t.count}</span>}
        </button>
      ))}
      {x != null && <span className="tab-light" style={{ transform: `translateX(${x}px)` }} aria-hidden="true" />}
    </nav>
  );
}

/** A filter shown as a pill ("Fit: Apply") that opens the normal menu. */
export function PillSelect({ label, value, options, onChange, labels, any = "Any" }: {
  label: string; value: string; options: string[]; onChange: (v: string) => void; labels?: Record<string, string>; any?: string;
}) {
  return (
    <span className={`pill-select ${value ? "is-set" : ""}`}>
      {label}: <b>{value ? labels?.[value] ?? value : any}</b>
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{any}</option>
        {options.map((o) => <option key={o} value={o}>{labels?.[o] ?? o}</option>)}
      </select>
    </span>
  );
}

/** Close a popover when you click elsewhere or press Escape. */
export function useDismiss(ref: RefObject<HTMLElement | null>, open: boolean, close: () => void) {
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) close(); };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [ref, open, close]);
}

/** Which side a popover should open from, so it stays inside the window. */
export function usePopSide(ref: RefObject<HTMLElement | null>, open: boolean, width = 300): "start" | "end" {
  const [side, setSide] = useState<"start" | "end">("start");
  useLayoutEffect(() => {
    if (!open || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    setSide(r.left + width > window.innerWidth - 16 ? "end" : "start");
  }, [open, ref, width]);
  return side;
}

/** Search box with its magnifier. */
export function SearchInput({ value, onChange, placeholder, label }: {
  value: string; onChange: (v: string) => void; placeholder: string; label: string;
}) {
  return (
    <span className="search-input">
      <Icon name="search" />
      <input type="search" value={value} placeholder={placeholder} aria-label={label} onChange={(e) => onChange(e.target.value)} />
    </span>
  );
}

/** For things that can't be undone: the first click asks, in plain words, what will happen. */
export function Confirm({ label, question, yes, onYes, disabled }: {
  label: string; question: string; yes: string; onYes: () => void; disabled?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" className="text-button danger" disabled={disabled} onClick={() => setAsking(true)}>{label}</button>;
  return (
    <div className="confirm" role="group" aria-label={question}>
      <p>{question}</p>
      <button type="button" className="button danger small" onClick={() => { setAsking(false); onYes(); }}>{yes}</button>
      <button type="button" className="text-button" onClick={() => setAsking(false)}>Keep it</button>
    </div>
  );
}

// ------------------------------------------------------------------ surfaces

export function Meter({ used, limit, label, note }: { used: number; limit: number; label: string; note?: string }) {
  const share = limit > 0 ? Math.min(1, used / limit) : 0;
  const high = share > 0.85;
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <span className="num">{used.toLocaleString()} of {limit.toLocaleString()}</span>
      </div>
      <div className="meter-track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used}>
        <div className={`meter-fill ${high ? "high" : ""}`} style={{ width: `${share * 100}%` }} />
      </div>
      {(note || high) && <p className="meter-note">{note ?? "Close to this month's limit."}</p>}
    </div>
  );
}

/** Details that float in from the right. Escape closes it. */
export function Drawer({ open, onClose, title, sub, foot, children }: {
  open: boolean; onClose: () => void; title: ReactNode; sub?: ReactNode; foot?: ReactNode; children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    panel.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <aside className="sheet" ref={panel} tabIndex={-1} aria-label="Details">
      <header className="sheet-head">
        <div>
          <h2>{title}</h2>
          {sub && <p className="sheet-sub">{sub}</p>}
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Close details"><Icon name="close" size={20} /></button>
      </header>
      <div className="sheet-body">{children}</div>
      {foot && <footer className="sheet-foot">{foot}</footer>}
    </aside>
  );
}

export function External({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  // pywebview opens target=_blank links in your normal browser.
  return <a href={href} target="_blank" rel="noreferrer" className={className}>{children}</a>;
}

/** An empty screen is an invitation: one serif line, one sentence, one button, on the Mesh. */
export function Empty({ title, children, action, small }: { title: string; children?: ReactNode; action?: ReactNode; small?: boolean }) {
  return (
    <div className={`empty ${small ? "small" : ""}`}>
      {!small && <Mesh />}
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <p className="error-note"><Icon name="alert" />{error instanceof Error ? error.message : String(error)}</p>;
}

export function Skeleton({ width = "100%", height = 20, style }: { width?: number | string; height?: number; style?: React.CSSProperties }) {
  return <span className="skeleton" style={{ display: "block", width, height, ...style }} aria-hidden="true" />;
}

/** A small card that follows the pointer, for charts. */
export function useTip() {
  const [tip, setTip] = useState<{ x: number; y: number; body: ReactNode } | null>(null);
  const show = (e: { clientX: number; clientY: number }, body: ReactNode) => setTip({ x: e.clientX, y: e.clientY, body });
  const at = (el: HTMLElement, body: ReactNode) => {
    const r = el.getBoundingClientRect();
    setTip({ x: r.left + r.width / 2, y: r.top, body });
  };
  const node = tip ? (
    <div className="tooltip" role="tooltip"
      style={{ left: Math.min(tip.x + 12, window.innerWidth - 340), top: Math.max(8, tip.y - 12), transform: "translateY(-100%)" }}>
      {tip.body}
    </div>
  ) : null;
  return { show, at, hide: () => setTip(null), node };
}
