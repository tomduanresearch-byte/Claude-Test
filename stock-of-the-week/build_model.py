#!/usr/bin/env python3
"""Build a linked three-statement model with a DCF from SEC XBRL data.

Two steps, so the analyst's judgment sits between them:

  build_model.py fetch TICKER --out work/            # SEC data -> historicals.json + assumptions.json
  (edit work/assumptions.json: growth path, margins, WACC inputs, share price)
  build_model.py build work/ --xlsx model.xlsx      # -> formula-driven workbook + summary.json
  build_model.py page page.html work/ --thesis thesis.json   # -> add the pick to the Friday Pick page

The workbook is live: every projected line, every total and the whole DCF are
Excel formulas, so changing an input on the Assumptions or DCF sheet flows
through all three statements. `build` recalculates it with headless LibreOffice
and fails loudly if the balance sheet does not balance in every year.

Conventions: $ millions except per-share; blue = hard-coded input, black =
formula, green = link to another sheet.
"""

import argparse
import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

UA = os.environ.get("SEC_USER_AGENT", "stock-of-the-week research tomduanresearch@gmail.com")
N_PROJ = 5
MAX_HIST = 5
M = 1e6

# --------------------------------------------------------------------------- fetch

# First tag that has data wins. Order matters: the most specific tag goes first.
FLOW_TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "SalesRevenueGoodsNet"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
             "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "gross_profit": ["GrossProfit"],
    "rnd": ["ResearchAndDevelopmentExpense",
            "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "ebit": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
                         "InterestPaidNet"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
           "DepreciationAndAmortization", "Depreciation"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "cfi": ["NetCashProvidedByUsedInInvestingActivities",
            "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"],
    "cff": ["NetCashProvidedByUsedInFinancingActivities",
            "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
}
INSTANT_TAGS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "st_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                       "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "ar": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "inventory": ["InventoryNet"],
    "total_current_assets": ["AssetsCurrent"],
    "ppe": ["PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
    "total_assets": ["Assets"],
    "ap": ["AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"],
    "total_current_liabilities": ["LiabilitiesCurrent"],
    "st_debt": ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"],
    "total_liabilities": ["Liabilities"],
    "equity": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "StockholdersEquity"],
    "liabilities_and_equity": ["LiabilitiesAndStockholdersEquity"],
}
# Debt components that are summed rather than picked, so a filer that splits
# current debt into commercial paper and current maturities is not undercounted.
ST_DEBT_PARTS = ["CommercialPaper", "LongTermDebtCurrent", "ShortTermBorrowings"]


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def lookup_cik(ticker):
    data = http_json("https://www.sec.gov/files/company_tickers.json")
    for row in data.values():
        if row["ticker"].upper() == ticker.upper():
            return int(row["cik_str"]), row["title"]
    sys.exit(f"ticker {ticker} not found in SEC company_tickers.json")


def days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def annual_series(facts, tag, unit="USD", instant=False):
    """{period end date: value} from 10-K filings, latest filing winning."""
    node = facts.get(tag)
    if not node or unit not in node["units"]:
        return {}
    out = {}
    for f in sorted(node["units"][unit], key=lambda f: f.get("filed", "")):
        if f.get("form") not in ("10-K", "10-K/A", "10-KT"):
            continue
        if instant:
            if "start" in f:
                continue
        else:
            if "start" not in f or not 330 <= days(f["start"], f["end"]) <= 400:
                continue
        out[f["end"]] = f["val"]
    return out


def first_available(facts, tags, ends, unit="USD", instant=False):
    """Per year, the first tag in priority order with a value for that year."""
    series = [(t, annual_series(facts, t, unit, instant)) for t in tags]
    vals, used = [], []
    for e in ends:
        hit = next(((t, s[e]) for t, s in series if e in s), (None, None))
        used.append(hit[0])
        vals.append(hit[1])
    return vals, used


def cmd_fetch(args):
    os.makedirs(args.out, exist_ok=True)
    cik, name = lookup_cik(args.ticker)
    cf = http_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    facts = cf["facts"].get("us-gaap")
    if not facts:
        sys.exit(f"{args.ticker} has no us-gaap facts (likely an IFRS/20-F filer); pick a 10-K filer")

    # Fiscal year ends come from whichever revenue tag has the most recent data.
    rev_ends = set()
    for t in FLOW_TAGS["revenue"]:
        rev_ends |= set(annual_series(facts, t))
    ends = sorted(rev_ends)[-MAX_HIST:]
    if len(ends) < 3:
        sys.exit(f"only {len(ends)} annual revenue data points for {args.ticker}; need at least 3")

    hist, sources = {}, {}
    for key, tags in FLOW_TAGS.items():
        unit = "shares" if key == "diluted_shares" else "USD"
        hist[key], sources[key] = first_available(facts, tags, ends, unit)
    for key, tags in INSTANT_TAGS.items():
        hist[key], sources[key] = first_available(facts, tags, ends, instant=True)

    parts = [annual_series(facts, t, instant=True) for t in ST_DEBT_PARTS]
    summed = [sum(p[e] for p in parts if e in p) if any(e in p for p in parts) else None for e in ends]
    debt_current = annual_series(facts, "DebtCurrent", instant=True)
    hist["st_debt"] = [debt_current[e] if e in debt_current else (s if s is not None else v)
                       for e, s, v in zip(ends, summed, hist["st_debt"])]

    dei = cf["facts"].get("dei", {})
    so = dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    latest_shares = max(so, key=lambda f: f["end"]) if so else None

    out = {
        "ticker": args.ticker.upper(), "name": name, "cik": cik,
        "fiscal_year_ends": ends,
        "values": hist, "xbrl_tags": sources,
        "shares_outstanding_latest": latest_shares["val"] if latest_shares else None,
        "shares_outstanding_as_of": latest_shares["end"] if latest_shares else None,
        "source": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
        "fetched": dt.date.today().isoformat(),
    }
    with open(os.path.join(args.out, "historicals.json"), "w") as f:
        json.dump(out, f, indent=1)

    hs = normalize(out)
    with open(os.path.join(args.out, "assumptions.json"), "w") as f:
        json.dump(default_assumptions(hs, out), f, indent=1)

    missing = [k for k, v in hist.items() if v[-1] is None]
    print(f"{name} (CIK {cik}): fiscal years {ends[0]} .. {ends[-1]}")
    print(f"wrote {args.out}/historicals.json and {args.out}/assumptions.json")
    if missing:
        print("missing in latest year (treated as 0 / derived):", ", ".join(missing))


# --------------------------------------------------------------------------- normalize

def normalize(h):
    """Historicals in $m with derived plugs so the condensed statements tie."""
    v = {k: list(x) for k, x in h["values"].items()}
    n = len(h["fiscal_year_ends"])
    z = lambda x: 0.0 if x is None else float(x)
    rows = {}

    def put(k, vals):
        rows[k] = [None if x is None else x / M for x in vals]

    for k in v:
        if k != "diluted_shares":
            put(k, v[k])
    rows["diluted_shares"] = [None if x is None else x / M for x in v["diluted_shares"]]

    rev = rows["revenue"]
    cogs = rows["cogs"]
    gp = rows["gross_profit"]
    rows["cogs"] = [c if c is not None else (r - g if g is not None else 0.0)
                    for c, r, g in zip(cogs, rev, gp)]
    rows["rnd"] = [z(x) for x in rows["rnd"]]
    rows["sga"] = [z(x) for x in rows["sga"]]
    rows["da"] = [z(x) for x in rows["da"]]
    rows["sbc"] = [z(x) for x in rows["sbc"]]
    rows["capex"] = [z(x) for x in rows["capex"]]
    rows["dividends"] = [z(x) for x in rows["dividends"]]
    rows["buybacks"] = [z(x) for x in rows["buybacks"]]
    rows["interest_expense"] = [z(x) for x in rows["interest_expense"]]
    rows["tax"] = [z(x) for x in rows["tax"]]
    for i in range(n):
        if rows["ebit"][i] is None:
            rows["ebit"][i] = z(rows["pretax"][i]) + rows["interest_expense"][i]
        if rows["pretax"][i] is None:
            rows["pretax"][i] = rows["ebit"][i] - rows["interest_expense"][i]
        if rows["net_income"][i] is None:
            rows["net_income"][i] = rows["pretax"][i] - rows["tax"][i]

    # Balance sheet: condensed lines plus "other" plugs that make it tie to reported totals.
    for k in ("cash", "st_investments", "ar", "inventory", "ppe", "ap", "st_debt", "lt_debt"):
        rows[k] = [z(x) for x in rows[k]]
    ta = rows["total_assets"]
    le = rows["liabilities_and_equity"]
    ta = [a if a is not None else l for a, l in zip(ta, le)]
    eq = rows["equity"]
    tl = rows["total_liabilities"]
    tl = [t if t is not None else (a - e if a is not None and e is not None else None)
          for t, a, e in zip(tl, ta, eq)]
    eq = [a - t if a is not None and t is not None else e for a, t, e in zip(ta, tl, eq)]
    tca = rows["total_current_assets"]
    tcl = rows["total_current_liabilities"]
    rows["total_assets"] = ta
    rows["equity"] = eq
    rows["debt"] = [s + l for s, l in zip(rows["st_debt"], rows["lt_debt"])]
    rows["other_ca"] = [(c if c is not None else a - p) - cs - si - r - iv
                        for c, a, p, cs, si, r, iv in zip(tca, ta, rows["ppe"], rows["cash"],
                                                          rows["st_investments"], rows["ar"],
                                                          rows["inventory"])]
    rows["other_nca"] = [a - cs - si - r - iv - oca - p
                         for a, cs, si, r, iv, oca, p in zip(ta, rows["cash"], rows["st_investments"],
                                                             rows["ar"], rows["inventory"],
                                                             rows["other_ca"], rows["ppe"])]
    rows["other_cl"] = [(c if c is not None else 0.0) - ap - sd if c is not None else 0.0
                        for c, ap, sd in zip(tcl, rows["ap"], rows["st_debt"])]
    rows["other_ncl"] = [a - e - ap - ocl - d
                         for a, e, ap, ocl, d in zip(ta, eq, rows["ap"], rows["other_cl"], rows["debt"])]
    for k in ("cfo", "cfi", "cff"):
        rows[k] = [z(x) for x in rows[k]]
    return rows


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def ratio(num, den, k=3):
    pairs = [(a, b) for a, b in zip(num[-k:], den[-k:]) if a is not None and b]
    return avg([a / b for a, b in pairs])



