#!/usr/bin/env python3
"""
Recruiter outreach prep for Job Watcher.

Mark jobs in job_tracker.xlsx by setting the Outreach column to "Email", then run:

  python outreach.py                  find recruiters and engineering leaders at those companies
                                      (Hunter.io) and write an outreach packet: one file per
                                      company with the roles, job descriptions and contacts, plus
                                      INSTRUCTIONS.md for your Claude agent to write the emails
                                      and create them as drafts in your mailbox
  python outreach.py --follow-ups     packet of polite follow-ups for emails you marked Sent
                                      more than follow_up_after_days ago with no reply
  python outreach.py --make-drafts outreach/<packet folder>
                                      put the emails your agent saved in emails.json into your
                                      Gmail Drafts folder, with your resume attached
  python outreach.py --export-drafts outreach/<packet folder>
                                      write each email in emails.json as a .md file (to read or
                                      copy into Gmail) and an .eml file (opens as a ready-to-send
                                      draft in Outlook or Thunderbird, resume attached)
  python outreach.py --credits        show how many Hunter credits are left this month

Nothing is ever sent. Every email ends up as a draft that you read, edit and send yourself.
Keys go in .env: HUNTER_API_KEY, and for --make-drafts GMAIL_ADDRESS and GMAIL_APP_PASSWORD.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import imaplib
import json
import logging
import mimetypes
import os
import re
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import requests

import job_watcher as jw

BASE = jw.BASE
OUT = BASE / "outreach"
CACHE_PATH = OUT / "contacts_cache.json"
HUNTER = "https://api.hunter.io/v2"
CACHE_DAYS = 60
log = logging.getLogger("job_watcher")

TECH_RECRUITER = re.compile(r"technical recruit|tech(nology)? recruit|engineering recruit|"
                            r"talent acquisition|recruiting (lead|manager|partner)", re.I)
RECRUITER = re.compile(r"recruit|talent|sourc(er|ing)|people partner|hiring|human resources|\bhr\b", re.I)
LEADER = re.compile(r"engineering manager|manager,? (software )?engineering|head of (engineering|product engineering)|"
                    r"director,? (of )?(software )?engineering|engineering director|vp,? (of )?engineering|"
                    r"vice president.{0,25}engineering|\bcto\b|chief technology|engineering lead|"
                    r"tech(nical)? lead|front[- ]?end lead|team lead", re.I)
FOUNDER = re.compile(r"founder|\bceo\b|chief executive", re.I)
TEAM_INBOXES = ("careers", "jobs", "recruiting", "recruitment", "talent", "hiring", "hr", "people", "join")
PLATFORM_HOSTS = ("myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
                  "smartrecruiters.com", "icims.com", "taleo.net", "successfactors", "eightfold.ai",
                  "phenompeople", "jobvite.com", "bamboohr.com", "breezy.hr", "recruitee.com",
                  "teamtailor.com", "applytojob.com", "oraclecloud.com", "ultipro", "adp.com",
                  "paylocity", "dayforcehcm", "rippling", "linkedin.com", "indeed.com")
FREE_MAIL = ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "proton.me")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class OutreachError(Exception):
    """Something the user needs to fix; the message says what."""


# ----------------------------------------------------------------- Hunter.io
class Hunter:
    """Small Hunter.io client that counts credits against your monthly limit."""

    def __init__(self, key, state, limit, now):
        self.key, self.limit = key, limit
        self.month = now.astimezone().strftime("%Y-%m")
        self.used = state.setdefault("hunter", {}).setdefault("credits_by_month", {})

    def left(self):
        return self.limit - self.used.get(self.month, 0)

    def _spend(self, credits):
        self.used[self.month] = round(self.used.get(self.month, 0) + credits, 1)

    def _get(self, path, params, polls=3):
        for attempt in range(polls):
            try:
                r = requests.get(f"{HUNTER}/{path}", params={**params, "api_key": self.key}, timeout=40)
            except requests.RequestException as exc:
                raise OutreachError(f"could not reach Hunter ({exc.__class__.__name__})") from None
            if r.status_code == 202 and attempt < polls - 1:  # verification still running
                time.sleep(5)
                continue
            try:
                data = r.json()
            except ValueError:
                raise OutreachError(f"Hunter answered HTTP {r.status_code} without JSON") from None
            if r.status_code == 401:
                raise OutreachError("Hunter rejected the key; check HUNTER_API_KEY in .env")
            if r.status_code in (402, 403, 429):
                detail = "; ".join(e.get("details", "") for e in data.get("errors", []))
                raise OutreachError(f"Hunter refused the request: {detail or r.status_code} "
                                    "(usually the plan's monthly credits are used up)")
            if r.status_code >= 400:
                detail = "; ".join(e.get("details", "") for e in data.get("errors", []))
                return {"error": detail or f"HTTP {r.status_code}"}
            return data
        return {}

    def domain_search(self, domain=None, company=None, departments=None):
        """People with addresses at a company. Calls that find nobody are free."""
        if self.left() < 1:
            raise OutreachError(f"this month's Hunter limit ({self.limit} credits) is used up")
        params = {"limit": 10}
        params.update({"domain": domain} if domain else {"company": company})
        if departments:
            params["department"] = ",".join(departments)
        data = self._get("domain-search", params)
        if data.get("error"):
            return {"emails": [], "error": data["error"]}
        body = data.get("data") or {}
        if body.get("emails"):
            self._spend(1)
        return body

    def verify(self, address):
        if self.left() < 0.5:
            return None
        data = self._get("email-verifier", {"email": address})
        self._spend(0.5)
        return (data.get("data") or {}).get("status")

    def account(self):
        return self._get("account", {})


# ----------------------------------------------------------------- picking people
def domain_for(row, jd_text=""):
    """The company's email domain: the domain column, else the careers page's domain, else an
    address in the job description. None means 'ask Hunter by company name'."""
    if (row.get("domain") or "").strip():
        return row["domain"].strip().lower().removeprefix("www.")
    url = (row.get("careers_url") or "").strip()
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower() if url else ""
    if host and not re.fullmatch(r"[\d.:]+", host) and not any(p in host for p in PLATFORM_HOSTS):
        parts = host.split(".")
        if parts[-1] not in ("jobs", "careers", "work"):
            if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "ac", "edu") and len(parts[-1]) == 2:
                return ".".join(parts[-3:])
            return ".".join(parts[-2:])
    for address in EMAIL_RE.findall(jd_text or ""):
        d = address.split("@")[1].lower()
        if d not in FREE_MAIL and not any(p in d for p in PLATFORM_HOSTS):
            return d
    return None


def rank_contacts(emails, wanted):
    """Best people to write to: a recruiter first, then an engineering leader (or a founder at a
    small company), then whoever else scores well. A team inbox such as careers@ only when no
    person is found. Addresses Hunter knows are invalid are dropped."""
    people, inboxes = [], []
    for e in emails:
        address = (e.get("value") or "").strip()
        status = (e.get("verification") or {}).get("status")
        if not address or status == "invalid":
            continue
        position = e.get("position") or e.get("position_raw") or ""
        entry = {"name": " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x),
                 "position": position, "email": address, "confidence": e.get("confidence"),
                 "verification": status or "not checked", "linkedin": e.get("linkedin") or "",
                 "sources": [s.get("uri") for s in (e.get("sources") or [])[:2] if s.get("uri")]}
        if e.get("type") == "generic" or not entry["name"]:
            if address.split("@")[0].lower().split(".")[0] in TEAM_INBOXES:
                inboxes.append({**entry, "kind": "team inbox", "score": 10})
            continue
        if TECH_RECRUITER.search(position):
            kind, score = "recruiter", 100
        elif RECRUITER.search(position):
            kind, score = "recruiter", 85
        elif LEADER.search(position):
            kind, score = "engineering leader", 80
        elif FOUNDER.search(position):
            kind, score = "founder", 60
        else:
            continue
        score += (e.get("confidence") or 0) / 10 + (5 if status == "valid" else 0)
        people.append({**entry, "kind": kind, "score": round(score, 1)})
    people.sort(key=lambda p: -p["score"])
    chosen = []
    for kind in ("recruiter", "engineering leader"):
        best = next((p for p in people if p["kind"] == kind), None)
        if best is None and kind == "engineering leader":
            best = next((p for p in people if p["kind"] == "founder"), None)
        if best and len(chosen) < wanted:
            chosen.append(best)
    for p in people:
        if len(chosen) >= wanted:
            break
        if p not in chosen:
            chosen.append(p)
    if not chosen and inboxes:
        chosen = inboxes[:1]
    return chosen


def find_contacts(hunter, row, jd_text, cfg, cache, now):
    """Contacts for one company, from the cache when fresh (so each company costs one credit
    at most every 60 days)."""
    o = cfg["outreach"]
    domain = domain_for(row, jd_text)
    key = (domain or "company:" + row["company"]).lower()
    hit = cache.get(key)
    if hit and now - jw.parse_date(hit["fetched"]) < dt.timedelta(days=CACHE_DAYS):
        body = hit
    else:
        body = hunter.domain_search(domain=domain, company=None if domain else row["company"],
                                    departments=o["departments"])
        if not body.get("emails") and o["departments"] and not body.get("error"):
            body = hunter.domain_search(domain=domain, company=None if domain else row["company"])
        body = {"fetched": now.isoformat(), "domain": body.get("domain") or domain,
                "organization": body.get("organization"), "pattern": body.get("pattern"),
                "emails": body.get("emails") or [], "error": body.get("error")}
        cache[key] = body
    contacts = rank_contacts(body["emails"], int(o["contacts_per_company"]))
    if o["verify_emails"]:
        for c in contacts:
            if c["verification"] not in ("valid", "invalid") and c["kind"] != "team inbox":
                status = hunter.verify(c["email"])
                if status:
                    c["verification"] = status
        contacts = [c for c in contacts if c["verification"] != "invalid"]
    in_posting = sorted({a for a in EMAIL_RE.findall(jd_text or "")
                         if a.split("@")[1].lower() not in FREE_MAIL})
    return body.get("domain") or domain, body.get("pattern"), contacts, in_posting, body.get("error")


# ----------------------------------------------------------------- packet writing
def load_about_me(cfg):
    path = BASE / cfg["outreach"]["about_me"]
    if not path.exists():
        example = BASE / "about_me.example.md"
        raise OutreachError(f"{path.name} is missing. Copy {example.name} to {path.name} and fill it in "
                            "(it tells the email writer who you are).")
    return path.read_text(encoding="utf-8-sig").strip()


def resume_for(cfg, company):
    """A tailored resume whose file name contains the company name, else your main resume."""
    o = cfg["outreach"]
    folder = (BASE / o["tailored_resumes_folder"]) if o["tailored_resumes_folder"] else None
    if folder and folder.is_dir():
        name = jw.norm_name(company)
        for pdf in sorted(folder.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True):
            if name and name in jw.norm_name(pdf.stem):
                return pdf, True
    main = Path(o["resume"])
    main = main if main.is_absolute() else BASE / main
    return main, False


def job_id(j):
    parts = j["key"].split(":")
    if parts[0] == "website":
        return j.get("url", "")
    return parts[-1]


def linkedin_links(company):
    q = quote_plus
    return [f"https://www.linkedin.com/search/results/people/?keywords={q(company + ' recruiter')}",
            f"https://www.linkedin.com/search/results/people/?keywords={q(company + ' engineering manager')}"]


def slugify(text, n=40):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:n] or "company"


INSTRUCTIONS = """# Recruiter outreach: {count} compan{ies}, prepared {when}

