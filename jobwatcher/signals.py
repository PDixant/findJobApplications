"""What a posting says besides its title: pay range, work authorization rules, remote or on-site,
relocation, and whether it was reposted or has been open a long time. Read locally from the
saved description; no requests."""
from __future__ import annotations

from .base import *  # noqa: F401,F403

_CUR = r"(?:CA\$|C\$|US\$|\$|USD\s?|CAD\s?|EUR\s?|GBP\s?|€|£)"
_NUM = r"\d{1,3}(?:[,  ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s?[kK]\b|\d{2,3}(?:\.\d{1,2})?"
RANGE = re.compile(
    rf"(?P<c1>{_CUR})?\s?(?P<a>{_NUM})\s*(?:-|\u2013|\u2014|to|and)\s*(?P<c2>{_CUR})?\s?(?P<b>{_NUM})"
    r"\s*(?P<c3>USD|CAD|EUR|GBP)?(?:\s*(?:per|/|an?|each)\s*(?P<per>hour|hr|year|yr|annum|annually))?", re.I)
PAY_WORDS = re.compile(r"salary|pay|compensation|wage|base|range|ote|earn", re.I)

AUTH_RULES = [
    ("citizens_only", "Citizens only", re.compile(
        r"\b(must|need to|required to) (be|hold) (a )?(u\.?s\.?|united states|us|canadian) citizen|"
        r"\b(u\.?s\.?|united states) citizenship (is )?required|\bcitizens only\b", re.I)),
    ("clearance", "Security clearance", re.compile(
        r"security clearance|secret clearance|top secret|\bts/sci\b|active clearance|clearance (is )?required|"
        r"\bpublic trust\b", re.I)),
    ("no_sponsorship", "No visa sponsorship", re.compile(
        r"\b(not|unable to|cannot|can't|won't|will not|do not|does not)\s+(be able to\s+)?(provide\s+|offer\s+)?"
        r"(visa\s+|immigration\s+|work permit\s+)?sponsor|without (the need for |requiring )?(current or future )?"
        r"(visa |employer )?sponsorship|sponsorship (is )?not (available|offered|provided)", re.I)),
]
REMOTE = re.compile(r"\bremote\b|work from (home|anywhere)|distributed team|remote-first", re.I)
HYBRID = re.compile(r"\bhybrid\b|\d days? (a|per) week in (the )?office|in (the )?office \d days", re.I)
ONSITE = re.compile(r"\bon-?site\b|\bin-office\b|in office (five|5) days|fully in (the )?office", re.I)
RELOCATION_YES = re.compile(r"relocation (assistance|support|package|bonus|benefits?|is available|is provided|offered)|"
                            r"(offer|provide)s? relocation|relocation (may be|will be) (available|provided)", re.I)
RELOCATION_NO = re.compile(r"no relocation|relocation (is )?not (offered|available|provided)|"
                           r"(not|unable to) (offer|provide) relocation", re.I)
LONG_OPEN_DAYS = 45
CANADA = re.compile(r"canada|\bcan\b|\b(bc|on|ab|qc|ns|mb|sk)\b|british columbia|ontario|alberta|quebec|"
                    r"vancouver|toronto|\bgta\b|ottawa|montr[eé]al|calgary|edmonton|waterloo|kitchener|victoria|"
                    r"burnaby|halifax|winnipeg|mississauga|brampton", re.I)


def _number(text):
    t = text.lower().replace(",", "").replace(" ", "").replace(" ", "")
    if t.endswith("k"):
        return float(t[:-1]) * 1000
    return float(t)


def pay_range(text, place=""):
    """The pay range a posting states, or None. Many places require it now (BC, Ontario,
    California, Washington, New York and more)."""
    if not text:
        return None
    for m in RANGE.finditer(text):
        found = [(m.group(g) or "").strip().upper() for g in ("c1", "c2", "c3")]
        currency = next((c for c in found if c and c != "$"), "") or next((c for c in found if c), "")
        before = text[max(0, m.start() - 160):m.start()]
        if not currency and not PAY_WORDS.search(before):
            continue
        try:
            low, high = _number(m.group("a")), _number(m.group("b"))
        except ValueError:
            continue
        if "k" in m.group("b").lower() and "k" not in m.group("a").lower() and low < 1000:
            low *= 1000  # "$120 - 150K"
        if low > high or high > 5 * low:
            continue
        per = (m.group("per") or "").lower()
        hourly = per in ("hour", "hr") or (not per and high < 500)
        if hourly and not 10 <= low <= 500 or not hourly and not 15000 <= low <= 1500000:
            continue
        if currency in ("CA$", "C$", "CAD"):
            cur = "CAD"
        elif currency in ("US$", "USD"):
            cur = "USD"
        elif currency in ("EUR", "€"):
            cur = "EUR"
        elif currency in ("GBP", "£"):
            cur = "GBP"
        else:
            cur = "CAD" if CANADA.search(place or "") else "USD"  # a bare "$" follows where the job is
        return {"min": round(low), "max": round(high), "currency": cur, "period": "hour" if hourly else "year",
                "text": m.group(0).strip()}
    return None