# --------------------------------------------------------------------------- assumptions

SCENARIOS = ("bear", "base", "bull")
SCEN_LABEL = {"bear": "Bear", "base": "Base", "bull": "Bull"}
# Drivers that differ by scenario (five projection years each).
SCEN_DRIVERS = [
    ("revenue_growth", "Revenue growth"),
    ("gross_margin", "Gross margin"),
    ("rnd_pct_revenue", "R&D % of revenue"),
    ("sga_pct_revenue", "SG&A % of revenue"),
    ("capex_pct_revenue", "Capex % of revenue"),
]
# Drivers shared by every scenario.
FLAT_DRIVERS = [
    ("other_opex_pct_revenue", "Other operating expense % of revenue", "pct"),
    ("da_pct_revenue", "D&A % of revenue", "pct"),
    ("sbc_pct_revenue", "Stock comp % of revenue", "pct"),
    ("tax_rate", "Tax rate", "pct"),
    ("dso_days", "DSO (days of revenue)", "days"),
    ("dio_days", "DIO (days of COGS)", "days"),
    ("dpo_days", "DPO (days of COGS)", "days"),
    ("other_ca_pct_revenue", "Other current assets % of revenue", "pct"),
    ("other_cl_pct_revenue", "Other current liabilities % of revenue", "pct"),
    ("interest_rate_on_debt", "Interest rate on debt (opening balance)", "pct"),
    ("interest_rate_on_cash", "Interest rate on cash & investments", "pct"),
    ("net_debt_issuance_musd", "Net debt issuance / (repayment), $m", "num"),
    ("dividend_payout_pct_net_income", "Dividend payout % of net income", "pct"),
    ("buybacks_musd", "Share repurchases, $m", "num"),
    ("gross_dilution_pct", "Gross share issuance from stock comp, % of shares", "pct"),
]


def default_assumptions(hs, h):
    rev = hs["revenue"]
    g = [(b / a - 1) for a, b in zip(rev[:-1], rev[1:]) if a]
    g_recent = avg(g[-3:])
    fade = lambda start, end: [round(start + (end - start) * i / (N_PROJ - 1), 4) for i in range(N_PROJ)]
    flat = lambda x: [round(x, 4)] * N_PROJ
    shift = lambda xs, d: [round(x + d, 4) for x in xs]
    tax_rate = min(max(ratio(hs["tax"], hs["pretax"]), 0.12), 0.30)
    gm = 1 - ratio(hs["cogs"], rev)
    base = {
        "probability": 0.5, "terminal_growth": 0.025, "exit_ev_ebitda": 12.0,
        "narrative": "REPLACE: what has to be true for this case, in one sentence with numbers.",
        "revenue_growth": fade(g_recent, 0.03),
        "gross_margin": flat(gm),
        "rnd_pct_revenue": flat(ratio(hs["rnd"], rev)),
        "sga_pct_revenue": flat(ratio(hs["sga"], rev)),
        "capex_pct_revenue": flat(ratio(hs["capex"], rev)),
    }
    bear = dict(base, probability=0.25, terminal_growth=0.02, exit_ev_ebitda=10.0,
                revenue_growth=fade(g_recent - 0.04, 0.02), gross_margin=shift(base["gross_margin"], -0.015),
                sga_pct_revenue=shift(base["sga_pct_revenue"], 0.005))
    bull = dict(base, probability=0.25, terminal_growth=0.03, exit_ev_ebitda=14.0,
                revenue_growth=fade(g_recent + 0.03, 0.04), gross_margin=shift(base["gross_margin"], 0.01))
    sbc_share = ratio(hs["sbc"], rev)
    return {
        "_note": ("Defaults: 3-year historical averages; base growth fades to 3%; bear/bull are mechanical "
                  "offsets. Replace every scenario with your own evidence-backed view."),
        "scenarios": {"bear": bear, "base": base, "bull": bull},
        "other_opex_pct_revenue": flat(ratio(
            [r - c - rd - s - e for r, c, rd, s, e in zip(rev, hs["cogs"], hs["rnd"], hs["sga"], hs["ebit"])], rev)),
        "da_pct_revenue": flat(ratio(hs["da"], rev)),
        "sbc_pct_revenue": flat(sbc_share),
        "tax_rate": flat(tax_rate),
        "dso_days": flat(365 * ratio(hs["ar"], rev, 1)),
        "dio_days": flat(365 * ratio(hs["inventory"], hs["cogs"], 1)),
        "dpo_days": flat(365 * ratio(hs["ap"], hs["cogs"], 1)),
        "other_ca_pct_revenue": flat(ratio(hs["other_ca"], rev, 1)),
        "other_cl_pct_revenue": flat(ratio(hs["other_cl"], rev, 1)),
        "interest_rate_on_debt": flat(min(ratio(hs["interest_expense"], hs["debt"], 1) or 0.05, 0.12)),
        "interest_rate_on_cash": flat(0.035),
        "net_debt_issuance_musd": flat(0.0),
        "dividend_payout_pct_net_income": flat(ratio(hs["dividends"], hs["net_income"], 1)),
        "buybacks_musd": flat(avg(hs["buybacks"][-3:])),
        "gross_dilution_pct": flat(0.01 if sbc_share > 0.03 else 0.005),
        "share_price": None,
        "share_price_as_of": None,
        "diluted_shares_m": round((h.get("shares_outstanding_latest") or 0) / M, 2) or hs["diluted_shares"][-1],
        "risk_free_rate": 0.042,
        "equity_risk_premium": 0.05,
        "beta": 1.0,
        "pretax_cost_of_debt": 0.055,
        "mid_year_convention": 1,
    }


def validate_assumptions(a):
    errs = []
    sc = a.get("scenarios", {})
    for s in SCENARIOS:
        if s not in sc:
            errs.append(f"scenarios.{s} missing")
            continue
        for k, _ in SCEN_DRIVERS:
            if len(sc[s].get(k) or []) != N_PROJ:
                errs.append(f"scenarios.{s}.{k} needs {N_PROJ} values")
        for k in ("probability", "terminal_growth", "exit_ev_ebitda"):
            if not isinstance(sc[s].get(k), (int, float)):
                errs.append(f"scenarios.{s}.{k} must be a number")
    if not errs and abs(sum(sc[s]["probability"] for s in SCENARIOS) - 1) > 1e-6:
        errs.append("scenario probabilities must sum to 1")
    for k, *_ in FLAT_DRIVERS:
        if len(a.get(k) or []) != N_PROJ:
            errs.append(f"{k} needs {N_PROJ} values")
    if errs:
        sys.exit("assumptions.json: " + "; ".join(errs))


# --------------------------------------------------------------------------- workbook styles

FONT = "Calibri"
INK, BLUE_C, GREEN_C, MUTED = "1B2433", "1F4FB4", "1E7B34", "6B7385"
NAVY = "1B2A4A"
F_INPUT = Font(name=FONT, size=10, color=BLUE_C)
F_CALC = Font(name=FONT, size=10, color=INK)
F_LINK = Font(name=FONT, size=10, color=GREEN_C)
F_BOLD = Font(name=FONT, size=10, bold=True, color=INK)
F_TITLE = Font(name=FONT, size=15, bold=True, color=NAVY)
F_SUB = Font(name=FONT, size=9, italic=True, color=MUTED)
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_SECTION = Font(name=FONT, size=10, bold=True, color=NAVY)
FILL_HEAD = PatternFill("solid", fgColor=NAVY)
FILL_PROJ = PatternFill("solid", fgColor="F3F5F9")
FILL_INPUT = PatternFill("solid", fgColor="FFF6D5")
FILL_SECTION = PatternFill("solid", fgColor="E6EAF2")
FILL_OK = PatternFill("solid", fgColor="E3F1E7")
TOP = Border(top=Side(style="thin", color="9AA3B5"))
DOUBLE = Border(top=Side(style="thin", color="9AA3B5"), bottom=Side(style="double", color="9AA3B5"))
NUM = '#,##0;(#,##0);"–"'
NUM1 = '#,##0.0;(#,##0.0);"–"'
PCT = '0.0%;(0.0%);"–"'
DAYS = '0.0'
USD = '$#,##0.00;($#,##0.00);"–"'
MULT = '0.0"x";(0.0"x");"–"'


def font_like(f, bold):
    return Font(name=FONT, size=10, bold=bold, color=f.color.rgb if f.color else INK)


class Sheet:
    """A statement-style sheet: label in A, units in B, one column per fiscal year from C."""

    def __init__(self, wb, title, years, n_hist, subtitle="$ millions except per-share data"):
        self.ws = wb.create_sheet(title)
        self.title = title
        self.years, self.n_hist = years, n_hist
        self.row, self.r = {}, 5
        ws = self.ws
        ws.sheet_view.showGridLines = False
        ws.column_dimensions["A"].width = 44
        ws.column_dimensions["B"].width = 6
        ws.cell(1, 1, title).font = F_TITLE
        ws.cell(2, 1, subtitle).font = F_SUB
        for c in range(1, 3 + len(years)):
            ws.cell(4, c).fill = FILL_HEAD
        ws.cell(4, 1, "Fiscal year").font = F_HEAD
        for i, y in enumerate(years):
            c = ws.cell(4, 3 + i, y)
            c.font, c.alignment = F_HEAD, Alignment(horizontal="right")
            ws.column_dimensions[get_column_letter(3 + i)].width = 11.5
            if i == n_hist:
                c.border = Border(left=Side(style="medium", color="FFFFFF"))
        ws.cell(3, 3, "Actual").font = F_SUB
        ws.cell(3, 3 + n_hist, "Projected →").font = F_SUB
        ws.freeze_panes = "C5"

    def col(self, i):
        return get_column_letter(3 + i)

    def section(self, text):
        self.r += 1
        for c in range(1, 3 + len(self.years)):
            self.ws.cell(self.r, c).fill = FILL_SECTION
        self.ws.cell(self.r, 1, text).font = F_SECTION
        self.r += 1

    def line(self, key, label, hist=None, proj=None, fmt=NUM, bold=False, total=None, indent=0):
        r = self.r
        self.row[key] = r
        ws = self.ws
        c0 = ws.cell(r, 1, label)
        c0.font = F_BOLD if bold else F_CALC
        c0.alignment = Alignment(indent=indent)
        for i in range(len(self.years)):
            is_hist = i < self.n_hist
            src = hist if is_hist else proj
            if src is None:
                continue
            val = src(i) if callable(src) else src[i]
            if val is None:
                continue
            c = ws.cell(r, 3 + i, val)
            c.number_format = fmt
            formula = isinstance(val, str) and val.startswith("=")
            f = (F_LINK if "!" in val else F_CALC) if formula else F_INPUT
            c.font = font_like(f, bold)
            if not is_hist:
                c.fill = FILL_PROJ
            if total:
                c.border = DOUBLE if total == "double" else TOP
        self.r += 1
        return r

    def note(self, text):
        self.r += 1
        self.ws.cell(self.r, 1, text).font = F_SUB
        self.r += 1


