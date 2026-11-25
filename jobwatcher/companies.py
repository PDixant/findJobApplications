"""The company list (companies.csv) and company scores."""
from __future__ import annotations

from .base import *  # noqa: F401,F403
from .boards import *  # noqa: F401,F403


# ----------------------------------------------------------------- company list
CSV_FIELDS = ["priority", "region", "company", "score", "tier", "type", "ticker", "stage",
              "last_raise", "layoffs_12m", "board", "slug", "careers_url", "domain", "ats", "hubs", "enabled",
              "status", "verified", "market_cap_b", "stock_12m_pct", "revenue_growth_pct",
              "profit_margin_pct", "metrics_updated", "notes"]


def companies_path(cfg):
    return BASE / cfg.get("companies_file", MY_COMPANIES)


def load_company_rows(cfg):
    """Rows from companies.csv, plus any extra_companies listed in config.toml."""
    rows, path = [], companies_path(cfg)
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
                if r.get("company"):
                    rows.append(r)
    else:
        log.warning("%s not found; using only the extra_companies listed in config.toml", path.name)
    for extra in cfg.get("companies", []):
        board, slug = extra.split(":", 1) if ":" in extra else ("auto", extra)
        rows.append({"priority": "9", "region": "From config file", "company": display_name(extra),
                     "board": board, "slug": slug, "enabled": "yes"})
    return rows


def save_company_rows(cfg, rows):
    """Write detected boards, metrics and scores back so the list maintains itself."""
    path = companies_path(cfg)
    file_rows = sorted((r for r in rows if r.get("region") != "From config file"),
                       key=lambda r: (priority_of(r), r["company"].lower()))
    fields = list(CSV_FIELDS) + sorted({k for r in file_rows for k in r} - set(CSV_FIELDS))
    tmp = path.with_name(path.stem + ".saving.csv")
    try:
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(file_rows)
        replace_file(tmp, path)
    except PermissionError:
        log.warning("%s is open in another program; updates will be saved next run.", path.name)
        tmp.unlink(missing_ok=True)


def reads_website(row):
    """True when this company is read from its own careers website."""
    board = (row.get("board") or "").lower()
    url = row.get("careers_url") or (row.get("slug") if board == "website" else "")
    return bool(SETTINGS["career_sites"] and url and (board == "website" or board in MANUAL_BOARDS))


def entry_for(row):
    board, slug = (row.get("board") or "auto").lower(), row.get("slug") or ""
    if reads_website(row):
        return f"website:{row.get('careers_url') or slug}"
    return f"{board}:{slug}" if board in FETCHERS and board != "website" and slug else (slug or row["company"])


def is_enabled(row):
    return (row.get("enabled") or "yes").lower() not in ("no", "n", "false", "0")


def is_manual(row):
    board = (row.get("board") or "").lower()
    return (board in MANUAL_BOARDS or board == "website") and not reads_website(row)


def priority_of(row):
    try:
        return int(row.get("priority") or 9)
    except ValueError:
        return 9


# ----------------------------------------------------------------- scoring
def num(value):
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


STAGE_BASE = {"seed": 45, "early-stage": 47, "series a": 50, "series b": 55, "series c": 60,
              "series d+": 64, "late": 64, "unicorn": 68, "bootstrapped": 55, "nonprofit": 55,
              "subsidiary": 58}


def score_row(r, today=None):
    """0-100 'worth applying' score. Returns ('', '') when there is not enough data yet."""
    today = today or dt.date.today()
    layoffs = (r.get("layoffs_12m") or "").strip()
    penalty = 0 if not layoffs else (4 if "minor" in layoffs.lower() else 12)
    if (r.get("type") or "").lower() == "public":
        s12, rg, pm = num(r.get("stock_12m_pct")), num(r.get("revenue_growth_pct")), num(r.get("profit_margin_pct"))
        cap = num(r.get("market_cap_b"))
        if s12 is None and rg is None and pm is None:
            return "", ""
        score = 50.0
        if s12 is not None:
            score += clamp(s12, -50, 50) * 0.3       # share price trend: up to +/-15
        if rg is not None:
            score += clamp(rg, -20, 40) * 0.5        # revenue growth: -10 to +20
        if pm is not None:
            score += clamp(pm, -30, 30) * 0.25       # profitability: +/-7.5
        if cap is not None:
            score += 5 if cap >= 50 else 3 if cap >= 5 else 0 if cap >= 1 else -5
    else:
        base = STAGE_BASE.get((r.get("stage") or "").strip().lower())
        if base is None:
            return "", ""
        score = float(base)
        m = re.match(r"(\d{4})(?:-(\d{1,2}))?", r.get("last_raise") or "")
        if m:
            months = (today.year - int(m.group(1))) * 12 + today.month - int(m.group(2) or 6)
            score += 8 if months <= 18 else -8 if months > 30 else 0  # fresh funding = runway
    score = round(clamp(score - penalty, 0, 100))
    return score, ("A" if score >= 65 else "B" if score >= 50 else "C")


def refresh_metrics(cfg, rows, force=False):
    """Pull market cap, 12-month stock change, revenue growth and margin for public companies."""
    days = cfg.get("metrics_refresh_days", 7)
    today = dt.date.today()

    def due(r):
        last = parse_date(r.get("metrics_updated"))
        return force or not last or (today - last.date()).days >= days

    todo = [r for r in rows if r.get("ticker") and due(r)]
    if not todo:
        return False
    try:
        import yfinance as yf
    except ImportError:
        log.info("Stock metrics skipped: run 'pip install yfinance' to enable them.")
        return False
    log.info("Refreshing stock metrics for %d companies...", len(todo))
    started = time.monotonic()
    for i, r in enumerate(todo, start=1):
        if CANCEL.is_set():
            log.info("Metrics refresh cancelled after %d of %d companies.", i - 1, len(todo))
            break
        if i % 25 == 0 or i == len(todo):
            log.info("  metrics: %d of %d companies (%.1f min)", i, len(todo), (time.monotonic() - started) / 60)
        emit("metrics_progress", done=i, total=len(todo))
        try:
            t = yf.Ticker(r["ticker"])
            info = t.info or {}
            change = info.get("52WeekChange")
            if change is None:
                hist = t.history(period="1y")
                if len(hist) > 1:
                    change = float(hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1)
            cap, rg, pm = info.get("marketCap"), info.get("revenueGrowth"), info.get("profitMargins")
            r["market_cap_b"] = f"{cap / 1e9:.1f}" if cap else ""
            r["stock_12m_pct"] = f"{change * 100:.1f}" if change is not None else ""
            r["revenue_growth_pct"] = f"{rg * 100:.1f}" if rg is not None else ""
            r["profit_margin_pct"] = f"{pm * 100:.1f}" if pm is not None else ""
            r["metrics_updated"] = today.isoformat()
        except Exception as exc:
            log.warning("Metrics for %s (%s) failed: %s", r["company"], r["ticker"], exc)
        time.sleep(cfg.get("metrics_delay_seconds", 0.5))
    return True


def rescore(rows):
    changed = False
    for r in rows:
        score, tier = score_row(r)
        if (str(r.get("score", "")), r.get("tier", "")) != (str(score), tier):
            r["score"], r["tier"] = str(score), tier
            changed = True
    return changed


__all__ = [n for n in list(globals()) if not n.startswith("__")]