You are helping {name} contact recruiters and engineering leaders about jobs they chose.
Create one **draft** email per contact listed in the company files in this folder.
**Never send anything.** {name} reviews, edits and sends every draft themselves.

Company files: {files}

## Steps

1. Read "About {name}" at the bottom of this file. If you can open it, also read the resume
   named in each company file.
2. For each company file, read the role(s) and job description. Pick the two or three
   requirements that matter most, and the facts from "About {name}" or the resume that match
   them best. If a company file says no email addresses were found and you have a Hunter tool,
   run one Domain Search for that company (its domain, or its name) to find a recruiter or an
   engineering manager, and use at most two people with a verification status of valid. Each
   search uses one of {name}'s Hunter credits, so do this only for companies with no contacts.
3. Write one email per contact, following the rules below. If a company lists several roles,
   write one email per contact that leads with the best-fitting role and names the others.
4. Create each email as a draft:
   - **With a Gmail (or other email) tool:** create the draft with To, Subject and Body. If the
     tool can attach files, attach the resume named in the company file. If it cannot, say so in
     your summary so {name} attaches it before sending.
   - **Without an email tool:** save every email to `emails.json` in this folder, as a list of
     objects with `to`, `to_name`, `company`, `subject`, `body`, `attach` (the resume path from
     the company file) and `linkedin_note`. Then tell {name} to run either
     `python outreach.py --make-drafts "{folder}"` (puts them in Gmail Drafts with the resume
     attached) or `python outreach.py --export-drafts "{folder}"` (writes a drafts folder with a
     .md and an .eml file per email).
