"""Pay, work authorization and workplace reading; job-system readers; phone alerts."""
import datetime as dt

import jobwatcher as jw
from jobwatcher import alerts, signals, systems


def test_pay_ranges():
    p = signals.pay_range("Base salary: $140,000\u2014$190,000 USD per year", "Vancouver / BC")
    assert (p["min"], p["max"], p["currency"]) == (140000, 190000, "USD")
    p = signals.pay_range("The pay range for this role is $120k - $150k.", "Vancouver / BC")
    assert (p["min"], p["max"], p["currency"]) == (120000, 150000, "CAD")
    p = signals.pay_range("Hourly wage: $45.00 - $60.00 per hour", "Seattle")
    assert p["period"] == "hour" and p["min"] == 45
    assert signals.pay_range("We have 3,000 - 5,000 customers", "") is None


def test_auth_workplace_relocation():
    ids = lambda text: [a["id"] for a in signals.work_auth(text)]  # noqa: E731
    assert ids("We are unable to sponsor visas for this role.") == ["no_sponsorship"]
    assert ids("Must hold an active Secret clearance.") == ["clearance"]
    assert ids("We welcome applicants from anywhere.") == []
    assert signals.workplace("Frontend Developer (Remote)", ["Canada"], "") == "remote"
    assert signals.workplace("Developer", ["Toronto"], "This is a hybrid role, 3 days a week in the office.") == "hybrid"
    assert signals.relocation("Relocation assistance is available.") is True
    assert signals.relocation("No relocation assistance is provided.") is False


def test_detect_job_systems():
    assert systems.detect("https://amazon.jobs", "https://www.amazon.jobs/en/", "")[0] == "amazon"
    o = systems.detect("x", "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/requisitions", "")
    assert o == ("oracle", {"host": "eeho.fa.us2.oraclecloud.com", "lang": "en", "site": "CX_1"})
    e = systems.detect("x", "https://careers.example.com/careers?domain=example.com", "<script src=eightfold></script>")
    assert e == ("eightfold", {"base": "https://careers.example.com", "domain": "example.com"})
    i = systems.detect("x", "https://www.example.com/careers", '<iframe src="https://careers-example.icims.com/jobs"></iframe>')
    assert i[0] == "icims" and i[1]["search"].startswith("https://careers-example.icims.com/jobs/search")
    assert systems.detect("x", "https://www.example.com/careers", "<p>jobs</p>") is None


def test_readers(monkeypatch):
    jw.SETTINGS.setdefault("search_terms", ["frontend"])
    monkeypatch.setitem(jw.SETTINGS, "search_terms", ["frontend"])

    def amazon_json(url, params=None):
        assert "amazon.jobs" in url
        return {"jobs": [{"id_icims": "301", "title": "Frontend Engineer", "normalized_location": "Vancouver, BC, CAN",
                          "job_path": "/en/jobs/301/frontend-engineer", "posted_date": "November 18, 2026",
                          "description": "<p>Build UI</p>", "basic_qualifications": "React"}]}
    monkeypatch.setattr(systems, "get_json", amazon_json)
    r = systems.amazon("https://amazon.jobs")
    j = r["jobs"][0]
    assert j["key"] == "amazon:301" and j["url"].endswith("/en/jobs/301/frontend-engineer")
    assert j["posted"].date() == dt.date(2026, 11, 18) and "Build UI" in j["text"]

    def oracle_json(url, params=None):
        if url.endswith("recruitingCEJobRequisitions"):
            return {"items": [{"TotalJobsCount": 1, "requisitionList": [
                {"Id": "77", "Title": "UI Developer", "PrimaryLocation": "Vancouver, BC, Canada",
                 "PostedDate": "2026-11-15", "secondaryLocations": [{"Name": "Remote, Canada"}]}]}]}
        return {"items": [{"ExternalDescriptionStr": "<p>Design systems</p>"}]}
    monkeypatch.setattr(systems, "get_json", oracle_json)
    r = systems.oracle("x", "h.oraclecloud.com", "en", "CX_1")
    j = r["jobs"][0]
    assert r["complete"] and j["locations"] == ["Vancouver, BC, Canada", "Remote, Canada"]
    assert j["url"].endswith("/sites/CX_1/job/77") and "Design systems" in j["load_detail"]()["text"]

    def eightfold_json(url, params=None):
        return {"count": 1, "positions": [{"id": 9, "name": "Web Engineer", "locations": ["Seattle, WA"],
                                           "t_create": 1763500000, "canonicalPositionUrl": "https://c/job/9"}]}
    monkeypatch.setattr(systems, "get_json", eightfold_json)
    j = systems.eightfold("x", "https://c", "c.com")["jobs"][0]
    assert j["key"] == "eightfold:c.com:9" and j["text"] is None and j["load_detail"] is not None

    def broken(url, params=None):
        raise ValueError("odd answer")
    monkeypatch.setattr(systems, "get_json", broken)
    assert systems.read("amazon", {}, "https://amazon.jobs") is None  # falls back to the careers reader


def test_phone_alerts(monkeypatch):
    sent = []

    class Reply:
        status_code = 200
    monkeypatch.setattr(alerts.requests, "post", lambda url, data=None, headers=None, timeout=None: sent.append(
        (url, data, headers)) or Reply())
    assert alerts.new_matches({"alerts": {"ntfy_topic": ""}}, [{"company": "A", "title": "B"}]) is False
    cfg = {"alerts": {"ntfy_topic": "jobwatcher-abc", "ntfy_server": "https://ntfy.sh"}}
    assert alerts.new_matches(cfg, [{"company": "Maple", "title": "Frontend", "region": "BC", "url": "https://x"}])
    url, data, headers = sent[0]
    assert url == "https://ntfy.sh/jobwatcher-abc" and b"Maple: Frontend" in data and headers["Click"] == "https://x"
    assert len(alerts.suggest_topic()) > 20


def test_keys_never_reach_the_log(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("HUNTER_API_KEY", "hunter-secret-123456")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    assert "hunter-secret" not in jw.redact("GET /v2/domain-search?domain=x.com&api_key=hunter-secret-123456")
    assert jw.redact("https://serpapi.com/search.json?q=a&api_key=zzz999yyy") == "https://serpapi.com/search.json?q=a&api_key=****"
    assert "abcdefghijklmnop" not in jw.redact("login abcdefghijklmnop failed")
    handler = logging.Handler()
    seen = []
    handler.emit = lambda record: seen.append(record.getMessage())
    jw.guard_logs(handler)
    jw.log.addHandler(handler)
    try:
        jw.log.warning("Hunter said %s", "bad key hunter-secret-123456")
    finally:
        jw.log.removeHandler(handler)
    assert seen == ["Hunter said bad key ****"]