def work_auth(text):
    return [{"id": key, "label": label} for key, label, rx in AUTH_RULES if text and rx.search(text)]


def workplace(title, locations, text):
    where = " ".join([title or "", *(locations or [])])
    if HYBRID.search(where):
        return "hybrid"
    if REMOTE.search(where):
        return "remote"
    body = text or ""
    if HYBRID.search(body):
        return "hybrid"
    if ONSITE.search(body):
        return "on-site"
    if re.search(r"(fully|100%) remote|remote-first|work from anywhere", body, re.I):
        return "remote"
    return ""


def relocation(text):
    if not text:
        return None
    if RELOCATION_NO.search(text):
        return False
    if RELOCATION_YES.search(text):
        return True
    return None


NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}
_N = r"\d{1,2}|" + "|".join(NUMBER_WORDS)
YEARS = re.compile(rf"(?<![\d.$,])({_N})\s*(?:\+|plus)?\s*(?:(?:-|to|\u2013|\u2014)\s*(?:{_N})\s*\+?\s*)?"
                   rf"(?:or more\s+)?(?:years?|yrs?)\b", re.I)
WANTED_ONLY = re.compile(r"\b(prefer|preferred|nice to have|nice-to-have|bonus|ideally|asset|a plus|desirable|"
                         r"would be great|not required)\b", re.I)


def years_required(text):
    """The most years of experience the posting asks for (the low end of a range such as 3 to 5),
    or None. Only sentences about experience count, and not ones that call it preferred or a
    bonus: "7+ years of experience" counts, "we've been around for 20 years" and "10+ years
    preferred" don't."""
    if not text:
        return None
    most = None
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+|\u2022", text):
        if not re.search(r"experience|\bexp\b|background in|track record", sentence, re.I):
            continue
        if WANTED_ONLY.search(sentence):
            continue
        for m in YEARS.finditer(sentence):
            n = m.group(1).lower()
            years = NUMBER_WORDS.get(n, None) if not n.isdigit() else int(n)
            if years and 1 <= years <= 15:
                most = years if most is None else max(most, years)
    return most


def read(job, text):
    """All the signals for one tracked job."""
    place = " ".join([job.get("region") or "", *(job.get("locations") or [])])
    return {"pay": pay_range(text, place), "work_auth": work_auth(text),
            "workplace": workplace(job.get("title"), job.get("locations"), text), "relocation": relocation(text),
            "years": years_required(text)}


def norm_title(title):
    t = re.sub(r"\(.*?\)|\[.*?\]|\b(req|job)\s*#?\s*\w*\d\w*", " ", (title or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def posting_history(db, jobs, now=None):
    """Reposted (the same title at the same company appeared before) and long-open postings,
    from the saved postings."""
    now = now or dt.datetime.now(dt.timezone.utc)
    companies = sorted({j["company"] for j in jobs})
    seen = {}
    for i in range(0, len(companies), 200):
        chunk = companies[i:i + 200]
        for r in db.execute(f"SELECT key, company, title, first_seen FROM postings WHERE company IN "
                            f"({','.join('?' * len(chunk))})", chunk):
            seen.setdefault((r["company"], norm_title(r["title"])), []).append((r["first_seen"], r["key"]))
    out = {}
    for j in jobs:
        group = sorted(seen.get((j["company"], norm_title(j["title"])), []))
        mine = next((f for f, k in group if k == j["key"]), None)
        first = parse_date(mine)
        earlier = [f for f, k in group if k != j["key"] and first and parse_date(f) and parse_date(f) < first - dt.timedelta(days=7)]
        oldest = parse_date(group[0][0]) if group else first
        out[j["key"]] = {"reposted": bool(earlier),
                         "open_days": (now - oldest).days if oldest and j.get("open") else None,
                         "long_open": bool(oldest and j.get("open") and (now - oldest).days >= LONG_OPEN_DAYS)}
    return out


__all__ = ["pay_range", "work_auth", "workplace", "relocation", "read", "posting_history", "norm_title"]