def resolve(sheets):
    """Replace {key} placeholders with row numbers once every row is known."""
    keys = {}
    for S in sheets:
        for k, v in S.row.items():
            if k in keys:
                raise ValueError(f"duplicate row key {k}")
            keys[k] = v
    for S in sheets:
        for row in S.ws.iter_rows(min_row=5):
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("=") and "{" in c.value:
                    v = c.value
                    for k, n in keys.items():
                        v = v.replace("{" + k + "}", str(n))
                    if "{" in v:
                        raise ValueError(f"unresolved placeholder in {S.title}!{c.coordinate}: {v}")
                    c.value = v
                    if "!" in v:
                        c.font = font_like(F_LINK, bool(c.font.bold))


# --------------------------------------------------------------------------- workbook

def build_workbook(h, hs, a, path, active=2):
    n = len(h["fiscal_year_ends"])
    # A fiscal year ending in the first half of January (52/53-week years) belongs to the prior year.
    fy = [int(e[:4]) - (1 if e[5:7] == "01" and int(e[8:10]) <= 15 else 0) for e in h["fiscal_year_ends"]]
    years = [f"FY{y}A" for y in fy] + [f"FY{fy[-1] + k}E" for k in range(1, N_PROJ + 1)]
    H, P = range(n), range(n, n + N_PROJ)
    last_h = n - 1
    sc = a["scenarios"]

    wb = Workbook()
    wb.remove(wb.active)
    cover = wb.create_sheet("Cover")
    SC = wb.create_sheet("Scenarios")
    A = Sheet(wb, "Assumptions", years, n, "Yellow cells are inputs. The live row in each block follows the "
                                           "scenario selected on the Scenarios sheet.")
    IS = Sheet(wb, "Income Statement", years, n)
    BS = Sheet(wb, "Balance Sheet", years, n)
    CF = Sheet(wb, "Cash Flow", years, n)
    RT = Sheet(wb, "Ratios", years, n, "Returns, cash conversion, leverage and efficiency")
    D = wb.create_sheet("DCF")
    DATA = wb.create_sheet("Data")
    col = IS.col
    SEL = "Scenarios!$C$5"

    # ---- Assumptions
    for k, label in SCEN_DRIVERS:
        A.section(label)
        live = A.r
        A.line(k, "Live (selected scenario)", None,
               lambda i, live=live: f"=CHOOSE({SEL},{col(i)}{live + 1},{col(i)}{live + 2},{col(i)}{live + 3})",
               PCT, bold=True)
        for s in SCENARIOS:
            A.line(f"{k}_{s}", SCEN_LABEL[s], None, lambda i, s=s, k=k: sc[s][k][i - n], PCT, indent=1)
            for i in P:
                A.ws.cell(A.row[f"{k}_{s}"], 3 + i).fill = FILL_INPUT
        # Realized history for context.
        hist_formula = {
            "revenue_growth": lambda i: None if i == 0 else f"='Income Statement'!{col(i)}{{growth}}",
            "gross_margin": lambda i: f"='Income Statement'!{col(i)}{{gm}}",
            "rnd_pct_revenue": lambda i: f"='Income Statement'!{col(i)}{{rnd}}/'Income Statement'!{col(i)}{{revenue}}",
            "sga_pct_revenue": lambda i: f"='Income Statement'!{col(i)}{{sga}}/'Income Statement'!{col(i)}{{revenue}}",
            "capex_pct_revenue": lambda i: f"='Cash Flow'!{col(i)}{{cf_capex_pos}}/'Income Statement'!{col(i)}{{revenue}}",
        }[k]
        for i in H:
            v = hist_formula(i)
            if v:
                c = A.ws.cell(live, 3 + i, v)
                c.number_format, c.font = PCT, F_LINK
    A.section("Shared drivers (all scenarios)")
    fmts = {"pct": PCT, "days": DAYS, "num": NUM}
    for k, label, kind in FLAT_DRIVERS:
        A.line(k, label, None, lambda i, k=k: a[k][i - n], fmts[kind])
        for i in P:
            A.ws.cell(A.row[k], 3 + i).fill = FILL_INPUT
    ar = lambda k, i: f"Assumptions!{col(i)}{A.row[k]}"

    # ---- Income statement
    L = lambda f: (lambda i: f.format(c=col(i), p=col(i - 1)))
    IS.section("Income statement")
    IS.line("revenue", "Revenue", [hs["revenue"][i] for i in H],
            lambda i: f"={col(i-1)}{{revenue}}*(1+{ar('revenue_growth', i)})", bold=True)
    IS.line("growth", "Growth", lambda i: None if i == 0 else f"={col(i)}{{revenue}}/{col(i-1)}{{revenue}}-1",
            L("={c}{{revenue}}/{p}{{revenue}}-1"), PCT, indent=1)
    IS.line("cogs", "Cost of revenue", [hs["cogs"][i] for i in H],
            lambda i: f"={col(i)}{{revenue}}*(1-{ar('gross_margin', i)})")
    IS.line("gross_profit", "Gross profit", L("={c}{{revenue}}-{c}{{cogs}}"), L("={c}{{revenue}}-{c}{{cogs}}"),
            bold=True, total="top")
    IS.line("gm", "Gross margin", L("={c}{{gross_profit}}/{c}{{revenue}}"), L("={c}{{gross_profit}}/{c}{{revenue}}"),
            PCT, indent=1)
    IS.line("rnd", "Research & development", [hs["rnd"][i] for i in H],
            lambda i: f"={col(i)}{{revenue}}*{ar('rnd_pct_revenue', i)}")
    IS.line("sga", "Selling, general & administrative", [hs["sga"][i] for i in H],
            lambda i: f"={col(i)}{{revenue}}*{ar('sga_pct_revenue', i)}")
    IS.line("other_opex", "Other operating expense, net",
            [hs["revenue"][i] - hs["cogs"][i] - hs["rnd"][i] - hs["sga"][i] - hs["ebit"][i] for i in H],
            lambda i: f"={col(i)}{{revenue}}*{ar('other_opex_pct_revenue', i)}")
    ebit = L("={c}{{gross_profit}}-{c}{{rnd}}-{c}{{sga}}-{c}{{other_opex}}")
    IS.line("ebit", "Operating income (EBIT)", ebit, ebit, bold=True, total="top")
    IS.line("ebit_margin", "Operating margin", L("={c}{{ebit}}/{c}{{revenue}}"), L("={c}{{ebit}}/{c}{{revenue}}"),
            PCT, indent=1)
    IS.line("interest_expense", "Interest expense", [hs["interest_expense"][i] for i in H],
            lambda i: f"='Balance Sheet'!{col(i-1)}{{bs_debt}}*{ar('interest_rate_on_debt', i)}")
    IS.line("other_income", "Interest income & other, net",
            [hs["pretax"][i] - hs["ebit"][i] + hs["interest_expense"][i] for i in H],
            lambda i: f"=('Balance Sheet'!{col(i-1)}{{bs_cash}}+'Balance Sheet'!{col(i-1)}{{bs_sti}})"
                      f"*{ar('interest_rate_on_cash', i)}")
    pt = L("={c}{{ebit}}-{c}{{interest_expense}}+{c}{{other_income}}")
    IS.line("pretax", "Pre-tax income", pt, pt, total="top")
    IS.line("tax", "Income tax", [hs["tax"][i] for i in H],
            lambda i: f"=MAX(0,{col(i)}{{pretax}}*{ar('tax_rate', i)})")
    IS.line("etr", "Effective tax rate", L("=IF({c}{{pretax}}=0,0,{c}{{tax}}/{c}{{pretax}})"),
            L("=IF({c}{{pretax}}=0,0,{c}{{tax}}/{c}{{pretax}})"), PCT, indent=1)
    IS.line("ni_other", "Minority interest, discontinued ops & other",
            [hs["net_income"][i] - (hs["pretax"][i] - hs["tax"][i]) for i in H], lambda i: 0)
    ni = L("={c}{{pretax}}-{c}{{tax}}+{c}{{ni_other}}")
    IS.line("net_income", "Net income", ni, ni, bold=True, total="double")
    IS.line("ni_margin", "Net margin", L("={c}{{net_income}}/{c}{{revenue}}"), L("={c}{{net_income}}/{c}{{revenue}}"),
            PCT, indent=1)

    IS.section("Share count (millions)")
    IS.line("sh_open", "Opening diluted shares", None,
            lambda i: "=DCF!$C$7" if i == n else f"={col(i-1)}{{sh_close}}", NUM1)
    IS.line("sh_issued", "Plus: issued for stock comp", None,
            lambda i: f"={col(i)}{{sh_open}}*{ar('gross_dilution_pct', i)}", NUM1, indent=1)
    IS.line("sh_price", "Assumed repurchase price ($)", None,
            lambda i: f"=DCF!$C$5*(1+DCF!$C$19)^({i - n + 1}-0.5)", USD, indent=1)
    IS.line("sh_bought", "Less: shares repurchased", None,
            lambda i: f"=IF({col(i)}{{sh_price}}=0,0,'Cash Flow'!{col(i)}{{cf_bb_pos}}/{col(i)}{{sh_price}})",
            NUM1, indent=1)
    IS.line("sh_close", "Closing diluted shares", None,
            L("={c}{{sh_open}}+{c}{{sh_issued}}-{c}{{sh_bought}}"), NUM1, total="top")
    IS.line("shares", "Average diluted shares (for EPS)", [hs["diluted_shares"][i] for i in H],
            L("=AVERAGE({c}{{sh_open}},{c}{{sh_close}})"), NUM1, bold=True)
    IS.line("eps", "Diluted EPS ($)", L("=IF({c}{{shares}}=0,0,{c}{{net_income}}/{c}{{shares}})"),
            L("=IF({c}{{shares}}=0,0,{c}{{net_income}}/{c}{{shares}})"), USD, bold=True)
    IS.line("eps_growth", "EPS growth", lambda i: None if i == 0 else f"=IF({col(i-1)}{{eps}}<=0,0,{col(i)}{{eps}}/{col(i-1)}{{eps}}-1)",
            L("=IF({p}{{eps}}<=0,0,{c}{{eps}}/{p}{{eps}}-1)"), PCT, indent=1)

    IS.section("Memo")
    IS.line("da", "Depreciation & amortization", [hs["da"][i] for i in H],
            lambda i: f"={col(i)}{{revenue}}*{ar('da_pct_revenue', i)}")
    IS.line("ebitda", "EBITDA", L("={c}{{ebit}}+{c}{{da}}"), L("={c}{{ebit}}+{c}{{da}}"), bold=True)
    IS.line("ebitda_margin", "EBITDA margin", L("={c}{{ebitda}}/{c}{{revenue}}"), L("={c}{{ebitda}}/{c}{{revenue}}"),
            PCT, indent=1)
    IS.line("sbc", "Stock-based compensation", [hs["sbc"][i] for i in H],
            lambda i: f"={col(i)}{{revenue}}*{ar('sbc_pct_revenue', i)}")

    # ---- Balance sheet
    ISX = lambda k, i: f"'Income Statement'!{col(i)}{{{k}}}"
    BS.section("Assets")
    BS.line("bs_cash", "Cash & equivalents", [hs["cash"][i] for i in H], lambda i: f"='Cash Flow'!{col(i)}{{cf_end_cash}}")
    BS.line("bs_sti", "Short-term investments", [hs["st_investments"][i] for i in H], L("={p}{{bs_sti}}"))
    BS.line("bs_ar", "Accounts receivable", [hs["ar"][i] for i in H],
            lambda i: f"={ISX('revenue', i)}*{ar('dso_days', i)}/365")
    BS.line("bs_inv", "Inventory", [hs["inventory"][i] for i in H],
            lambda i: f"={ISX('cogs', i)}*{ar('dio_days', i)}/365")
    BS.line("bs_oca", "Other current assets", [hs["other_ca"][i] for i in H],
            lambda i: f"={ISX('revenue', i)}*{ar('other_ca_pct_revenue', i)}")
    tca = L("=SUM({c}{{bs_cash}}:{c}{{bs_oca}})")
    BS.line("bs_tca", "Total current assets", tca, tca, bold=True, total="top")
    BS.line("bs_ppe", "Property, plant & equipment, net", [hs["ppe"][i] for i in H],
            lambda i: f"={col(i-1)}{{bs_ppe}}+'Cash Flow'!{col(i)}{{cf_capex_pos}}-{ISX('da', i)}")
    BS.line("bs_onca", "Goodwill, intangibles & other non-current", [hs["other_nca"][i] for i in H], L("={p}{{bs_onca}}"))
    ta = L("={c}{{bs_tca}}+{c}{{bs_ppe}}+{c}{{bs_onca}}")
    BS.line("bs_ta", "Total assets", ta, ta, bold=True, total="double")
    BS.section("Liabilities & equity")
    BS.line("bs_ap", "Accounts payable", [hs["ap"][i] for i in H],
            lambda i: f"={ISX('cogs', i)}*{ar('dpo_days', i)}/365")
    BS.line("bs_ocl", "Other current liabilities", [hs["other_cl"][i] for i in H],
            lambda i: f"={ISX('revenue', i)}*{ar('other_cl_pct_revenue', i)}")
    BS.line("bs_debt", "Total debt (see debt schedule)", [hs["debt"][i] for i in H], L("={c}{{ds_close}}"))
    BS.line("bs_oncl", "Other non-current liabilities", [hs["other_ncl"][i] for i in H], L("={p}{{bs_oncl}}"))
    tl = L("=SUM({c}{{bs_ap}}:{c}{{bs_oncl}})")
    BS.line("bs_tl", "Total liabilities", tl, tl, bold=True, total="top")
    BS.line("bs_eq", "Total equity", [hs["equity"][i] for i in H],
            lambda i: f"={col(i-1)}{{bs_eq}}+{ISX('net_income', i)}+{ISX('sbc', i)}"
                      f"-'Cash Flow'!{col(i)}{{cf_div_pos}}-'Cash Flow'!{col(i)}{{cf_bb_pos}}")
    tle = L("={c}{{bs_tl}}+{c}{{bs_eq}}")
    BS.line("bs_tle", "Total liabilities & equity", tle, tle, bold=True, total="double")
    BS.section("Checks")
    chk = L("=ROUND({c}{{bs_ta}}-{c}{{bs_tle}},3)")
    BS.line("bs_check", "Assets less liabilities & equity (must be 0)", chk, chk, NUM1)
    BS.line("bs_cash_ok", "Cash stays positive (1 = yes)", None, L("=IF({c}{{bs_cash}}>=0,1,0)"), '0')
    BS.line("bs_reported_ta", "Reported total assets (10-K)", [hs["total_assets"][i] for i in H])
    BS.section("Debt schedule")
    BS.line("ds_open", "Opening debt", None, L("={p}{{bs_debt}}"))
    BS.line("ds_issue", "Issuance / (repayment)", None, lambda i: f"={ar('net_debt_issuance_musd', i)}", indent=1)
    BS.line("ds_close", "Closing debt", None, L("={c}{{ds_open}}+{c}{{ds_issue}}"), total="top")
    BS.line("ds_rate", "Interest rate on opening balance", None, lambda i: f"={ar('interest_rate_on_debt', i)}", PCT, indent=1)
    BS.section("Memo")
    nd = L("={c}{{bs_debt}}-{c}{{bs_cash}}-{c}{{bs_sti}}")
    BS.line("bs_netdebt", "Net debt / (net cash)", nd, nd, bold=True)
    nwc = L("={c}{{bs_ar}}+{c}{{bs_inv}}+{c}{{bs_oca}}-{c}{{bs_ap}}-{c}{{bs_ocl}}")
    BS.line("bs_nwc", "Net working capital (ex cash & debt)", nwc, nwc)
    ic = L("={c}{{bs_eq}}+{c}{{bs_debt}}-{c}{{bs_cash}}-{c}{{bs_sti}}")
    BS.line("bs_ic", "Invested capital (equity + net debt)", ic, ic)

    # ---- Cash flow
    ISR = lambda k: (lambda i: f"={ISX(k, i)}")
    da_ = lambda k: (lambda i: None if i == 0 else f"='Balance Sheet'!{col(i-1)}{{{k}}}-'Balance Sheet'!{col(i)}{{{k}}}")
    dl_ = lambda k: (lambda i: None if i == 0 else f"='Balance Sheet'!{col(i)}{{{k}}}-'Balance Sheet'!{col(i-1)}{{{k}}}")
    CF.section("Operating activities")
    CF.line("cf_ni", "Net income", ISR("net_income"), ISR("net_income"))
    CF.line("cf_da", "Depreciation & amortization", ISR("da"), ISR("da"))
    CF.line("cf_sbc", "Stock-based compensation", ISR("sbc"), ISR("sbc"))
    CF.line("cf_ar", "(Increase) / decrease in receivables", da_("bs_ar"), da_("bs_ar"), indent=1)
    CF.line("cf_inv", "(Increase) / decrease in inventory", da_("bs_inv"), da_("bs_inv"), indent=1)
    CF.line("cf_oca", "(Increase) / decrease in other current assets", da_("bs_oca"), da_("bs_oca"), indent=1)
    CF.line("cf_ap", "Increase / (decrease) in payables", dl_("bs_ap"), dl_("bs_ap"), indent=1)
    CF.line("cf_ocl", "Increase / (decrease) in other current liabilities", dl_("bs_ocl"), dl_("bs_ocl"), indent=1)
    CF.line("cf_other_op", "Other operating items (reported less above)",
            lambda i: f"={hs['cfo'][i]}-SUM({col(i)}{{cf_ni}}:{col(i)}{{cf_ocl}})", lambda i: 0)
    cfo = L("=SUM({c}{{cf_ni}}:{c}{{cf_other_op}})")
    CF.line("cf_cfo", "Cash from operations", cfo, cfo, bold=True, total="top")
    CF.section("Investing activities")
    CF.line("cf_capex", "Capital expenditures", [-hs["capex"][i] for i in H], L("=-{c}{{cf_capex_pos}}"))
    CF.line("cf_other_inv", "Acquisitions, investments & other", [hs["cfi"][i] + hs["capex"][i] for i in H], lambda i: 0)
    cfi = L("={c}{{cf_capex}}+{c}{{cf_other_inv}}")
    CF.line("cf_cfi", "Cash from investing", cfi, cfi, bold=True, total="top")
    CF.section("Financing activities")
    CF.line("cf_debt", "Net debt issuance / (repayment)", None, lambda i: f"='Balance Sheet'!{col(i)}{{ds_issue}}")
    CF.line("cf_div", "Dividends paid", [-hs["dividends"][i] for i in H], L("=-{c}{{cf_div_pos}}"))
    CF.line("cf_bb", "Share repurchases", [-hs["buybacks"][i] for i in H], L("=-{c}{{cf_bb_pos}}"))
    CF.line("cf_other_fin", "Debt & other financing (reported less above)",
            [hs["cff"][i] + hs["dividends"][i] + hs["buybacks"][i] for i in H], lambda i: 0)
    cff = L("=SUM({c}{{cf_debt}}:{c}{{cf_other_fin}})")
    CF.line("cf_cff", "Cash from financing", cff, cff, bold=True, total="top")
    CF.r += 1
    net = L("={c}{{cf_cfo}}+{c}{{cf_cfi}}+{c}{{cf_cff}}")
    CF.line("cf_net", "Net change in cash", net, net, bold=True)
    CF.line("cf_beg_cash", "Beginning cash", None, lambda i: f"='Balance Sheet'!{col(i-1)}{{bs_cash}}")
    CF.line("cf_end_cash", "Ending cash", None, L("={c}{{cf_beg_cash}}+{c}{{cf_net}}"), bold=True, total="double")
    CF.section("Memo")
    fcf = L("={c}{{cf_cfo}}+{c}{{cf_capex}}")
    CF.line("cf_fcf", "Free cash flow (CFO less capex)", fcf, fcf, bold=True)
    CF.line("cf_capex_pos", "Capex", [hs["capex"][i] for i in H],
            lambda i: f"={ISX('revenue', i)}*{ar('capex_pct_revenue', i)}")
    CF.line("cf_div_pos", "Dividends", [hs["dividends"][i] for i in H],
            lambda i: f"=MAX(0,{ISX('net_income', i)}*{ar('dividend_payout_pct_net_income', i)})")
    CF.line("cf_bb_pos", "Buybacks", [hs["buybacks"][i] for i in H], lambda i: f"={ar('buybacks_musd', i)}")

    # ---- Ratios
    both = lambda f: (L(f), L(f))
    ISc = lambda k: "'Income Statement'!{c}{{" + k + "}}"
    BSc = lambda k: "'Balance Sheet'!{c}{{" + k + "}}"
    BSp = lambda k: "'Balance Sheet'!{p}{{" + k + "}}"
    CFc = lambda k: "'Cash Flow'!{c}{{" + k + "}}"
    RT.section("Returns")
    def roic_h(i):
        ic = (f"'Balance Sheet'!{col(i)}{{bs_ic}}" if i == 0 else
              f"AVERAGE('Balance Sheet'!{col(i)}{{bs_ic}},'Balance Sheet'!{col(i-1)}{{bs_ic}})")
        return f"=IF({ic}<=0,0,{ISX('ebit', i)}*(1-{ISX('etr', i)})/{ic})"
    RT.line("r_roic", "Return on invested capital (after tax)", roic_h, roic_h, PCT, bold=True)
    RT.line("r_roe", "Return on equity", *both(f"=IF({BSc('bs_eq')}<=0,0,{ISc('net_income')}/{BSc('bs_eq')})"), fmt=PCT)
    RT.section("Cash generation")
    RT.line("r_fcf_margin", "Free cash flow margin", *both(f"={CFc('cf_fcf')}/{ISc('revenue')}"), fmt=PCT)
    RT.line("r_fcf_conv", "FCF conversion (FCF / net income)",
            *both(f"=IF({ISc('net_income')}<=0,0,{CFc('cf_fcf')}/{ISc('net_income')})"), fmt=PCT)
    RT.line("r_fcf_ps", "Free cash flow per share ($)", *both(f"=IF({ISc('shares')}=0,0,{CFc('cf_fcf')}/{ISc('shares')})"), fmt=USD)
    RT.line("r_capex_da", "Capex / D&A", *both(f"=IF({ISc('da')}=0,0,{CFc('cf_capex_pos')}/{ISc('da')})"), fmt=MULT)
    RT.line("r_sbc", "Stock comp % of revenue", *both(f"={ISc('sbc')}/{ISc('revenue')}"), fmt=PCT)
    RT.line("r_payout", "Shareholder returns % of FCF",
            *both(f"=IF({CFc('cf_fcf')}<=0,0,({CFc('cf_div_pos')}+{CFc('cf_bb_pos')})/{CFc('cf_fcf')})"), fmt=PCT)
    RT.section("Leverage")
    RT.line("r_nd_ebitda", "Net debt / EBITDA", *both(f"=IF({ISc('ebitda')}<=0,0,{BSc('bs_netdebt')}/{ISc('ebitda')})"), fmt=MULT)
    RT.line("r_cover", "EBITDA / interest expense",
            *both(f"=IF({ISc('interest_expense')}<=0,0,{ISc('ebitda')}/{ISc('interest_expense')})"), fmt=MULT)
    RT.section("Working capital")
    RT.line("r_dso", "Days sales outstanding", *both(f"={BSc('bs_ar')}/{ISc('revenue')}*365"), fmt=DAYS)
    RT.line("r_dio", "Days inventory", *both(f"=IF({ISc('cogs')}=0,0,{BSc('bs_inv')}/{ISc('cogs')}*365)"), fmt=DAYS)
    RT.line("r_dpo", "Days payables", *both(f"=IF({ISc('cogs')}=0,0,{BSc('bs_ap')}/{ISc('cogs')}*365)"), fmt=DAYS)
    RT.line("r_nwc_pct", "Net working capital % of revenue", *both(f"={BSc('bs_nwc')}/{ISc('revenue')}"), fmt=PCT)

    resolve([IS, BS, CF, RT, A])

    # ---- DCF
    D.sheet_view.showGridLines = False
    D.column_dimensions["A"].width = 40
    D.column_dimensions["B"].width = 13
    for j in range(3, 10):
        D.column_dimensions[get_column_letter(j)].width = 12.5
    D["A1"] = "Discounted cash flow"
    D["A1"].font = F_TITLE
    D["A2"] = "$ millions except per-share. Yellow = input; green = link to another sheet."
    D["A2"].font = F_SUB
    lc_is = col(n + N_PROJ - 1)

    def put(ref, v, fmt=None, font=None, fill=None):
        c = D[ref]
        c.value = v
        if fmt:
            c.number_format = fmt
        if font is None:
            font = (F_LINK if "!" in v else F_CALC) if isinstance(v, str) and v.startswith("=") else F_INPUT
        c.font = font
        if fill:
            c.fill = fill
        return c

    def head(r, text):
        for cc in range(1, 10):
            D.cell(r, cc).fill = FILL_SECTION
        D.cell(r, 1, text).font = F_SECTION

    head(4, "Market data & inputs")
    rows_in = [
        (5, "Share price ($)", a.get("share_price") or 0, USD, True),
        (6, "Price as of", a.get("share_price_as_of") or "", None, True),
        (7, "Diluted shares (m)", a["diluted_shares_m"], NUM1, True),
        (8, "Risk-free rate (10-year Treasury)", a["risk_free_rate"], PCT, True),
        (9, "Equity risk premium", a["equity_risk_premium"], PCT, True),
        (10, "Beta", a["beta"], '0.00', True),
        (11, "Pre-tax cost of debt", a["pretax_cost_of_debt"], PCT, True),
        (12, "Tax rate (first projection year)", f"=Assumptions!{col(n)}{A.row['tax_rate']}", PCT, False),
        (13, "Terminal growth (selected scenario)", "=Scenarios!$G$9", PCT, False),
        (14, "Exit EV / EBITDA (selected scenario)", "=Scenarios!$G$10", MULT, False),
        (15, "Mid-year convention (1 = on, 0 = off)", a.get("mid_year_convention", 1), '0', True),
    ]
    for r, label, v, fmt, is_input in rows_in:
        D.cell(r, 1, label).font = F_CALC
        put(f"C{r}", v, fmt, fill=FILL_INPUT if is_input else None)
    head(17, "Cost of capital")
    rows_w = [
        (18, "Market capitalization", "=C5*C7", NUM),
        (19, "Cost of equity (CAPM)", "=C8+C10*C9", PCT),
        (20, "After-tax cost of debt", "=C11*(1-C12)", PCT),
        (21, "Gross debt, latest actual", f"='Balance Sheet'!{col(last_h)}{BS.row['bs_debt']}", NUM),
        (22, "Net debt / (net cash), latest actual", f"='Balance Sheet'!{col(last_h)}{BS.row['bs_netdebt']}", NUM),
        (23, "Equity weight", "=IF(C18+C21=0,1,C18/(C18+C21))", PCT),
        (24, "WACC", "=C23*C19+(1-C23)*C20", PCT),
    ]
    for r, label, f, fmt in rows_w:
        D.cell(r, 1, label).font = F_BOLD if label == "WACC" else F_CALC
        put(f"C{r}", f, fmt, font=F_BOLD if label == "WACC" else None)

    head(26, "Unlevered free cash flow")
    for j in range(N_PROJ):
        c = D.cell(27, 3 + j, years[n + j])
        c.font, c.fill, c.alignment = F_HEAD, FILL_HEAD, Alignment(horizontal="right")
    for cc in (1, 2):
        D.cell(27, cc).fill = FILL_HEAD
    isr = lambda key, j: f"='Income Statement'!{col(n + j)}{IS.row[key]}"
    dc = lambda j: get_column_letter(3 + j)
    uf = [
        (28, "Revenue", lambda j: isr("revenue", j), NUM),
        (29, "EBITDA", lambda j: isr("ebitda", j), NUM),
        (30, "EBIT", lambda j: isr("ebit", j), NUM),
        (31, "Less: taxes on EBIT", lambda j: f"=-{dc(j)}30*Assumptions!{col(n+j)}{A.row['tax_rate']}", NUM),
        (32, "NOPAT", lambda j: f"={dc(j)}30+{dc(j)}31", NUM),
        (33, "Plus: D&A", lambda j: isr("da", j), NUM),
        (34, "Less: capex", lambda j: f"='Cash Flow'!{col(n+j)}{CF.row['cf_capex']}", NUM),
        (35, "Less: increase in net working capital",
         lambda j: f"='Balance Sheet'!{col(n+j-1)}{BS.row['bs_nwc']}-'Balance Sheet'!{col(n+j)}{BS.row['bs_nwc']}", NUM),
        (36, "Unlevered free cash flow", lambda j: f"=SUM({dc(j)}32:{dc(j)}35)", NUM),
        (37, "Discount period (years)", lambda j: f"={j + 1}-0.5*$C$15", '0.0'),
        (38, "Discount factor", lambda j: f"=1/(1+$C$24)^{dc(j)}37", '0.000'),
        (39, "Present value of UFCF", lambda j: f"={dc(j)}36*{dc(j)}38", NUM),
    ]
    for r, label, f, fmt in uf:
        bold = label in ("NOPAT", "Unlevered free cash flow", "Present value of UFCF")
        D.cell(r, 1, label).font = F_BOLD if bold else F_CALC
        for j in range(N_PROJ):
            c = put(f"{dc(j)}{r}", f(j), fmt)
            if bold:
                c.font = font_like(c.font, True)
            if label == "Unlevered free cash flow":
                c.border = TOP
    D.cell(40, 1, "Stock comp is treated as a real cost: it is not added back to free cash flow.").font = F_SUB
    L5 = dc(N_PROJ - 1)
    head(42, "Valuation")
    for cc, t in ((2, "Perpetuity"), (3, "Exit multiple")):
        c = D.cell(43, cc, t)
        c.font, c.fill, c.alignment = F_HEAD, FILL_HEAD, Alignment(horizontal="right")
    D.cell(43, 1).fill = FILL_HEAD
    val = [
        (44, "Sum of PV of UFCF", f"=SUM(C39:{L5}39)", f"=SUM(C39:{L5}39)", NUM),
        (45, "Terminal value (end of final year)", f"={L5}36*(1+C13)/(C24-C13)", f"={L5}29*C14", NUM),
        (46, "PV of terminal value", f"=B45/(1+C24)^{N_PROJ}", f"=C45/(1+C24)^{N_PROJ}", NUM),
        (47, "Enterprise value", "=B44+B46", "=C44+C46", NUM),
        (48, "Less: net debt / plus: net cash", "=-C22", "=-C22", NUM),
        (49, "Equity value", "=B47+B48", "=C47+C48", NUM),
        (50, "Value per share ($)", "=B49/$C$7", "=C49/$C$7", USD),
        (51, "Upside / (downside) to current price", "=IF($C$5=0,0,B50/$C$5-1)", "=IF($C$5=0,0,C50/$C$5-1)", PCT),
        (52, "Terminal value % of EV", "=B46/B47", "=C46/C47", PCT),
        (53, "Implied exit EV / EBITDA", f"=B45/{L5}29", f"=C45/{L5}29", MULT),
        (54, "Implied perpetuity growth", f"=(B45*C24-{L5}36)/(B45+{L5}36)", f"=(C45*C24-{L5}36)/(C45+{L5}36)", PCT),
    ]
    for r, label, b, c_, fmt in val:
        bold = r in (47, 50)
        D.cell(r, 1, label).font = F_BOLD if bold else F_CALC
        for ref, v in ((f"B{r}", b), (f"C{r}", c_)):
            cell = put(ref, v, fmt)
            if bold:
                cell.font = font_like(cell.font, True)
    D.cell(56, 1, "Value per share, 50/50 blend ($)").font = F_BOLD
    put("C56", "=AVERAGE(B50,C50)", USD, font=F_BOLD, fill=FILL_OK)
    D.cell(57, 1, "Upside / (downside), blend").font = F_BOLD
    put("C57", "=IF(C5=0,0,C56/C5-1)", PCT, font=F_BOLD)

    head(59, "Trading multiples at current price")
    fy1 = col(n)
    fy2 = col(n + 1)
    mults = [
        (60, "EV / revenue, FY+1", "=IF(C28<=0,0,(C18+C22)/C28)", MULT),
        (61, "EV / EBITDA, FY+1", "=IF(C29<=0,0,(C18+C22)/C29)", MULT),
        (62, "EV / EBITDA, latest actual", f"=IF('Income Statement'!{col(last_h)}{IS.row['ebitda']}<=0,0,(C18+C22)/'Income Statement'!{col(last_h)}{IS.row['ebitda']})", MULT),
        (63, "P / E, FY+1", f"=IF('Income Statement'!{fy1}{IS.row['eps']}<=0,0,C5/'Income Statement'!{fy1}{IS.row['eps']})", MULT),
        (64, "P / E, FY+2", f"=IF('Income Statement'!{fy2}{IS.row['eps']}<=0,0,C5/'Income Statement'!{fy2}{IS.row['eps']})", MULT),
        (65, "FCF yield, FY+1 (levered FCF / market cap)", f"='Cash Flow'!{fy1}{CF.row['cf_fcf']}/C18", PCT),
    ]
    for r, label, f, fmt in mults:
        D.cell(r, 1, label).font = F_CALC
        put(f"C{r}", f, fmt)

    grid_d = [-0.01, -0.005, 0, 0.005, 0.01]
    npv = f"NPV({{w}},$C$36:${L5}$36)*(1+{{w}})^(0.5*$C$15)"

    def grid(r0, title, col_label, col_vals_formula, fmt_cols, cell_formula):
        head(r0, title)
        D.cell(r0 + 1, 2, col_label).font = F_SUB
        for j, d in enumerate(grid_d if "growth" in col_label else [-2, -1, 0, 1, 2]):
            c = put(f"{dc(j)}{r0 + 1}", col_vals_formula(d), fmt_cols)
            c.font = F_BOLD
        for k, dw in enumerate(grid_d):
            r = r0 + 2 + k
            c = put(f"B{r}", f"=$C$24+({dw})", PCT)
            c.font = F_BOLD
            for j in range(5):
                w, x = f"$B{r}", f"{dc(j)}${r0 + 1}"
                c = put(f"{dc(j)}{r}", cell_formula(w, x), USD)
                if k == 2 and j == 2:
                    c.fill = FILL_OK
                    c.font = F_BOLD
        D.cell(r0 + 2, 1, "WACC ↓").font = F_SUB

    grid(67, "Sensitivity: value per share (perpetuity), WACC × terminal growth", "Terminal growth →",
         lambda d: f"=$C$13+({d})", PCT,
         lambda w, g: f"=({npv.format(w=w)}+${L5}$36*(1+{g})/({w}-{g})/(1+{w})^{N_PROJ}-$C$22)/$C$7")
    grid(75, "Sensitivity: value per share (exit multiple), WACC × exit EV/EBITDA", "Exit multiple →",
         lambda d: f"=$C$14+({d})", MULT,
         lambda w, m: f"=({npv.format(w=w)}+${L5}$29*{m}/(1+{w})^{N_PROJ}-$C$22)/$C$7")

    # ---- Scenarios sheet
    SC.sheet_view.showGridLines = False
    SC.column_dimensions["A"].width = 44
    for c_, w in zip("BCDEFGH", (4, 14, 14, 14, 16, 16, 4)):
        SC.column_dimensions[c_].width = w
    SC["A1"] = "Scenarios"
    SC["A1"].font = F_TITLE
    SC["A2"] = "Pick a scenario and every sheet (statements, ratios, DCF) recalculates for it."
    SC["A2"].font = F_SUB
    SC["A5"] = "Selected scenario (1 = Bear, 2 = Base, 3 = Bull)"
    SC["A5"].font = F_BOLD
    c = SC["C5"]
    c.value, c.font, c.fill, c.number_format = active, Font(name=FONT, size=12, bold=True, color=BLUE_C), FILL_INPUT, '0'
    SC["D5"] = '=CHOOSE(C5,"Bear","Base","Bull")'
    SC["D5"].font = F_BOLD

    def sc_head(r, labels):
        for cc in range(1, 8):
            SC.cell(r, cc).fill = FILL_HEAD
        for cc, t in labels:
            x = SC.cell(r, cc, t)
            x.font, x.alignment = F_HEAD, Alignment(horizontal="right")

    sc_head(7, [(1, "Scenario settings"), (3, "Bear"), (4, "Base"), (5, "Bull"), (7, "Selected")])
    SC.cell(7, 1).alignment = Alignment(horizontal="left")
    settings = [(8, "Probability", "probability", PCT), (9, "Terminal growth", "terminal_growth", PCT),
                (10, "Exit EV / EBITDA", "exit_ev_ebitda", MULT)]
    for r, label, k, fmt in settings:
        SC.cell(r, 1, label).font = F_CALC
        for j, s in enumerate(SCENARIOS):
            x = SC.cell(r, 3 + j, sc[s][k])
            x.number_format, x.font, x.fill = fmt, F_INPUT, FILL_INPUT
        x = SC.cell(r, 7, f"=CHOOSE($C$5,C{r},D{r},E{r})")
        x.number_format, x.font = fmt, F_BOLD
    SC.cell(11, 1, "Probabilities sum to").font = F_SUB
    x = SC.cell(11, 3, "=SUM(C8:E8)")
    x.number_format, x.font = PCT, F_SUB
    SC.cell(12, 1, "What has to be true").font = F_CALC
    SC.cell(12, 1).alignment = Alignment(vertical="top")
    for j, s in enumerate(SCENARIOS):
        x = SC.cell(12, 3 + j, sc[s].get("narrative", ""))
        x.font, x.alignment = F_INPUT, Alignment(wrap_text=True, vertical="top")
    SC.row_dimensions[12].height = 96

    OUT = [
        ("rev_last", f"Revenue, {years[-1]}", f"='Income Statement'!{lc_is}{IS.row['revenue']}", NUM),
        ("rev_cagr", f"Revenue CAGR, {years[last_h]}–{years[-1]}",
         f"=('Income Statement'!{lc_is}{IS.row['revenue']}/'Income Statement'!{col(last_h)}{IS.row['revenue']})^(1/{N_PROJ})-1", PCT),
        ("ebit_margin_last", f"Operating margin, {years[-1]}", f"='Income Statement'!{lc_is}{IS.row['ebit_margin']}", PCT),
        ("eps_fy1", f"Diluted EPS, {years[n]}", f"='Income Statement'!{col(n)}{IS.row['eps']}", USD),
        ("eps_last", f"Diluted EPS, {years[-1]}", f"='Income Statement'!{lc_is}{IS.row['eps']}", USD),
        ("fcf_cum", "Cumulative free cash flow, 5 years", f"=SUM('Cash Flow'!{col(n)}{CF.row['cf_fcf']}:{lc_is}{CF.row['cf_fcf']})", NUM),
        ("roic_last", f"ROIC, {years[-1]}", f"=Ratios!{lc_is}{RT.row['r_roic']}", PCT),
        ("v_perp", "Value per share, perpetuity ($)", "=DCF!B50", USD),
        ("v_exit", "Value per share, exit multiple ($)", "=DCF!C50", USD),
        ("v_blend", "Value per share, blend ($)", "=DCF!C56", USD),
        ("upside", "Upside / (downside) to current price", "=DCF!C57", PCT),
    ]
    sc_head(14, [(1, "Scenario outputs"), (3, "Bear"), (4, "Base"), (5, "Bull"), (6, "Prob.-weighted"), (7, "Live")])
    SC.cell(14, 1).alignment = Alignment(horizontal="left")
    out_row = {}
    for k, (key, label, f, fmt) in enumerate(OUT):
        r = 15 + k
        out_row[key] = r
        SC.cell(r, 1, label).font = F_BOLD if key in ("v_blend", "upside") else F_CALC
        for j in range(3):
            SC.cell(r, 3 + j).number_format = fmt
        if key == "upside":
            wf = "=IF(DCF!$C$5=0,0,F{}/DCF!$C$5-1)".format(out_row["v_blend"])
        else:
            wf = f"=SUMPRODUCT($C$8:$E$8,C{r}:E{r})"
        x = SC.cell(r, 6, wf)
        x.number_format, x.font = fmt, F_BOLD
        x = SC.cell(r, 7, f)
        x.number_format, x.font = fmt, F_LINK
    SC.cell(15 + len(OUT) + 1, 1,
            "Bear, Base and Bull columns are values captured when the model was built (each scenario calculated in turn). "
            "The Live column follows the selector above.").font = F_SUB
    SC.cell(15 + len(OUT) + 1, 1).alignment = Alignment(wrap_text=False)

    # ---- Data sheet (raw 10-K values and the XBRL tag behind each)
    DATA.sheet_view.showGridLines = False
    DATA.column_dimensions["A"].width = 28
    DATA.column_dimensions["B"].width = 58
    DATA["A1"] = "Source data"
    DATA["A1"].font = F_TITLE
    DATA["A2"] = f"As reported in 10-K XBRL, USD (shares in units). Source: {h['source']} · fetched {h['fetched']}"
    DATA["A2"].font = F_SUB
    for cc in range(1, 3 + n):
        DATA.cell(4, cc).fill = FILL_HEAD
    DATA.cell(4, 1, "Field").font = F_HEAD
    DATA.cell(4, 2, "XBRL tag (latest year)").font = F_HEAD
    for i, e in enumerate(h["fiscal_year_ends"]):
        x = DATA.cell(4, 3 + i, e)
        x.font, x.alignment = F_HEAD, Alignment(horizontal="right")
        DATA.column_dimensions[get_column_letter(3 + i)].width = 17
    for k, (field, vals) in enumerate(h["values"].items()):
        r = 5 + k
        DATA.cell(r, 1, field).font = F_CALC
        tags = h.get("xbrl_tags", {}).get(field) or []
        DATA.cell(r, 2, next((t for t in reversed(tags) if t), "—")).font = F_SUB
        for i, v in enumerate(vals):
            if v is not None:
                x = DATA.cell(r, 3 + i, v)
                x.number_format, x.font = '#,##0', F_INPUT

    # ---- Cover
    cover.sheet_view.showGridLines = False
    cover.column_dimensions["A"].width = 38
    cover.column_dimensions["B"].width = 70
    cover["A1"] = f"{h['name']} ({h['ticker']})"
    cover["A1"].font = Font(name=FONT, size=18, bold=True, color=NAVY)
    cover["A2"] = "Three-statement model · scenario analysis · DCF valuation"
    cover["A2"].font = F_SUB
    bs_chk = f"'Balance Sheet'!C{BS.row['bs_check']}:{lc_is}{BS.row['bs_check']}"
    cash_ok = f"'Balance Sheet'!{col(n)}{BS.row['bs_cash_ok']}:{lc_is}{BS.row['bs_cash_ok']}"
    items = [
        ("Selected scenario", "=Scenarios!D5", None),
        ("Share price", "=DCF!C5", USD),
        ("Price date", "=DCF!C6", None),
        ("Value per share, selected scenario (blend)", "=DCF!C56", USD),
        ("Value per share, probability-weighted", f"=Scenarios!F{out_row['v_blend']}", USD),
        ("Upside / (downside), probability-weighted", f"=Scenarios!F{out_row['upside']}", PCT),
        ("WACC", "=DCF!C24", PCT),
        ("", "", None),
        ("Balance sheet balances in every year", f'=IF(SUMPRODUCT(ABS({bs_chk}))<0.01,"Yes","NO — check the Balance Sheet")', None),
        ("Cash stays positive in every year", f'=IF(MIN({cash_ok})=1,"Yes","NO — reduce buybacks or add debt")', None),
        ("", "", None),
        ("Historical years", f"{years[0]} – {years[last_h]} (10-K XBRL)", None),
        ("Projection years", f"{years[n]} – {years[-1]}", None),
        ("Units", "$ millions except per-share", None),
        ("Source", h["source"], None),
        ("Colour code", "Blue = hard-coded input · Black = formula · Green = link to another sheet · Yellow fill = input cell", None),
        ("How to use", "Switch scenarios on the Scenarios sheet (cell C5). Edit yellow cells on Scenarios, Assumptions and DCF.", None),
        ("Method notes", "Condensed balance sheet with 'other' lines plugged to reported totals. Interest on opening balances "
                         "(no circularity). Buybacks retire shares at the current price grown at the cost of equity. "
                         "Stock comp treated as a cash cost in the DCF.", None),
    ]
    for k, (label, v, fmt) in enumerate(items):
        r = 4 + k
        cover.cell(r, 1, label).font = F_BOLD
        x = cover.cell(r, 2, v)
        x.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
        x.font = F_LINK if isinstance(v, str) and v.startswith("=") else F_CALC
        if fmt:
            x.number_format = fmt
    cover.row_dimensions[4 + len(items) - 1].height = 44

    for ws in wb.worksheets:
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_options.gridLines = False
        ws.oddFooter.center.text = f"{h['name']} ({h['ticker']}) · &A · page &P of &N"
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    return {"IS": IS.row, "BS": BS.row, "CF": CF.row, "RT": RT.row, "A": A.row, "n": n, "years": years,
            "out_row": out_row, "out_keys": [o[0] for o in OUT]}


