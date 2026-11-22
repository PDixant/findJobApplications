"""The Excel tracker: reading your edits and writing job_tracker.xlsx."""
from __future__ import annotations

from .base import *  # noqa: F401,F403


# ----------------------------------------------------------------- excel
COLUMNS = [("Found", 17), ("Posted", 11), ("Company", 20), ("Score", 7), ("Title", 46),
           ("Location", 34), ("Region", 22), ("Level", 12), ("Stack match", 28), ("Board", 14),
           ("Status", 14), ("Notes", 30), ("Outreach", 12), ("Posting", 9), ("Key", 10)]
HEADERS = [c for c, _ in COLUMNS]
COL = {name: i + 1 for i, name in enumerate(HEADERS)}
LETTER = {name: chr(64 + i) for name, i in COL.items()}


def fill(hex_color):
    return PatternFill("solid", start_color=hex_color, end_color=hex_color)


def header_row(ws):
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill("1F3864")
        cell.alignment = Alignment(vertical="center")


def read_excel_edits(path):
    """Pull Status and Notes the user typed into the tracker, keyed by job key."""
    if not path.exists():
        return {}
    try:
        wb = load_workbook(path, read_only=True)
        rows = wb["Jobs"].iter_rows(values_only=True)
        header = list(next(rows))
        ki, si, ni = header.index("Key"), header.index("Status"), header.index("Notes")
        oi = header.index("Outreach") if "Outreach" in header else None  # older trackers lack it
        edits = {}
        for row in rows:
            if row and row[ki]:
                edits[row[ki]] = {"status": row[si] or "New", "notes": row[ni] or ""}
                if oi is not None:
                    edits[row[ki]]["outreach"] = (row[oi] or "").strip()
        wb.close()
        return edits
    except Exception as exc:
        log.warning("Could not read edits from %s (%s)", path.name, exc)
        return {}


def apply_excel_edits(state, edits, now):
    """Bring your Status, Notes and Outreach edits from the tracker into the saved state."""
    for key, edit in edits.items():
        j = state["jobs"].get(key)
        if not j:
            continue
        outreach = edit.pop("outreach", None)  # None when the tracker has no Outreach column
        j.update(edit)
        if outreach is None:
            continue
        old = j.get("outreach", "")
        if outreach == "Email" and old and old != "Email" and j.get("outreach_prepared"):
            continue  # a tracker saved before outreach.py ran; keep what outreach.py recorded
        if outreach == "Sent" and old != "Sent" and not j.get("outreach_sent"):
            j["outreach_sent"] = now.isoformat()  # starts the follow-up clock
        j["outreach"] = outreach