5. For each contact, also write a LinkedIn connection note under 300 characters.
6. Finish with a short table: company, contact, subject, draft created (yes/no), the LinkedIn
   note, and anything {name} should check (for example an address marked accept_all or unknown,
   which may not be deliverable).

## Rules for the emails

- 90 to 150 words, plain text, no images, no link shorteners, no tracking.
- Subject: the exact role title and job ID, then {name}'s name. For example:
  `Senior Software Engineer, Frontend (4567890) - {name}`.
- First line: the role, the job ID and link, and whether {name} has already applied (the
  company file gives the tracker status for each role).
- Then one or two sentences of evidence: a real achievement that matches the job's top
  requirement. Use a number only if it appears in "About {name}" or the resume.
- **Never invent** experience, metrics, skills, referrals, mutual connections or familiarity with
  the company's product. If you are unsure whether something is true, leave it out.
- For a recruiter: ask whether the profile fits and what the best next step is.
- For an engineering leader or founder: one line on why their team's work fits {name}'s
  background (only from the job description), then ask for a short chat or for the resume to
  reach the hiring manager.
- For a team inbox (careers@ and similar): a shorter note naming the role and job ID.
- No flattery, no "I hope this email finds you well", no em dashes.
- Sign off with the signature from "About {name}".

---