# --------------------------------------------------------------------------- recalculation

def recalc(path):
    """Recalculate with headless LibreOffice and return the computed values."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        sys.exit("LibreOffice is required: apt-get install -y libreoffice-calc")
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([soffice, "--headless", "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML",
                            "--outdir", tmp, path], capture_output=True, timeout=240, env={**os.environ, "HOME": tmp})
        out = os.path.join(tmp, os.path.basename(path))
        if not os.path.exists(out):
            sys.exit("LibreOffice could not recalculate the workbook (is libreoffice-calc installed?)\n"
                     + r.stderr.decode(errors="replace")[-800:])
        return load_workbook(out, data_only=True)


def read_state(wb, rows):
    n, years = rows["n"], rows["years"]
    ny = len(years)
    ser = lambda sheet, table, key: [wb[sheet].cell(rows[table][key], 3 + i).value for i in range(ny)]
    D, SC = wb["DCF"], wb["Scenarios"]
    grid = lambda r0: {"rows": [D.cell(r0 + 2 + k, 2).value for k in range(5)],
                       "cols": [D.cell(r0 + 1, 3 + j).value for j in range(5)],
                       "values": [[D.cell(r0 + 2 + k, 3 + j).value for j in range(5)] for k in range(5)]}
    errors = [f"{ws.title}!{c.coordinate}" for ws in wb for row in ws.iter_rows() for c in row
              if isinstance(c.value, str) and (c.value.startswith("#") or c.value.startswith("Err:"))]
    return {
        "income_statement": {k: ser("Income Statement", "IS", k) for k in
                             ("revenue", "growth", "gross_profit", "gm", "rnd", "sga", "ebit", "ebit_margin",
                              "ebitda", "ebitda_margin", "net_income", "ni_margin", "eps", "eps_growth", "shares", "sbc")},
        "balance_sheet": {k: ser("Balance Sheet", "BS", k) for k in
                          ("bs_cash", "bs_sti", "bs_ar", "bs_inv", "bs_ppe", "bs_ta", "bs_ap", "bs_debt", "bs_tl",
                           "bs_eq", "bs_netdebt", "bs_nwc", "bs_check")},
        "cash_flow": {k: ser("Cash Flow", "CF", k) for k in ("cf_cfo", "cf_capex", "cf_fcf", "cf_div", "cf_bb")},
        "ratios": {k: ser("Ratios", "RT", k) for k in
                   ("r_roic", "r_fcf_margin", "r_fcf_conv", "r_nd_ebitda", "r_cover", "r_sbc", "r_payout")},
        "cash_ok": all(v == 1 for v in ser("Balance Sheet", "BS", "bs_cash_ok")[n:]),
        "balanced": all(v is not None and abs(v) <= 0.01 for v in ser("Balance Sheet", "BS", "bs_check")),
        "errors": errors,
        "dcf": {
            "share_price": D["C5"].value, "price_as_of": D["C6"].value, "diluted_shares_m": D["C7"].value,
            "market_cap": D["C18"].value, "net_debt": D["C22"].value, "cost_of_equity": D["C19"].value,
            "wacc": D["C24"].value, "terminal_growth": D["C13"].value, "exit_multiple": D["C14"].value,
            "ufcf": [D.cell(36, 3 + j).value for j in range(N_PROJ)],
            "ev_perpetuity": D["B47"].value, "ev_exit": D["C47"].value,
            "value_perpetuity": D["B50"].value, "value_exit": D["C50"].value,
            "value_blend": D["C56"].value, "upside_blend": D["C57"].value,
            "tv_pct_ev": D["B52"].value, "implied_exit_multiple": D["B53"].value,
            "implied_growth_from_exit": D["C54"].value,
            "ev_revenue_fy1": D["C60"].value, "ev_ebitda_fy1": D["C61"].value, "ev_ebitda_ltm": D["C62"].value,
            "pe_fy1": D["C63"].value, "pe_fy2": D["C64"].value, "fcf_yield_fy1": D["C65"].value,
            "sens_growth": grid(67), "sens_exit": grid(75),
        },
        "scenario_outputs": {k: SC.cell(r, 7).value for k, r in rows["out_row"].items()},
    }


def cmd_build(args):
    with open(os.path.join(args.workdir, "historicals.json")) as f:
        h = json.load(f)
    with open(os.path.join(args.workdir, "assumptions.json")) as f:
        a = json.load(f)
    validate_assumptions(a)
    hs = normalize(h)
    states = {}
    with tempfile.TemporaryDirectory() as tmp:
        for idx, s in enumerate(SCENARIOS, start=1):
            p = os.path.join(tmp, f"{s}.xlsx")
            rows = build_workbook(h, hs, a, p, active=idx)
            states[s] = read_state(recalc(p), rows)
            print(f"  {SCEN_LABEL[s]:<4}  balanced={states[s]['balanced']}  cash_ok={states[s]['cash_ok']}  "
                  f"value/share=${states[s]['dcf']['value_blend'] or 0:,.2f}")

    # Final workbook: base selected, with every scenario's outputs captured side by side.
    rows = build_workbook(h, hs, a, args.xlsx, active=2)
    wb = load_workbook(args.xlsx)
    SC = wb["Scenarios"]
    for key, r in rows["out_row"].items():
        for j, s in enumerate(SCENARIOS):
            SC.cell(r, 3 + j, states[s]["scenario_outputs"][key]).font = F_CALC
    wb.calculation.fullCalcOnLoad = True
    wb.save(args.xlsx)
    final = read_state(recalc(args.xlsx), rows)

    sc = a["scenarios"]
    probs = {s: sc[s]["probability"] for s in SCENARIOS}
    weighted = sum(probs[s] * states[s]["dcf"]["value_blend"] for s in SCENARIOS)
    price = final["dcf"]["share_price"] or 0
    summary = {
        "ticker": h["ticker"], "name": h["name"], "years": rows["years"], "n_hist": rows["n"],
        **{k: final[k] for k in ("income_statement", "balance_sheet", "cash_flow", "ratios", "dcf")},
        "scenarios": {s: {
            "label": SCEN_LABEL[s], "probability": probs[s], "narrative": sc[s].get("narrative", ""),
            "terminal_growth": sc[s]["terminal_growth"], "exit_multiple": sc[s]["exit_ev_ebitda"],
            "revenue_growth": sc[s]["revenue_growth"], "gross_margin": sc[s]["gross_margin"],
            "outputs": states[s]["scenario_outputs"],
            "revenue": states[s]["income_statement"]["revenue"], "eps": states[s]["income_statement"]["eps"],
            "ebit_margin": states[s]["income_statement"]["ebit_margin"],
        } for s in SCENARIOS},
        "weighted_value": weighted,
        "weighted_upside": (weighted / price - 1) if price else None,
        "checks": {
            "balanced_all_scenarios": all(states[s]["balanced"] for s in SCENARIOS),
            "cash_positive_all_scenarios": all(states[s]["cash_ok"] for s in SCENARIOS),
            "formula_errors": sorted(set(sum((states[s]["errors"] for s in SCENARIOS), final["errors"]))),
        },
    }
    out = os.path.join(args.workdir, "summary.json")
    with open(out, "w") as f:
        json.dump(summary, f, indent=1, default=str)
    d = final["dcf"]
    print(f"wrote {args.xlsx} and {out}")
    print(f"WACC {d['wacc']:.2%} · base ${d['value_blend']:,.2f} · probability-weighted ${weighted:,.2f} "
          f"vs price ${price:,.2f}" + (f" ({weighted / price - 1:+.1%})" if price else ""))
    c = summary["checks"]
    if not c["balanced_all_scenarios"] or c["formula_errors"]:
        sys.exit(f"MODEL CHECK FAILED — balanced: {c['balanced_all_scenarios']}; errors: {c['formula_errors'][:10]}")
    if not c["cash_positive_all_scenarios"]:
        print("WARNING: projected cash goes negative in at least one scenario; lower buybacks or add debt issuance")
    print("checks passed: balance sheet balances in every year of every scenario; no formula errors")


# --------------------------------------------------------------------------- page

PICKS_OPEN = '<script type="application/json" id="picks">'
REQUIRED_THESIS = ("date", "stance", "sector", "thesis", "catalysts", "risks", "kill_criteria",
                   "valuation_note", "sources")
# Phrases that signal an unsupported or hedged claim. The thesis is rejected if any appear.
VAGUE = ["well-positioned", "well positioned", "poised to", "best-in-class", "best in class", "world-class",
         "robust", "significant upside", "strong growth", "solid growth", "compelling", "attractive valuation",
         "tailwinds", "headwinds", "could potentially", "may potentially", "we believe", "we think", "it seems",
         "arguably", "going forward", "moving forward", "game-changer", "game changer", "unlock value",
         "unlocking value", "secular growth story", "strong fundamentals", "solid fundamentals", "market leader",
         "industry-leading", "industry leading", "cutting-edge", "synergies", "a lot of", "huge", "massive",
         "significantly", "substantially", "various", "numerous"]


def has_number(s):
    return any(ch.isdigit() for ch in s or "")


def lint_thesis(t):
    errs = []
    texts = [("headline", t["thesis"].get("headline", "")), ("summary", t["thesis"].get("summary", "")),
             ("variant", t["thesis"].get("variant", "")), ("valuation_note", t.get("valuation_note", ""))]
    pillars = t["thesis"].get("pillars") or []
    if not 3 <= len(pillars) <= 4:
        errs.append("thesis needs 3–4 pillars")
    for i, p in enumerate(pillars):
        for k in ("title", "body", "proof"):
            if not p.get(k):
                errs.append(f"pillar {i + 1} is missing '{k}'")
        texts += [(f"pillar {i + 1} body", p.get("body", "")), (f"pillar {i + 1} proof", p.get("proof", ""))]
        if sum(ch.isdigit() for ch in p.get("body", "")) < 2:
            errs.append(f"pillar {i + 1} body needs at least two specific figures")
        if not has_number(p.get("proof")):
            errs.append(f"pillar {i + 1} proof must be a number")
    for k in ("headline", "variant"):
        if not has_number(t["thesis"].get(k)):
            errs.append(f"thesis.{k} must contain a specific figure")
    for c in t.get("catalysts", []):
        if not has_number(c.get("when")):
            errs.append(f"catalyst '{c.get('what', '')[:40]}' needs a dated 'when'")
        texts.append(("catalyst", c.get("what", "")))
    for r in t.get("risks", []):
        texts += [("risk", r.get("risk", "")), ("risk", r.get("mitigant", ""))]
    for k in t.get("kill_criteria", []):
        if not has_number(k):
            errs.append(f"kill criterion needs a measurable threshold: '{k[:60]}'")
    if not 3 <= len(t.get("catalysts", [])) <= 5:
        errs.append("3–5 catalysts required")
    if not 3 <= len(t.get("risks", [])) <= 5:
        errs.append("3–5 risks required")
    if not 2 <= len(t.get("kill_criteria", [])) <= 3:
        errs.append("2–3 kill criteria required")
    if len(t.get("sources", [])) < 4:
        errs.append("at least 4 sources required")
    for where, s in texts:
        low = (s or "").lower()
        for v in VAGUE:
            if v in low:
                errs.append(f"vague phrase '{v}' in {where}")
    return errs


def cmd_page(args):
    with open(os.path.join(args.workdir, "summary.json")) as f:
        s = json.load(f)
    with open(args.thesis) as f:
        t = json.load(f)
    missing = [k for k in REQUIRED_THESIS if not t.get(k)]
    if missing:
        sys.exit(f"thesis.json is missing: {', '.join(missing)}")
    errs = lint_thesis(t)
    if errs:
        sys.exit("THESIS REJECTED — rewrite and rerun:\n  - " + "\n  - ".join(errs))
    c = s["checks"]
    if not c["balanced_all_scenarios"] or c["formula_errors"]:
        sys.exit("the model failed its checks; fix it before publishing")
    if not c["cash_positive_all_scenarios"]:
        sys.exit("projected cash goes negative in a scenario; fix buybacks/debt before publishing")
    d = s["dcf"]
    if not d.get("share_price"):
        sys.exit("share_price is 0 in the model; set it in assumptions.json and rebuild")
    pid = f"{t['date']}-{s['ticker']}"
    entry = {
        "id": pid, "date": t["date"], "ticker": s["ticker"], "name": t.get("name") or s["name"],
        "exchange": t.get("exchange"), "sector": t["sector"], "stance": t["stance"],
        "price": d["share_price"], "price_as_of": d["price_as_of"], "market_cap_m": d["market_cap"],
        "net_debt_m": d["net_debt"], "price_target": s["weighted_value"], "upside": s["weighted_upside"],
        "fair_value": {"perpetuity": d["value_perpetuity"], "exit": d["value_exit"], "base": d["value_blend"]},
        "dcf": {k: d[k] for k in ("wacc", "cost_of_equity", "terminal_growth", "exit_multiple", "tv_pct_ev",
                                  "implied_exit_multiple", "ev_revenue_fy1", "ev_ebitda_fy1", "ev_ebitda_ltm",
                                  "pe_fy1", "pe_fy2", "fcf_yield_fy1", "sens_growth", "sens_exit")},
        "scenarios": s["scenarios"],
        "thesis": t["thesis"], "catalysts": t["catalysts"], "risks": t["risks"],
        "kill_criteria": t["kill_criteria"], "valuation_note": t["valuation_note"],
        "model_note": t.get("model_note", ""), "sources": t["sources"], "test": bool(t.get("test")), "test_note": t.get("test_note", ""),
        "financials": {"years": s["years"], "n_hist": s["n_hist"], "income_statement": s["income_statement"],
                       "balance_sheet": s["balance_sheet"], "cash_flow": s["cash_flow"], "ratios": s["ratios"]},
        "model_file": f"{pid}.xlsx",
        "model_b64": base64.b64encode(open(args.xlsx, "rb").read()).decode("ascii"),
    }
    with open(args.page) as f:
        html = f.read()
    start = html.index(PICKS_OPEN) + len(PICKS_OPEN)
    end = html.index("</script>", start)
    picks = json.loads(html[start:end])
    picks = [p for p in picks if p["id"] != pid] + [entry]
    picks.sort(key=lambda p: p["date"])
    blob = json.dumps(picks, separators=(",", ":"), ensure_ascii=False, default=str).replace("</", "<\\/")
    with open(args.page, "w") as f:
        f.write(html[:start] + blob + html[end:])
    print(f"page now holds {len(picks)} pick(s); latest {pid}; the model is embedded in the page")
    print(f"publish {args.page} to PAGE (no extra files needed)")
    print("past tickers:", ", ".join(p["ticker"] for p in picks))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("ticker")
    f.add_argument("--out", required=True)
    b = sub.add_parser("build")
    b.add_argument("workdir")
    b.add_argument("--xlsx", required=True)
    g = sub.add_parser("page")
    g.add_argument("page")
    g.add_argument("workdir")
    g.add_argument("--thesis", required=True)
    g.add_argument("--xlsx", required=True, help="the built workbook, embedded in the page for download")
    args = p.parse_args()
    {"fetch": cmd_fetch, "build": cmd_build, "page": cmd_page}[args.cmd](args)


if __name__ == "__main__":
    main()
