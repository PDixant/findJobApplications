import { useEffect, useRef, type ReactNode } from "react";
import { hex } from "../format";

export function Chip({ color, children, title }: { color?: string | null; children: ReactNode; title?: string }) {
  return (
    <span className="chip" style={color ? { background: hex(color) } : undefined} title={title}>
      {children}
    </span>
  );
}

export function Meter({ used, limit, label, note }: { used: number; limit: number; label: string; note?: string }) {
  const share = limit > 0 ? Math.min(1, used / limit) : 0;
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <span className="num">
          {used.toLocaleString()} of {limit.toLocaleString()}
        </span>
      </div>
      <div className="meter-track" role="meter" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used}>
        <div className={`meter-fill ${share > 0.85 ? "high" : ""}`} style={{ width: `${share * 100}%` }} />
      </div>
      {note && <p className="meter-note">{note}</p>}
    </div>
  );
}

export function Drawer({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
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
    <aside className="drawer" ref={panel} tabIndex={-1} aria-label="Details">
      <header className="drawer-head">
        <h2>{title}</h2>
        <button className="icon-button" onClick={onClose} aria-label="Close details">
          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
            <path d="M3.5 3.5l9 9m0-9l-9 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </header>
      <div className="drawer-body">{children}</div>
    </aside>
  );
}

export function External({ href, children }: { href: string; children: ReactNode }) {
  // pywebview opens target=_blank links in your normal browser.
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <p>{children}</p>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <p className="error-note">{error instanceof Error ? error.message : String(error)}</p>;
}
