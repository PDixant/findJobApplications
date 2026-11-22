"""Claude helps you choose and apply, with your own Anthropic API key (Settings, Keys).

- fit(): reads your resume and the posting and says APPLY, STRETCH or SKIP, with a 0 to 100
  screening chance, what matches and what is missing. New matches can be scored automatically.
- tailor(): rewrites your base LaTeX resume for one posting, following your resume rules, and
  makes a PDF when LaTeX is installed. Outreach attaches it for that company.

Each call sends the posting, your resume and about_me.md to Anthropic. Nothing else leaves
your computer. A monthly limit keeps the cost predictable.
"""
from __future__ import annotations

import base64
import subprocess

from .base import *  # noqa: F401,F403
from .tracked import connect, get_meta, load_jobs, set_meta, update_job

API = "https://api.anthropic.com/v1/messages"

FIT_RULES = """You screen job postings for one person, using only their resume and about-me notes.

1. List the posting's hard requirements (years in a specific domain, named languages, frameworks or
   engines, degrees, clearances, location or on-site rules, work authorization) and its nice-to-haves.
2. Mark each hard requirement MET, PARTIAL or MISSING, based only on what the resume and notes show.
   Never assume experience that isn't written there.
3. Verdict: APPLY when all or nearly all hard requirements are met; STRETCH when one notable gap can
   be credibly bridged by adjacent experience; SKIP when a domain, language, years or authorization
   requirement can't honestly be met.
4. Score: the chance, 0 to 100, that a recruiter's 10-second scan and an ATS keyword match put this
   person through to a first call.
5. Strengths: the two or three facts from the resume that best match the posting. Gaps: what is
   missing or weak, most important first.
Write plainly. No em dashes."""

DEFAULT_RESUME_RULES = """Tailor the resume to the posting:
- Mark the posting's hard requirements MET, PARTIAL or MISSING from the resume alone.
- Headline: 4 to 6 terms, each backed by a bullet below. No claim without proof.
- Reorder bullets so the most relevant come first within each role.
- Bullet formula: strong verb, what was built, how, and the outcome or scale.
- Only use numbers already in the resume. Where a number would help, write the bullet without it and
  list it under numbers to find.
- Reframe adjacent experience honestly; never add a tool, language, title, date or domain the
  person hasn't used.
- Skills: the posting's tools the person knows first; drop unrelated ones to save space.
- One page. No summary section. No em dashes. Keep the LaTeX template, commands and packages as they are."""


class AIProblem(Exception):
    """Something the person can fix; the message says how."""


def _key():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise AIProblem("Add your Anthropic API key in Settings, Keys and accounts.")
    return key