## About {name}

{about}
"""

FOLLOW_UP_INSTRUCTIONS = """# Follow-ups: {count} email{s} with no reply, prepared {when}

{name} emailed these contacts about {days}+ days ago and has not heard back. For each contact in
the company files in this folder, create a short follow-up **draft**. **Never send anything.**

Company files: {files}

## Steps

1. **With a Gmail (or other email) tool:** search {name}'s Sent mail for the original email to that
   address (the original subject usually contains the job ID listed in the company file). Create
   the follow-up as a draft reply in that same thread.
2. **Without an email tool:** save the follow-ups to `emails.json` in this folder (`to`,
   `to_name`, `subject` starting with "Re: " and the original subject, `body`; no attachment).
   Then tell {name} to run: `python outreach.py --make-drafts "{folder}"`
3. Finish with a short table: contact, company, draft created (yes/no).

## Rules

- Two to four sentences. Polite and brief: restate interest in the role and job ID, add one new
  relevant detail if one fits (from "About {name}"), and offer to send anything helpful.
- Never invent facts, never guilt-trip, no em dashes. One follow-up only; do not draft a second.

---

## About {name}

{about}
"""


def company_file(cfg, n, company, row, jobs, store, contacts, domain, pattern, in_posting,
                 error, resume, tailored, contacted, scores, follow_up=False):
    score, tier = scores.get(company, ("", ""))
    lines = [f"# {company}", ""]
    meta = [f"Domain: {domain or 'unknown'}"]
    if score != "":
        meta.append(f"Company score: {score} ({tier})")
    meta.append(f"Board: {jobs[0].get('provider', '')}")
    lines += [" | ".join(meta), ""]
    if not follow_up:
        note = "tailored for this company" if tailored else "main resume"
        exists = "" if resume.exists() else "  **(file not found: fix the resume path in Settings, Recruiter emails)**"
        lines += [f"Resume to attach ({note}): `{resume}`{exists}", ""]
    lines += ["## Role" + ("s" if len(jobs) > 1 else ""), ""]
    for j in jobs:
        posted = jw.to_local_naive(j.get("posted"))
        lines += [f"### {j['title']}", "",
                  f"- Job ID: {job_id(j)}",
                  f"- Link: {j.get('url', '')}",
                  f"- Location: {'; '.join(dict.fromkeys(j.get('locations') or [])) or 'not listed'}",
                  f"- Posted: {posted:%Y-%m-%d}" if posted else "- Posted: not listed",
                  f"- Level: {j.get('level', '')}",
                  f"- Stack match: {', '.join(j.get('stack') or []) or 'none listed'}",
                  f"- Tracker status: {j.get('status', 'New')} (Applied means {cfg['_name']} has applied)"]
        if j.get("notes"):
            lines.append(f"- {cfg['_name']}'s notes: {j['notes']}")
        if follow_up:
            sent = jw.to_local_naive(j.get("outreach_sent"))
            lines.append(f"- Original email marked Sent: {sent:%Y-%m-%d}" if sent else "- Original email date unknown")
        text = store.text_of(j["key"]) if store else None
        if text and not follow_up:
            text = " ".join(text.split())
            lines += ["", "Job description:", "", "> " + text[:6000] + (" ..." if len(text) > 6000 else "")]
        lines.append("")
    lines += ["## Contacts" + (" emailed" if follow_up else " (suggested order)"), ""]
    if contacts:
        for i, c in enumerate(contacts, start=1):
            who = c["name"] or c["email"]
            bits = [f"{i}. **{who}**", c.get("position") or c["kind"], f"`{c['email']}`",
                    f"{c['kind']}", f"confidence {c.get('confidence')}",
                    f"verification: {c['verification']}"]
            lines.append(" | ".join(str(b) for b in bits if b))
            if c.get("linkedin"):
                lines.append(f"   LinkedIn: {c['linkedin']}")
            if contacted.get(c["email"]) and not follow_up:
                lines.append(f"   Already included in a packet on {contacted[c['email']]['date']}; "
                             "only email again if that email was never sent.")
    else:
        lines.append("No email addresses found." + (f" (Hunter: {error})" if error else ""))
    if in_posting and not follow_up:
        lines += ["", f"Addresses written in the job posting: {', '.join(in_posting)}"]
    if pattern and not follow_up:
        lines += ["", f"Hunter's usual address pattern here: `{pattern}` (do not guess addresses from it)"]
    if not follow_up:
        lines += ["", "Find more people (for LinkedIn notes or referrals):"] + [f"- {u}" for u in linkedin_links(company)]
    return "\n".join(lines) + "\n"


def name_from_about(about):
    m = re.search(r"^\s*(?:-\s*)?Name:\s*(.+)$", about, re.M | re.I)
    return m.group(1).strip() if m else "the job seeker"


def tracker_has_outreach_column(path):
    try:
        wb = jw.load_workbook(path, read_only=True)
        header = next(wb["Jobs"].iter_rows(values_only=True))
        wb.close()
        return "Outreach" in header
    except Exception:
        return True  # cannot tell; fall back to the usual message


def load_everything(cfg, now):
    state = jw.load_state(cfg)  # your latest Outreach marks, from the app (or Excel before the app)
    excel = BASE / cfg["excel_file"]
    rows = jw.load_company_rows(cfg)
    return state, rows, excel


def save_everything(cfg, state, rows, excel, csv_changed, since):
    if jw.take_pending_company_edits(cfg, rows):
        csv_changed = True
    if csv_changed:
        jw.save_company_rows(cfg, rows)
    jw.save_state(cfg, state, since)
    scores = {r["company"]: (r.get("score", ""), r.get("tier", "")) for r in rows}
    if not jw.write_excel(excel, state, cfg, scores):
        log.info("The Outreach column will show the new values after you close the tracker "
                 "and the next run updates it.")


def prepare(cfg, follow_ups=False):
    now = dt.datetime.now(dt.timezone.utc)
    about = load_about_me(cfg)
    cfg["_name"] = name_from_about(about)
    state, rows, excel = load_everything(cfg, now)
    by_company = {r["company"]: r for r in rows}
    days = int(cfg["outreach"]["follow_up_after_days"])

    if follow_ups:
        due = [j for j in state["jobs"].values() if j.get("outreach") == "Sent" and not j.get("outreach_followup")
               and jw.parse_date(j.get("outreach_sent")) and now - jw.parse_date(j["outreach_sent"]) >= dt.timedelta(days=days)]
        if not due:
            print(f"No follow-ups due: nothing marked Sent more than {days} days ago without a reply.")
            return None
    else:
        due = [j for j in state["jobs"].values() if (j.get("outreach") or "").lower() == "email"]
        if not due:
            if jw.edits_in(cfg) == "app":
                print('Nothing to prepare. In the app\'s Jobs screen, set Outreach to "Email" for the jobs '
                      "you want, then run this again.")
            elif not tracker_has_outreach_column(excel):
                print("Your tracker does not have the Outreach column yet: it was made by an older version. "
                      "Close job_tracker.xlsx, run  python job_watcher.py --refilter  (makes no requests) "
                      'to rebuild it, then set Outreach to "Email" on the jobs you want, save, close, '
                      "and run this again.")
            else:
                print('Nothing to prepare. In job_tracker.xlsx, set the Outreach column to "Email" '
                      "for the jobs you want, save, close the file, and run this again.")
            return None

    groups = {}
    for j in sorted(due, key=lambda j: (j["company"].lower(), j.get("posted") or "")):
        groups.setdefault(j["company"], []).append(j)

    hunter = None
    if not follow_ups:
        key = os.environ.get("HUNTER_API_KEY", "").strip()
        if not key:
            raise OutreachError(f"HUNTER_API_KEY is not set. Add it to {jw.ENV_PATH} "
                                "(free key at hunter.io).")
        hunter = Hunter(key, state, float(cfg["outreach"]["monthly_hunter_credit_limit"]), now)

    folder = OUT / (now.astimezone().strftime("%Y-%m-%d_%H%M") + ("_follow-ups" if follow_ups else ""))
    folder.mkdir(parents=True, exist_ok=True)
    cache = jw.load_json(CACHE_PATH, {})
    contacted = state.setdefault("outreach_contacts", {})
    scores = {r["company"]: (r.get("score", ""), r.get("tier", "")) for r in rows}
    store = jw.PostingStore(BASE / cfg["postings_db"]) if (BASE / cfg["postings_db"]).exists() else None
    files, packet, csv_changed, stopped = [], [], False, None
    try:
        for n, (company, jobs) in enumerate(groups.items(), start=1):
            row = by_company.get(company, {"company": company})
            if follow_ups:
                contacts = [c for j in jobs for c in j.get("outreach_contacts", [])]
                contacts = list({c["email"]: c for c in contacts}.values())
                domain, pattern, in_posting, error = row.get("domain"), None, [], None
            else:
                jd = " ".join((store.text_of(j["key"]) or "") for j in jobs) if store else ""
                try:
                    domain, pattern, contacts, in_posting, error = find_contacts(
                        hunter, row, jd, cfg, cache, now)
                except OutreachError as exc:
                    stopped = str(exc)
                    break
                if domain and row is by_company.get(company) and not row.get("domain"):
                    row["domain"] = domain
                    csv_changed = True
            resume, tailored = resume_for(cfg, company)
            text = company_file(cfg, n, company, row, jobs, store, contacts, domain, pattern, in_posting,
                                error, resume, tailored, contacted, scores, follow_up=follow_ups)
            name = f"{n:02d}_{slugify(company)}.md"
            (folder / name).write_text(text, encoding="utf-8")
            files.append(name)
            packet.append({"company": company, "domain": domain, "resume": str(resume), "file": name,
                           "contacts": contacts, "in_posting": in_posting,
                           "jobs": [{k: j.get(k) for k in ("key", "title", "url", "status", "posted", "locations")}
                                    | {"job_id": job_id(j)} for j in jobs]})
            stamp = now.isoformat()
            for j in jobs:
                if follow_ups:
                    j["outreach_followup"], j["outreach"] = stamp, "Follow-up"
                else:
                    j["outreach_prepared"] = stamp
                    j["outreach"] = "Prepared" if (contacts or in_posting) else "No contact"
                    j["outreach_contacts"] = contacts
                    j["outreach_packet"] = folder.name
            for c in contacts:
                contacted.setdefault(c["email"], {"company": company, "date": now.astimezone().date().isoformat()})
            log.info("  %s  %s: %s", "FOLLOW" if follow_ups else "READY ", company,
                     ", ".join(f"{c['name'] or c['email']} ({c['kind']})" for c in contacts) or "no contacts found")
    finally:
        if store:
            store.close()
        if not follow_ups:
            jw.save_json(CACHE_PATH, cache)

    if not files:
        folder.rmdir()
        raise OutreachError(stopped or "nothing was prepared")
    template = FOLLOW_UP_INSTRUCTIONS if follow_ups else INSTRUCTIONS
    (folder / "INSTRUCTIONS.md").write_text(template.format(
        count=len(files), ies="y" if len(files) == 1 else "ies", s="" if len(files) == 1 else "s",
        when=now.astimezone().strftime("%Y-%m-%d %H:%M"), name=cfg["_name"], files=", ".join(files),
        folder=folder.relative_to(BASE).as_posix(), about=about, days=days), encoding="utf-8")
    jw.save_json(folder / "packet.json", {"prepared": now.isoformat(), "follow_ups": follow_ups,
                                          "companies": packet})
    save_everything(cfg, state, rows, excel, csv_changed, since=now)
    if hunter:
        log.info("Hunter credits used this month: %s of %s.", hunter.used.get(hunter.month, 0), hunter.limit)
    if stopped:
        log.warning("Stopped early: %s. The rest stay marked Email for next time.", stopped)
    rel = folder.relative_to(BASE).as_posix()
    print(f"\nPacket ready: {folder}\n\nGive your Claude agent this request:\n"
          f'  Read {rel}/INSTRUCTIONS.md in my job watcher folder and follow it to create my draft emails.\n')
    return folder


# ----------------------------------------------------------------- emails.json
def load_emails(folder):
    folder = Path(folder)
    folder = folder if folder.is_absolute() else BASE / folder
    path = folder / "emails.json"
    if not path.exists():
        raise OutreachError(f"{path} does not exist yet. Ask your Claude agent to write the emails first.")
    try:
        emails = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise OutreachError(f"emails.json is not valid JSON: {exc}") from None
    if isinstance(emails, dict):
        emails = emails.get("emails", [])
    problems = [f"email {i}: missing {f}" for i, e in enumerate(emails, start=1)
                for f in ("to", "subject", "body") if not str(e.get(f, "")).strip()]
    if problems:
        raise OutreachError("; ".join(problems))
    return folder, emails


def build_message(e, sender, unsent=False):
    """The email as a MIME message, with its attachments. unsent=True makes an .eml that mail
    apps open as a draft: the X-Unsent header goes first, there is no From (your mail app fills
    in your own account), and an HTML copy of the text is included because the new Outlook for
    Windows drops plain-text-only bodies from such drafts."""
    msg = EmailMessage()
    if unsent:
        msg["X-Unsent"] = "1"
    if sender and not unsent:
        msg["From"] = sender
    msg["To"] = email.utils.formataddr((e.get("to_name") or "", e["to"].strip()))
    msg["Subject"] = e["subject"].strip()
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid()
    msg.set_content(e["body"].strip() + "\n")
    if unsent:
        paragraphs = e["body"].strip().split("\n\n")
        html_body = "".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs)
        msg.add_alternative(f"<html><body style=\"font-family:Calibri,Arial,sans-serif\">{html_body}</body></html>",
                            subtype="html")
    attach = e.get("attach") or []
    for item in ([attach] if isinstance(attach, str) else attach):
        file = Path(item)
        file = file if file.is_absolute() else BASE / file
        if not file.exists():
            raise OutreachError(f"attachment not found for {e['to']}: {file}")
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(file.read_bytes(), maintype=main, subtype=sub, filename=file.name)
    return msg


def export_drafts(cfg, folder):
    """A drafts folder with, per email, a .md file to read or copy from and an .eml file that
    opens as an unsent, ready-to-edit draft in Outlook or Thunderbird."""
    folder, emails = load_emails(folder)
    out = folder / "drafts"
    out.mkdir(exist_ok=True)
    for i, e in enumerate(emails, start=1):
        who = e.get("to_name") or e["to"].split("@")[0]
        company = e.get("company") or e["to"].split("@")[1].split(".")[0].title()
        stem = f"{i:02d}_{slugify(company, 30)}_{slugify(who, 30)}"
        attach = e.get("attach") or []
        attach = [attach] if isinstance(attach, str) else attach
        md = [f"**To:** {who} <{e['to']}>", f"**Subject:** {e['subject']}"]
        if attach:
            md.append(f"**Attach:** {', '.join(attach)}")
        md += ["", "---", "", e["body"].strip(), ""]
        if e.get("linkedin_note"):
            md += ["---", "", "**LinkedIn connection note:**", "", e["linkedin_note"].strip(), ""]
        (out / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")
        msg = build_message(e, "", unsent=True)  # opens as a draft from your own mail account
        (out / f"{stem}.eml").write_bytes(msg.as_bytes())
        print(f"  WROTE  {stem}.md and .eml")
    print(f"\n{len(emails)} draft{'s' if len(emails) != 1 else ''} written to {out}. Nothing was sent.\n"
          "Double-click a .eml file to open it as a draft in Outlook or Thunderbird, sent from your "
          "own account. Check the resume is attached before sending (the new Outlook for Windows can "
          "drop it); the .md file has the same text to copy from.")
    return out


# ----------------------------------------------------------------- Gmail drafts over IMAP
def drafts_mailbox(imap, fallback):
    """The folder the server marks as Drafts (its name depends on the Gmail language)."""
    status, lines = imap.list()
    for line in lines or []:
        text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
        if "\\Drafts" in text:
            m = re.search(r'"([^"]+)"\s*$', text) or re.search(r"(\S+)\s*$", text)
            if m:
                return m.group(1)
    return fallback


def make_drafts(cfg, folder):
    folder, emails = load_emails(folder)
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not address or not password:
        raise OutreachError(f"Add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to {jw.ENV_PATH}. "
                            "An app password comes from myaccount.google.com/apppasswords "
                            "(2-Step Verification must be on).")
    o = cfg["outreach"]
    try:
        imap = imaplib.IMAP4_SSL(o["imap_server"])
        imap.login(address, password)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise OutreachError(f"could not sign in to {o['imap_server']} ({exc}). Check the app password.") from None
    made = []
    try:
        mailbox = drafts_mailbox(imap, o["drafts_folder"])
        for e in emails:
            msg = build_message(e, address)
            status, _ = imap.append(f'"{mailbox}"', "\\Draft",
                                    imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            if status != "OK":
                raise OutreachError(f"the server refused the draft to {e['to']}")
            made.append(e["to"])
            print(f"  DRAFT  {e['to']}: {e['subject']}")
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    print(f"\n{len(made)} draft{'s' if len(made) != 1 else ''} saved to {mailbox}. Nothing was sent.")
    return made


def show_credits(cfg):
    key = os.environ.get("HUNTER_API_KEY", "").strip()
    if not key:
        raise OutreachError(f"HUNTER_API_KEY is not set in {jw.ENV_PATH}")
    now = dt.datetime.now(dt.timezone.utc)
    state = jw.load_json(jw.STATE_PATH, {})
    hunter = Hunter(key, state, float(cfg["outreach"]["monthly_hunter_credit_limit"]), now)
    data = (hunter.account() or {}).get("data") or {}
    print(f"Counted by this script this month: {hunter.used.get(hunter.month, 0)} of {hunter.limit}.")
    for name, value in (data.get("requests") or {}).items():
        if isinstance(value, dict) and "used" in value:
            print(f"Hunter account, {name}: {value.get('used')} used of {value.get('available')}.")
    if data.get("reset_date"):
        print(f"Hunter resets on {data['reset_date']}.")


def main():
    parser = argparse.ArgumentParser(description="Prepare recruiter outreach for jobs marked Email in your tracker.")
    parser.add_argument("--follow-ups", action="store_true", help="prepare follow-ups for unanswered emails")
    parser.add_argument("--make-drafts", metavar="FOLDER", help="put a packet's emails.json into Gmail Drafts")
    parser.add_argument("--export-drafts", metavar="FOLDER",
                        help="write a packet's emails.json as .md and .eml draft files")
    parser.add_argument("--credits", action="store_true", help="show Hunter credits used and left")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M",
                        handlers=[logging.FileHandler(jw.LOG_PATH, encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    jw.load_env()
    jw.guard_logs()
    try:
        cfg = jw.load_config()
        if args.make_drafts:
            make_drafts(cfg, args.make_drafts)
        elif args.export_drafts:
            export_drafts(cfg, args.export_drafts)
        elif args.credits:
            show_credits(cfg)
        else:
            with jw.RunLock():  # it updates the tracker, so never alongside a run
                prepare(cfg, follow_ups=args.follow_ups)
    except (OutreachError, jw.ConfigError, jw.Busy) as exc:
        log.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
