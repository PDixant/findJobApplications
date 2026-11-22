"""The Outreach screen: contacts, drafts written with Claude, .eml and Gmail drafts, follow-ups.

It reuses outreach.py (Hunter, contact ranking, the writing rules, building messages), so the
app and `python outreach.py` behave the same. Drafts are kept in postings.db. Nothing is ever
sent: you send each email from your own mail app.
"""
from __future__ import annotations

import base64
import imaplib
import subprocess

from .base import *  # noqa: F401,F403
from .tracked import connect, load_jobs, edit_job, update_job

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
EXECUTIVE = re.compile(r"\b(ceo|cto|cfo|coo|cio|cpo|cmo|chief|co-?founder|founder|president|chair(man|woman)?|"
                       r"board|vp|vice president|svp|evp|general manager|managing director)\b|talent intelligence", re.I)
WAITING = ("Email", "Prepared", "No contact")


class OutreachProblem(Exception):
    """Something the person can fix; the message says how."""


def _out():
    import outreach  # the top-level outreach.py
    return outreach


# ----------------------------------------------------------------- reading
def _rows(cfg):
    from .companies import load_company_rows
    return {r["company"]: r for r in load_company_rows(cfg)}


def _text_of(cfg, key):
    db = connect(cfg)
    try:
        row = db.execute("SELECT text FROM postings WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        db.close()
    return zlib.decompress(row[0]).decode("utf-8") if row and row[0] else ""


def drafts(cfg, company=None):
    db = connect(cfg)
    try:
        sql, args = "SELECT * FROM drafts", ()
        if company:
            sql, args = sql + " WHERE company=?", (company,)
        return [{**dict(r), "job_keys": json.loads(r["job_keys"] or "[]")} for r in db.execute(sql + " ORDER BY id", args)]
    finally:
        db.close()


def _cache():
    return load_json(_out().CACHE_PATH, {})


def _cache_key(row, jd_text):
    domain = _out().domain_for(row, jd_text)
    return (domain or "company:" + row["company"]).lower(), domain


def people(emails):
    """Everyone Hunter found, with what kind of contact they are and whether they're an executive."""
    out = []
    for e in emails:
        address = (e.get("value") or "").strip()
        status = (e.get("verification") or {}).get("status")
        if not address or status == "invalid":
            continue
        position = e.get("position") or e.get("position_raw") or ""
        name = " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x)
        o = _out()
        kind = ("recruiter" if o.RECRUITER.search(position) else "engineering leader" if o.LEADER.search(position)
                else "founder" if o.FOUNDER.search(position)
                else "team inbox" if e.get("type") == "generic" or not name else "other")
        out.append({"name": name or address.split("@")[0], "position": position, "email": address, "kind": kind,
                    "executive": bool(EXECUTIVE.search(position)), "confidence": e.get("confidence") or 0,
                    "verification": status or "not checked", "linkedin": e.get("linkedin") or ""})
    return out


def suggested(emails, wanted):
    """The default picks: the usual ranking, leaving executives out unless nobody else is found."""
    o = _out()
    regular = [e for e in emails if not EXECUTIVE.search(e.get("position") or e.get("position_raw") or "")]
    picks = o.rank_contacts(regular, wanted) or o.rank_contacts(emails, wanted)
    return [p["email"] for p in picks]


def overview(cfg):
    """Every company with outreach to do, grouped by stage, newest work first."""
    now = dt.datetime.now(dt.timezone.utc)
    jobs = load_jobs(cfg)
    rows = _rows(cfg)
    cache = _cache()
    days = int(cfg["outreach"]["follow_up_after_days"])
    wanted = int(cfg["outreach"]["contacts_per_company"])
    by_company = {}
    for d in drafts(cfg):
        by_company.setdefault(d["company"], {"drafts": [], "jobs": {}})["drafts"].append(d)
    for j in jobs.values():
        o = j.get("outreach") or ""
        if o in WAITING or o in ("Sent", "Follow-up", "Replied") or j["company"] in by_company:
            if o:
                by_company.setdefault(j["company"], {"drafts": [], "jobs": {}})["jobs"][j["key"]] = j
    out = []
    for company, item in by_company.items():
        js = list(item["jobs"].values())
        if not js and not item["drafts"]:
            continue
        sent = [j for j in js if j.get("outreach") == "Sent"]
        due = [j for j in sent if not j.get("outreach_followup") and parse_date(j.get("outreach_sent"))
               and now - parse_date(j["outreach_sent"]) >= dt.timedelta(days=days)]
        open_drafts = [d for d in item["drafts"] if d["status"] in ("draft", "opened")]
        if any(j.get("outreach") == "Replied" for j in js) and not open_drafts:
            stage = "replied"
        elif open_drafts:
            stage = "drafts"
        elif due:
            stage = "follow_up"
        elif any(j.get("outreach") in WAITING for j in js):
            stage = "to_write"
        else:
            stage = "sent"
        row = rows.get(company, {"company": company})
        jd = _text_of(cfg, js[0]["key"]) if js else ""
        key, domain = _cache_key(row, jd)
        hit = cache.get(key)
        contacts = None
        if hit and now - parse_date(hit["fetched"]) < dt.timedelta(days=_out().CACHE_DAYS):
            contacts = {"people": people(hit.get("emails") or []), "suggested": suggested(hit.get("emails") or [], wanted),
                        "domain": hit.get("domain") or domain, "pattern": hit.get("pattern"), "fetched": hit["fetched"],
                        "error": hit.get("error")}
        out.append({
            "company": company, "stage": stage, "domain": domain, "contacts": contacts,
            "linkedin": _out().linkedin_links(company),
            "jobs": sorted(({k: j.get(k) for k in ("key", "title", "url", "status", "outreach", "posted", "region",
                                                   "outreach_sent", "applied")} for j in js),
                           key=lambda j: j.get("posted") or "", reverse=True),
            "drafts": item["drafts"], "follow_up_due": [j["key"] for j in due],
            "updated": max([d["updated"] or "" for d in item["drafts"]] + [j.get("outreach_sent") or "" for j in js] + [""])})
    order = {"follow_up": 0, "to_write": 1, "drafts": 2, "sent": 3, "replied": 4}
    out.sort(key=lambda c: (order[c["stage"]], c["company"].lower()))
    return out


# ----------------------------------------------------------------- contacts
def find_contacts(cfg, company):
    """Ask Hunter for people at the company (one credit, then cached for 60 days)."""
    o = _out()
    key_value = os.environ.get("HUNTER_API_KEY", "").strip()
    if not key_value:
        raise OutreachProblem("Add your Hunter key in Settings, Keys and accounts, to find contacts.")
    now = dt.datetime.now(dt.timezone.utc)
    jobs = [j for j in load_jobs(cfg).values() if j["company"] == company]
    row = _rows(cfg).get(company, {"company": company})
    jd = _text_of(cfg, jobs[0]["key"]) if jobs else ""
    cache = _cache()
    key, domain = _cache_key(row, jd)
    state = load_json(STATE_PATH, {})
    hunter = o.Hunter(key_value, state, float(cfg["outreach"]["monthly_hunter_credit_limit"]), now)
    try:
        body = hunter.domain_search(domain=domain, company=None if domain else company,
                                    departments=cfg["outreach"]["departments"])
        if not body.get("emails") and cfg["outreach"]["departments"] and not body.get("error"):
            body = hunter.domain_search(domain=domain, company=None if domain else company)
    except o.OutreachError as exc:
        raise OutreachProblem(f"Hunter: {exc}") from None
    cache[key] = {"fetched": now.isoformat(), "domain": body.get("domain") or domain,
                  "organization": body.get("organization"), "pattern": body.get("pattern"),
                  "emails": body.get("emails") or [], "error": body.get("error")}
    o.OUT.mkdir(parents=True, exist_ok=True)
    save_json(o.CACHE_PATH, cache)
    _save_hunter_usage(hunter)
    if not cache[key]["emails"]:
        for j in jobs:
            if j.get("outreach") == "Email":
                update_job(cfg, j["key"], {"outreach": "No contact"})
    return cache[key]


def _save_hunter_usage(hunter):
    """Hunter credits are counted in job_watcher_state.json, like outreach.py does."""
    state = load_json(STATE_PATH, {})
    state.setdefault("hunter", {})["credits_by_month"] = hunter.used
    save_json(STATE_PATH, {k: v for k, v in state.items() if k != "jobs"})


# ----------------------------------------------------------------- writing with Claude
def _rules(follow_up, name):
    o = _out()
    text = o.FOLLOW_UP_INSTRUCTIONS if follow_up else o.INSTRUCTIONS
    start = text.index("## Rules")
    section = text[start:text.index("\n---", start)]
    return section.replace("{name}", name)


def write_drafts(cfg, company, emails, follow_up=False):
    """Draft one email per chosen contact with Claude, using your about-me file, your resume and
    the job descriptions, under the same rules your Claude agent follows. Saves them as drafts."""
    o = _out()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise OutreachProblem("Add your Anthropic API key in Settings, Keys and accounts, to write drafts in the app.")
    try:
        about = o.load_about_me(cfg)
    except o.OutreachError as exc:
        raise OutreachProblem(str(exc)) from None
    name = o.name_from_about(about)
    now = dt.datetime.now(dt.timezone.utc)
    all_jobs = [j for j in load_jobs(cfg).values() if j["company"] == company]
    if follow_up:
        jobs = [j for j in all_jobs if j.get("outreach") == "Sent"]
    else:
        jobs = [j for j in all_jobs if j.get("outreach") in WAITING] or all_jobs
    if not jobs:
        raise OutreachProblem(f"No jobs at {company} are marked for outreach.")
    previous = {d["to_email"].lower(): d for d in drafts(cfg, company) if d["status"] in ("sent", "replied")}

    contacts = []
    if follow_up:
        for address in emails:
            d = previous.get(address.lower())
            contacts.append({"email": address, "name": d["to_name"] if d else "", "position": d["position"] if d else "",
                             "kind": d["kind"] if d else ""})
    else:
        row = _rows(cfg).get(company, {"company": company})
        hit = _cache().get(_cache_key(row, _text_of(cfg, jobs[0]["key"]))[0]) or {}
        known = {p["email"].lower(): p for p in people(hit.get("emails") or [])}
        for address in emails:
            contacts.append(known.get(address.lower()) or {"email": address, "name": "", "position": "", "kind": "other"})
        _verify(cfg, contacts, now)
    if not contacts:
        raise OutreachProblem("Pick at least one person to write to.")

    resume, _tailored = o.resume_for(cfg, company)
    job_text = []
    for j in jobs:
        desc = _text_of(cfg, j["key"])[:6000] or "(description not saved; write from the title)"
        job_text.append(f"### {j['title']}\nJob ID: {o.job_id(j)}\nLink: {j.get('url', '')}\n"
                        f"Tracker status: {j.get('status') or 'New'}\nPosted: {(j.get('posted') or '')[:10]}\n\n{desc}")
    lines = [f"Write {'a follow-up' if follow_up else 'an email'} to each of these people at {company}:"]
    for c in contacts:
        lines.append(f"- {c.get('name') or 'unknown name'} <{c['email']}>, {c.get('position') or 'role unknown'}"
                     f" ({c.get('kind') or 'contact'})")
        if follow_up and c["email"].lower() in previous:
            p = previous[c["email"].lower()]
            lines.append(f"  Original email, subject \"{p['subject']}\":\n  {p['body']}")
    lines += ["", "## The role(s)", *job_text, "", f"## About {name}", about,
              "", "Return every email with the save_drafts tool. Also write a LinkedIn connection note "
              "(under 300 characters) for each person."]
    content = []
    if resume.exists() and resume.suffix.lower() == ".pdf" and resume.stat().st_size < 4_500_000:
        content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                       "data": base64.b64encode(resume.read_bytes()).decode()},
                        "title": f"{name}'s resume"})
    content.append({"type": "text", "text": "\n".join(lines)})
    system = (f"You write short, factual recruiting emails for {name}, who reviews and sends every one "
              f"themselves. Follow these rules exactly.\n\n{_rules(follow_up, name)}")
    tool = {"name": "save_drafts", "description": "Save the drafted emails.",
            "input_schema": {"type": "object", "required": ["emails"], "properties": {"emails": {
                "type": "array", "items": {"type": "object", "required": ["to", "subject", "body", "linkedin_note"],
                                           "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
                                                          "body": {"type": "string"},
                                                          "linkedin_note": {"type": "string"}}}}}}}
    payload = {"model": cfg["outreach"].get("model") or "claude-sonnet-5-5", "max_tokens": 4000, "system": system,
               "tools": [tool], "tool_choice": {"type": "tool", "name": "save_drafts"},
               "messages": [{"role": "user", "content": content}]}
    written = _call_claude(api_key, payload)

    by_email = {c["email"].lower(): c for c in contacts}
    attach = "" if follow_up or not resume.exists() else (
        resume.relative_to(BASE).as_posix() if BASE in resume.parents else str(resume))
    stamp = now.isoformat()
    made = []
    db = connect(cfg)
    try:
        with db:
            for e in written:
                c = by_email.get(str(e.get("to", "")).lower().strip())
                if not c:
                    continue
                subject = _tidy(e.get("subject", ""))
                if follow_up and not subject.lower().startswith("re:") and c["email"].lower() in previous:
                    subject = "Re: " + previous[c["email"].lower()]["subject"]
                cur = db.execute(
                    "INSERT INTO drafts (company, job_keys, to_email, to_name, position, kind, subject, body,"
                    " linkedin_note, attach, follow_up, status, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (company, json.dumps([j["key"] for j in jobs]), c["email"], c.get("name", ""), c.get("position", ""),
                     c.get("kind", ""), subject, _tidy(e.get("body", "")), _tidy(e.get("linkedin_note", "")), attach,
                     1 if follow_up else 0, "draft", stamp, stamp))
                made.append(cur.lastrowid)
    finally:
        db.close()
    if not made:
        raise OutreachProblem("Claude didn't return drafts for the people you picked. Try again.")
    if not follow_up:
        for j in jobs:
            if j.get("outreach") in ("Email", "No contact"):
                update_job(cfg, j["key"], {"outreach": "Prepared", "outreach_prepared": stamp})
    return [d for d in drafts(cfg, company) if d["id"] in made]