def write_excel(path, state, cfg, scores):
    region_colors = {r["name"]: r.get("color") for r in cfg["regions"]}
    jobs = sorted(state["jobs"].values(),
                  key=lambda j: (j["open"], j["found"], j.get("posted") or ""), reverse=True)

    def style_job_cells(ws, r, j, cols):
        if j.get("url"):
            ws.cell(r, cols["Title"]).hyperlink = j["url"]
            ws.cell(r, cols["Title"]).font = Font(color="0563C1", underline="single")
        if region_colors.get(j["region"]):
            ws.cell(r, cols["Region"]).fill = fill(region_colors[j["region"]])
        if LEVEL_COLORS.get(j["level"]):
            ws.cell(r, cols["Level"]).fill = fill(LEVEL_COLORS[j["level"]])
        tier = scores.get(j["company"], ("", ""))[1]
        if tier in TIER_COLORS:
            ws.cell(r, cols["Score"]).fill = fill(TIER_COLORS[tier])

    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append(HEADERS)
    header_row(ws)
    ws.row_dimensions[1].height = 22
    for j in jobs:
        score = scores.get(j["company"], ("", ""))[0]
        ws.append([
            to_local_naive(j["found"]), to_local_naive(j.get("posted")), j["company"],
            int(score) if str(score).isdigit() else None, j["title"],
            "; ".join(dict.fromkeys(j["locations"])), j["region"], j["level"], ", ".join(j["stack"]),
            j["provider"].title(), j.get("status", "New"), j.get("notes", ""), j.get("outreach", ""),
            "Open" if j["open"] else "Closed", j["key"],
        ])
        r = ws.max_row
        ws.cell(r, COL["Found"]).number_format = "yyyy-mm-dd hh:mm"
        ws.cell(r, COL["Posted"]).number_format = "yyyy-mm-dd"
        style_job_cells(ws, r, j, COL)

    last = max(ws.max_row, 2)
    for name, width in COLUMNS:
        ws.column_dimensions[LETTER[name]].width = width
    ws.column_dimensions[LETTER["Key"]].hidden = True
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{LETTER['Posting']}{last}"
    S, F, P = LETTER["Status"], LETTER["Found"], LETTER["Posting"]

    dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{S}2:{S}{last + 500}")
    O = LETTER["Outreach"]
    dv_out = DataValidation(type="list", formula1='"' + ",".join(OUTREACH) + '"', allow_blank=True)
    ws.add_data_validation(dv_out)
    dv_out.add(f"{O}2:{O}{last + 500}")
    for value, bg in OUTREACH_COLORS.items():
        ws.conditional_formatting.add(f"{O}2:{O}{last + 500}", FormulaRule(
            formula=[f'${O}2="{value}"'], fill=fill(bg)))
    ws.conditional_formatting.add(f"{F}2:{F}{last}", FormulaRule(
        formula=[f"AND(ISNUMBER(${F}2),NOW()-${F}2<1)"],
        fill=fill("C6EFCE"), font=Font(bold=True, color="006100")))
    for status, (bg, fg) in STATUS_COLORS.items():
        ws.conditional_formatting.add(f"{S}2:{S}{last + 500}", FormulaRule(
            formula=[f'${S}2="{status}"'], fill=fill(bg), font=Font(bold=True, color=fg)))
    ws.conditional_formatting.add(f"A2:{P}{last}", FormulaRule(
        formula=[f'OR(${S}2="Rejected",${S}2="Skipped",${P}2="Closed")'],
        font=Font(color="999999", italic=True)))

    # Since Last Check
    rep = state.get("last_check_report")
    if rep:
        sc = wb.create_sheet("Since Last Check", 0)
        start, end = to_local_naive(rep["from"]), to_local_naive(rep["to"])
        sc.append([f"Postings since {start:%Y-%m-%d %H:%M}  (checked {end:%Y-%m-%d %H:%M})"])
        sc["A1"].font = Font(bold=True, size=13, color="1F3864")
        sc.append([f"{len(rep['keys'])} matching posting(s). Track status on the Jobs tab; "
                   "this tab is rebuilt each time you run with --since-last-check."])
        sc["A2"].font = Font(italic=True, color="595959")
        sc.append([])
        sc_cols = ["Posted", "Found", "Company", "Score", "Title", "Location", "Region", "Level",
                   "Stack match"]
        sc.append(sc_cols)
        header_row(sc)
        cols = {n: i + 1 for i, n in enumerate(sc_cols)}
        for key in rep["keys"]:
            j = state["jobs"].get(key)
            if not j:
                continue
            score = scores.get(j["company"], ("", ""))[0]
            sc.append([to_local_naive(j.get("posted")), to_local_naive(j["found"]), j["company"],
                       int(score) if str(score).isdigit() else None, j["title"],
                       "; ".join(dict.fromkeys(j["locations"])), j["region"], j["level"],
                       ", ".join(j["stack"])])
            r = sc.max_row
            sc.cell(r, 1).number_format = sc.cell(r, 2).number_format = "yyyy-mm-dd hh:mm"
            style_job_cells(sc, r, j, cols)
        if not rep["keys"]:
            sc.append(["Nothing new matched your filters in this window."])
        for i, w in enumerate([17, 17, 20, 7, 46, 34, 22, 12, 28], start=1):
            sc.column_dimensions[chr(64 + i)].width = w
        sc.freeze_panes = "A5"
        wb.active = 0

    # Legend
    lg = wb.create_sheet("Legend")
    lg.append(["Colour", "Meaning"])
    header_row(lg)
    legend = [("C6EFCE", "Found column: discovered in the last 24 hours")]
    legend += [(TIER_COLORS["A"], "Score 65+: tier A, strong signals"),
               (TIER_COLORS["B"], "Score 50-64: tier B, mixed signals"),
               (TIER_COLORS["C"], "Score under 50: tier C, weak signals or recent layoffs")]
    legend += [(r.get("color"), f"Region: {r['name']}") for r in cfg["regions"] if r.get("color")]
    legend += [(c, f"Level: {lvl}") for lvl, c in LEVEL_COLORS.items() if c]
    legend += [(bg, f"Status: {s}") for s, (bg, _) in STATUS_COLORS.items()]
    legend += [(OUTREACH_COLORS["Email"], "Outreach: Email = you want a recruiter email prepared (run outreach.py)"),
               (OUTREACH_COLORS["Prepared"], "Outreach: Prepared = contacts found, ready for drafting"),
               (OUTREACH_COLORS["No contact"], "Outreach: No contact = no recruiter email found; see the packet for LinkedIn links"),
               (OUTREACH_COLORS["Sent"], "Outreach: Sent = you sent it (set this yourself)"),
               (OUTREACH_COLORS["Follow-up"], "Outreach: Follow-up = a follow-up has been prepared"),
               (OUTREACH_COLORS["Replied"], "Outreach: Replied = they answered (set this yourself)")]
    legend += [("FFFFFF", "Grey italic row: Rejected, Skipped, or posting no longer on the board")]
    for color, text in legend:
        lg.append(["", text])
        lg.cell(lg.max_row, 1).fill = fill(color)
    lg.column_dimensions["A"].width = 10
    lg.column_dimensions["B"].width = 70

    # Companies
    cs = wb.create_sheet("Companies")
    cs.append(["Priority", "Region", "Company", "Score", "Tier", "Board", "Slug", "Result",
               "Open postings", "Matches", "Last checked", "Note"])
    header_row(cs)
    ordered = sorted(state.get("companies", {}).items(),
                     key=lambda kv: (kv[1].get("priority", 9), kv[0].lower()))
    result_colors = {"OK": "C6EFCE", "NOT FOUND": "F8CBAD", "MANUAL": "DDEBF7", "RETRY": "FFE699",
                     "PENDING": "EDEDED", "ERROR": "F4B183", "NO JOBS READ": "FCE4D6"}
    for name, c in ordered:
        score, tier = scores.get(name, ("", ""))
        cs.append([c.get("priority", ""), c.get("region", ""), name,
                   int(score) if str(score).isdigit() else None, tier, c.get("provider", ""),
                   c.get("slug", ""), c["result"], c.get("open", 0), c.get("matches", 0),
                   to_local_naive(c.get("checked")), c.get("error", "")])
        r = cs.max_row
        cs.cell(r, 11).number_format = "yyyy-mm-dd hh:mm"
        cs.cell(r, 8).fill = fill(result_colors.get(c["result"], "FFE699"))
        if tier in TIER_COLORS:
            cs.cell(r, 4).fill = cs.cell(r, 5).fill = fill(TIER_COLORS[tier])
        if c.get("careers_url"):
            cs.cell(r, 12).hyperlink = c["careers_url"]
            cs.cell(r, 12).font = Font(color="0563C1", underline="single")
    for col, w in zip("ABCDEFGHIJKL", [9, 20, 28, 7, 6, 15, 26, 12, 13, 9, 17, 60]):
        cs.column_dimensions[col].width = w
    cs.freeze_panes = "A2"
    cs.auto_filter.ref = f"A1:L{max(cs.max_row, 2)}"

    tmp = path.with_name(path.stem + ".saving.xlsx")
    try:
        wb.save(tmp)
        os.replace(tmp, path)
        return True
    except PermissionError:
        log.warning("%s is open in Excel; it will be updated on the next run after you close it.",
                    path.name)
        tmp.unlink(missing_ok=True)
        return False


__all__ = [n for n in list(globals()) if not n.startswith("__")]