def tool_call(payload, timeout=180):
    """Send one request and return the forced tool's input."""
    try:
        r = requests.post(API, json=payload, timeout=timeout, headers={
            "x-api-key": _key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    except requests.RequestException as exc:
        raise AIProblem(f"Could not reach Claude ({exc.__class__.__name__}). Check your connection.") from None
    if r.status_code == 401:
        raise AIProblem("Anthropic rejected the API key. Check it in Settings, Keys and accounts.")
    if r.status_code in (429, 529):
        raise AIProblem("Claude is busy right now. Try again in a minute.")
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        raise AIProblem(f"Claude answered {r.status_code}{': ' + detail if detail else ''}.")
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input") or {}
    raise AIProblem("Claude didn't return an answer. Try again.")


# ----------------------------------------------------------------- usage
def _month():
    return dt.datetime.now().strftime("%Y-%m")


def used_this_month(cfg):
    db = connect(cfg)
    try:
        return int(get_meta(db, f"ai_uses_{_month()}", "0"))
    finally:
        db.close()


def _count(cfg):
    db = connect(cfg)
    try:
        with db:
            set_meta(db, f"ai_uses_{_month()}", str(int(get_meta(db, f"ai_uses_{_month()}", "0")) + 1))
    finally:
        db.close()


def _check_budget(cfg):
    limit = int(cfg["ai"]["monthly_limit"])
    if limit and used_this_month(cfg) >= limit:
        raise AIProblem(f"This month's limit of {limit} Claude requests is used up (Settings, AI help).")


# ----------------------------------------------------------------- inputs
def _resume_block(cfg):
    o = cfg["outreach"]
    path = Path(o["resume"])
    path = path if path.is_absolute() else BASE / path
    if path.exists() and path.suffix.lower() == ".pdf" and path.stat().st_size < 4_500_000:
        return {"type": "document", "title": "Resume",
                "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(path.read_bytes()).decode()}}
    return None


def _about(cfg):
    path = BASE / cfg["outreach"]["about_me"]
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _posting(cfg, key):
    jobs = load_jobs(cfg)
    job = jobs.get(key)
    if not job:
        raise AIProblem("That job is not in your tracker.")
    db = connect(cfg)
    try:
        row = db.execute("SELECT text FROM postings WHERE key=?", (key,)).fetchone()
    finally:
        db.close()
    text = zlib.decompress(row[0]).decode("utf-8") if row and row[0] else ""
    if not text:
        raise AIProblem("The description of this posting wasn't saved, so Claude has nothing to read. Open the posting instead.")
    return job, text[:12000]


def _tidy(text):
    return re.sub(r"\s*\u2014\s*", ", ", str(text or "")).strip()


# ----------------------------------------------------------------- fit
def fit(cfg, key):
    _check_budget(cfg)
    job, text = _posting(cfg, key)
    resume, about = _resume_block(cfg), _about(cfg)
    if not resume and not about:
        raise AIProblem("Claude needs your resume (resume.pdf, set under Recruiter emails) or about_me.md to judge fit.")
    content = [resume] if resume else []
    content.append({"type": "text", "text": (
        f"Posting: {job['title']} at {job['company']} ({', '.join(job.get('locations') or [])})\n\n{text}\n\n"
        + (f"About the person:\n{about}\n\n" if about else "") + "Judge the fit with the record_fit tool.")})
    tool = {"name": "record_fit", "description": "Record the fit judgement.", "input_schema": {
        "type": "object", "required": ["verdict", "score", "summary", "strengths", "gaps", "requirements"],
        "properties": {
            "verdict": {"type": "string", "enum": ["APPLY", "STRETCH", "SKIP"]},
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "summary": {"type": "string", "description": "One sentence."},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "requirements": {"type": "array", "items": {"type": "object", "required": ["text", "status"], "properties": {
                "text": {"type": "string"}, "status": {"type": "string", "enum": ["MET", "PARTIAL", "MISSING"]}}}}}}}
    out = tool_call({"model": cfg["ai"]["model"], "max_tokens": 2000, "system": FIT_RULES, "tools": [tool],
                     "tool_choice": {"type": "tool", "name": "record_fit"},
                     "messages": [{"role": "user", "content": content}]})
    _count(cfg)
    result = {"verdict": out.get("verdict", "STRETCH"), "score": max(0, min(100, int(out.get("score") or 0))),
              "summary": _tidy(out.get("summary")), "strengths": [_tidy(s) for s in out.get("strengths") or []][:4],
              "gaps": [_tidy(s) for s in out.get("gaps") or []][:5],
              "requirements": [{"text": _tidy(r.get("text")), "status": r.get("status", "PARTIAL")}
                               for r in out.get("requirements") or []][:12],
              "at": dt.datetime.now(dt.timezone.utc).isoformat(), "model": cfg["ai"]["model"]}
    update_job(cfg, key, {"fit": result})
    return result


def score_new(cfg, keys, on_done=None, on_each=None):
    """Score matches that have no fit yet (after a run). Stops quietly at the monthly limit."""
    if not cfg["ai"]["score_new_matches"] or not os.environ.get("ANTHROPIC_API_KEY"):
        return 0
    jobs = load_jobs(cfg)
    done = 0
    for key in keys:
        if key not in jobs or jobs[key].get("fit"):
            continue
        try:
            fit(cfg, key)
            done += 1
            if on_each:
                on_each(key)
        except AIProblem as exc:
            log.info("Fit scores stopped: %s", exc)
            break
        except Exception:
            log.exception("Fit score failed for %s", key)
    if done:
        log.info("Claude scored %d new match%s.", done, "" if done == 1 else "es")
        if on_done:
            on_done(done)
    return done


# ----------------------------------------------------------------- tailored resume
def _file_name(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "Resume"


def compiler():
    for name in ("tectonic", "pdflatex", "xelatex", "lualatex"):
        found = shutil.which(name)
        if found:
            return name, found
    return None, None


def tailor(cfg, key):
    _check_budget(cfg)
    job, text = _posting(cfg, key)
    a = cfg["ai"]
    base = Path(a["resume_tex"])
    base = base if base.is_absolute() else BASE / base
    if not base.exists():
        raise AIProblem(f"Put your base LaTeX resume in the Job Watcher folder as {base.name} "
                        "(or set its name under Settings, AI help) to make tailored resumes.")
    rules_path = BASE / a["resume_rules"]
    rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else DEFAULT_RESUME_RULES
    about = _about(cfg)
    person = (re.search(r"^\s*(?:Name|Full name)\s*:\s*(.+)$", about, re.M | re.I) or [None, ""])[1].strip() or "Resume"
    tool = {"name": "save_resume", "description": "Save the tailored resume.", "input_schema": {
        "type": "object", "required": ["tex", "verdict", "numbers_to_find"], "properties": {
            "tex": {"type": "string", "description": "The complete .tex file."},
            "verdict": {"type": "string", "enum": ["APPLY", "STRETCH", "SKIP"]},
            "numbers_to_find": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string", "description": "Two sentences at most on what changed and why."}}}}
    out = tool_call({
        "model": a["model"], "max_tokens": 16000, "tools": [tool], "tool_choice": {"type": "tool", "name": "save_resume"},
        "system": "You edit a LaTeX resume for one job posting. Follow these rules exactly.\n\n" + rules,
        "messages": [{"role": "user", "content": [{"type": "text", "text": (
            f"Posting: {job['title']} at {job['company']}\n\n{text}\n\n"
            + (f"About the person (facts you may use):\n{about}\n\n" if about else "")
            + f"Base resume (.tex):\n{base.read_text(encoding='utf-8')}\n\nReturn the complete edited .tex with save_resume.")}]}]},
        timeout=300)
    _count(cfg)
    tex = str(out.get("tex") or "")
    if "\\begin{document}" not in tex:
        raise AIProblem("Claude didn't return a complete LaTeX file. Try again.")
    folder = BASE / (cfg["outreach"]["tailored_resumes_folder"] or "resumes")
    folder.mkdir(parents=True, exist_ok=True)
    stem = _file_name(f"{person}_{job['company']}_{job['title']}")
    tex_path = folder / f"{stem}.tex"
    tex = re.sub(r"(?<=\w)\s*---\s*(?=\w)", ", ", tex.replace("\u2014", ", "))  # no em dashes, in either form
    tex_path.write_text(tex, encoding="utf-8")
    pdf_path, made_with, problem = None, None, None
    name, exe = compiler()
    if exe:
        try:
            cmd = [exe, tex_path.name] if name == "tectonic" else [exe, "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
            subprocess.run(cmd, cwd=folder, capture_output=True, timeout=180, check=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if (folder / f"{stem}.pdf").exists():
                pdf_path, made_with = folder / f"{stem}.pdf", name
                for leftover in (".aux", ".log", ".out"):
                    (folder / f"{stem}{leftover}").unlink(missing_ok=True)
            else:
                problem = f"{name} could not build the PDF; open the .tex to see why."
        except (OSError, subprocess.TimeoutExpired) as exc:
            problem = f"{name} could not build the PDF ({exc.__class__.__name__})."
    result = {"tex": tex_path.relative_to(BASE).as_posix(),
              "pdf": pdf_path.relative_to(BASE).as_posix() if pdf_path else None, "compiler": made_with,
              "problem": problem or (None if pdf_path else "Install LaTeX (MiKTeX or Tectonic) to get a PDF automatically."),
              "verdict": out.get("verdict"), "numbers_to_find": [_tidy(n) for n in out.get("numbers_to_find") or []],
              "notes": _tidy(out.get("notes")), "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    update_job(cfg, key, {"tailored": result})
    return result


__all__ = ["AIProblem", "fit", "score_new", "tailor", "tool_call", "used_this_month", "compiler", "FIT_RULES",
           "DEFAULT_RESUME_RULES"]