def _verify(cfg, contacts, now):
    if not cfg["outreach"]["verify_emails"]:
        return
    todo = [c for c in contacts if c.get("verification") not in ("valid", "invalid") and c.get("kind") != "team inbox"]
    key_value = os.environ.get("HUNTER_API_KEY", "").strip()
    if not todo or not key_value:
        return
    o = _out()
    state = load_json(STATE_PATH, {})
    hunter = o.Hunter(key_value, state, float(cfg["outreach"]["monthly_hunter_credit_limit"]), now)
    for c in todo:
        try:
            status = hunter.verify(c["email"])
        except o.OutreachError:
            break
        if status:
            c["verification"] = status
    _save_hunter_usage(hunter)
    bad = [c["email"] for c in contacts if c.get("verification") == "invalid"]
    if bad:
        raise OutreachProblem(f"Hunter says these addresses don't work: {', '.join(bad)}. Pick someone else.")


def _call_claude(api_key, payload):
    try:
        r = requests.post(ANTHROPIC_URL, json=payload, timeout=120, headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    except requests.RequestException as exc:
        raise OutreachProblem(f"Could not reach Claude ({exc.__class__.__name__}). Check your connection.") from None
    if r.status_code == 401:
        raise OutreachProblem("Anthropic rejected the API key. Check it in Settings, Keys and accounts.")
    if r.status_code in (429, 529):
        raise OutreachProblem("Claude is busy right now. Try again in a minute.")
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        raise OutreachProblem(f"Claude answered {r.status_code}{': ' + detail if detail else ''}.")
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "save_drafts":
            return block.get("input", {}).get("emails") or []
    return []


def _tidy(text):
    """The rules ask for no em dashes; take out any that slip through."""
    return re.sub(r"\s*—\s*", ", ", str(text or "")).strip()


# ----------------------------------------------------------------- after writing
def get_draft(cfg, draft_id):
    found = [d for d in drafts(cfg) if d["id"] == draft_id]
    if not found:
        raise KeyError(draft_id)
    return found[0]


def edit_draft(cfg, draft_id, fields):
    get_draft(cfg, draft_id)
    allowed = {k: str(v) for k, v in fields.items() if k in ("subject", "body", "linkedin_note", "to_email", "to_name")}
    if not allowed:
        raise OutreachProblem("Send subject, body or linkedin_note.")
    db = connect(cfg)
    try:
        with db:
            sets = ", ".join(f"{k}=?" for k in allowed)
            db.execute(f"UPDATE drafts SET {sets}, updated=? WHERE id=?",
                       (*allowed.values(), dt.datetime.now(dt.timezone.utc).isoformat(), draft_id))
    finally:
        db.close()
    return get_draft(cfg, draft_id)


def delete_draft(cfg, draft_id):
    get_draft(cfg, draft_id)
    db = connect(cfg)
    try:
        with db:
            db.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
    finally:
        db.close()


def _set_status(cfg, draft_id, status, sent=False):
    db = connect(cfg)
    try:
        with db:
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            db.execute("UPDATE drafts SET status=?, updated=?" + (", sent=?" if sent else "") + " WHERE id=?",
                       (status, now, now, draft_id) if sent else (status, now, draft_id))
    finally:
        db.close()


def _message_fields(d):
    return {"to": d["to_email"], "to_name": d["to_name"], "subject": d["subject"], "body": d["body"],
            "attach": [d["attach"]] if d.get("attach") else []}


def write_eml(cfg, draft_id):
    """An .eml file that opens as an unsent draft in Outlook, Windows Mail or Thunderbird."""
    o = _out()
    d = get_draft(cfg, draft_id)
    folder = o.OUT / "drafts" / o.slugify(d["company"], 40)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{d['id']:04d}_{o.slugify(d['to_name'] or d['to_email'].split('@')[0], 30)}.eml"
    try:
        path.write_bytes(o.build_message(_message_fields(d), "", unsent=True).as_bytes())
    except o.OutreachError as exc:
        raise OutreachProblem(str(exc)) from None
    if d["status"] == "draft":
        _set_status(cfg, draft_id, "opened")
    return path


def open_in_mail_app(cfg, draft_id):
    path = write_eml(cfg, draft_id)
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606  (opens with your default mail app)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        raise OutreachProblem(f"Saved {path.name}, but no mail app opened it ({exc}). Open it from {path.parent}.") from None
    return path


def gmail_draft(cfg, draft_id):
    """Put the email in your Gmail Drafts folder, resume attached (needs the Gmail app password)."""
    o = _out()
    d = get_draft(cfg, draft_id)
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not (address and password):
        raise OutreachProblem("Add your Gmail address and app password in Settings, Keys and accounts.")
    server = cfg["outreach"]["imap_server"]
    try:
        imap = imaplib.IMAP4_SSL(server, timeout=30)
        imap.login(address, password)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise OutreachProblem(f"Could not sign in to {server} ({exc}). Check the app password.") from None
    try:
        mailbox = o.drafts_mailbox(imap, cfg["outreach"]["drafts_folder"])
        msg = o.build_message(_message_fields(d), address)
        status, _ = imap.append(f'"{mailbox}"', "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if status != "OK":
            raise OutreachProblem("Gmail refused the draft.")
    except o.OutreachError as exc:
        raise OutreachProblem(str(exc)) from None
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: S110
            pass
    if d["status"] == "draft":
        _set_status(cfg, draft_id, "opened")
    return mailbox


def mark_sent(cfg, draft_id):
    """You sent it: the jobs move to Sent (starting the follow-up clock), or Follow-up for a follow-up."""
    d = get_draft(cfg, draft_id)
    _set_status(cfg, draft_id, "sent", sent=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for key in d["job_keys"]:
        try:
            if d["follow_up"]:
                update_job(cfg, key, {"outreach": "Follow-up", "outreach_followup": now})
            else:
                edit_job(cfg, key, {"outreach": "Sent"})
        except KeyError:
            pass
    return get_draft(cfg, draft_id)


def mark_replied(cfg, company):
    for d in drafts(cfg, company):
        if d["status"] == "sent":
            _set_status(cfg, d["id"], "replied")
    for j in load_jobs(cfg).values():
        if j["company"] == company and j.get("outreach") in ("Sent", "Follow-up", "Prepared"):
            edit_job(cfg, j["key"], {"outreach": "Replied"})


# ----------------------------------------------------------------- without an API key: packets
def packets(cfg):
    o = _out()
    if not o.OUT.exists():
        return []
    return [{"folder": p.relative_to(BASE).as_posix(), "has_emails": (p / "emails.json").exists(),
             "name": p.name} for p in sorted(o.OUT.iterdir(), reverse=True) if p.is_dir() and (p / "INSTRUCTIONS.md").exists()]


def import_packet(cfg, folder):
    """Load the emails.json your Claude agent wrote into the app as drafts."""
    o = _out()
    try:
        folder, emails = o.load_emails(BASE / folder)
    except o.OutreachError as exc:
        raise OutreachProblem(str(exc)) from None
    jobs = load_jobs(cfg).values()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    db = connect(cfg)
    try:
        with db:
            for e in emails:
                company = e.get("company") or ""
                keys = [j["key"] for j in jobs if j["company"] == company and j.get("outreach") in (*WAITING, "Sent")]
                attach = e.get("attach") or ""
                db.execute(
                    "INSERT INTO drafts (company, job_keys, to_email, to_name, subject, body, linkedin_note, attach,"
                    " follow_up, status, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (company, json.dumps(keys), e["to"], e.get("to_name", ""), e["subject"], e["body"],
                     e.get("linkedin_note", ""), attach if isinstance(attach, str) else (attach[0] if attach else ""),
                     1 if str(e["subject"]).lower().startswith("re:") else 0, "draft", stamp, stamp))
    finally:
        db.close()
    return len(emails)


__all__ = [n for n in list(globals()) if not n.startswith("__")]
