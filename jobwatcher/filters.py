"""Your filters: titles, regions, stack keywords and posting age."""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .signals import years_required


# ----------------------------------------------------------------- filtering
class Rules:
    def __init__(self, cfg):
        self.title_in = compile_terms(cfg["titles_include"])
        self.title_out = compile_terms(cfg["titles_exclude"])
        self.stack = compile_terms(cfg["stack_keywords"])
        self.loc_out = compile_terms(cfg.get("locations_exclude", []))
        self.regions = [(r["name"], compile_terms(r["match"])) for r in cfg["regions"]]
        self.require_stack = cfg.get("require_stack_match", True)
        self.my_years = float(cfg.get("years_experience") or 0)
        self.years_over = float(cfg.get("years_over_ok") or 0)
        self.skip_too_many_years = cfg.get("too_many_years", "skip") == "skip"
        hours = cfg.get("max_posted_age_hours")
        if hours is None and cfg.get("max_posted_age_days"):
            hours = cfg["max_posted_age_days"] * 24
        self.max_age = dt.timedelta(hours=hours if hours is not None else 24)

    def title_ok(self, title):
        return any_match(self.title_in, title) and not any_match(self.title_out, title)

    def region_for(self, locations):
        kept = [l for l in locations if l and not any_match(self.loc_out, l)]
        for name, matchers in self.regions:
            if any(any_match(matchers, l) for l in kept):
                return name
        return None

    def stack_for(self, title, text):
        return all_matches(self.stack, f"{title}\n{text or ''}")

    def too_many_years(self, years):
        """True when a posting asks for more years than yours plus the allowance."""
        return bool(self.my_years and years and years > self.my_years + self.years_over)


def level_for(title):
    if re.search(r"(?<![A-Za-z])(Senior|Sr\.?|Lead)(?![A-Za-z])", title, re.I):
        return "Senior/Lead"
    if re.search(r"(?<![A-Za-z0-9])(II|2|Intermediate|Level 2)(?![A-Za-z0-9])", title):
        return "Mid"
    return "Unlabelled"



def find_new_matches(store, state, rules, now, max_age, window_start):
    """The local filter: checks every saved open posting against your rules. No requests."""
    matches = []
    for r in store.open_postings():
        key = r["key"]
        if key in state["jobs"] or not rules.title_ok(r["title"]):
            continue
        posted = parse_date(r["posted"])
        # Undated postings count from when they first appeared, unless they were already
        # up the first time their board was read.
        appeared = posted or (None if r["baseline"] else parse_date(r["first_seen"]))
        if appeared is None or now - appeared > max_age or (window_start and appeared < window_start):
            continue
        locations = json.loads(r["locations"] or "[]")
        region = rules.region_for(locations)
        if not region:
            continue
        text = store.text_of(key) if r["has_text"] else None
        if text is None and rules.require_stack:
            continue  # description not downloaded yet; it is fetched on the next run
        stack = rules.stack_for(r["title"], text)
        if rules.require_stack and not stack:
            continue
        years = years_required(text)
        if rules.skip_too_many_years and rules.too_many_years(years):
            continue  # asks for more experience than you have (Which jobs, years of experience)
        matches.append({"key": key, "company": r["company"], "provider": r["provider"], "title": r["title"],
                        "locations": locations, "region": region, "stack": stack, "url": r["url"],
                        "posted": posted})
    return matches


__all__ = [n for n in list(globals()) if not n.startswith("__")]
