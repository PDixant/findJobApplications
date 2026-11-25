import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, send, type AiStatus } from "../api";
import { External } from "./bits";

/** Before the first request to Claude: exactly what goes to Anthropic, and an OK that's asked once.
 *  `ensure()` resolves true when it's fine to send (already agreed, or agreed now). */
export function useClaudeConsent() {
  const client = useQueryClient();
  const [pending, setPending] = useState<null | ((ok: boolean) => void)>(null);

  async function ensure(): Promise<boolean> {
    try {
      const status = await client.fetchQuery({ queryKey: ["ai"], queryFn: () => api<AiStatus>("ai"), staleTime: 5_000 });
      if (status.consented) return true;
    } catch {
      // can't tell: ask, and the server checks again anyway
    }
    return new Promise<boolean>((resolve) => setPending(() => resolve));
  }

  async function answer(ok: boolean) {
    if (ok) {
      try {
        await send("ai/consent", "POST");
        await client.invalidateQueries({ queryKey: ["ai"] });
      } catch {
        ok = false;
      }
    }
    pending?.(ok);
    setPending(null);
  }

  const dialog = pending ? createPortal(<ConsentDialog onAnswer={answer} />, document.body) : null;
  return { ensure, dialog };
}

function ConsentDialog({ onAnswer }: { onAnswer: (ok: boolean) => void }) {
  const yes = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    yes.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onAnswer(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onAnswer]);
  return (
    <div className="scrim" onClick={(e) => e.target === e.currentTarget && onAnswer(false)}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="consent-title">
        <h2 id="consent-title">Before anything goes to Claude</h2>
        <p>
          Pathfinder sends these to Anthropic, the company that makes Claude, with your own API key, and only when
          you ask (or after a run, if you turned on scoring new matches):
        </p>
        <ul className="consent-list">
          <li>The job posting: its title, company and description</li>
          <li>Your <code>about_me.md</code></li>
          <li>For fit scores and email drafts, <code>resume.pdf</code>. For tailored resumes, <code>resume.tex</code> and your resume rules</li>
          <li>For email drafts, each person's name, email and role</li>
        </ul>
        <p>
          Nothing else on your computer is sent. Anthropic handles it under{" "}
          <External href="https://www.anthropic.com/legal/privacy">its own terms</External>. This is asked once; removing
          your Anthropic key stops it.
        </p>
        <div className="card-foot" style={{ marginTop: 28 }}>
          <button ref={yes} className="button primary" onClick={() => onAnswer(true)}>Send to Claude</button>
          <button className="text-button" onClick={() => onAnswer(false)}>Not now</button>
        </div>
      </div>
    </div>
  );
}
