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


def default_assumptions(hs, h):
    rev = hs["revenue"]
    g = [(b / a - 1) for a, b in zip(rev[:-1], rev[1:]) if a]
    g_recent = avg(g[-3:])
    # Fade the recent growth rate toward 3% over the projection period.
    growth = [round(g_recent + (0.03 - g_recent) * i / (N_PROJ - 1), 4) for i in range(N_PROJ)]
    rv = rev[-3:]
    pretax = hs["pretax"][-3:]
    tax_rate = ratio(hs["tax"], hs["pretax"])
    tax_rate = min(max(tax_rate, 0.12), 0.30)
    flat = lambda x: [round(x, 4)] * N_PROJ
    last = lambda k: hs[k][-1]
    cogs_last = last("cogs") or 1.0
    return {
        "_note": ("Defaults are 3-year historical averages with revenue growth fading to 3%. "
                  "Replace them with your own view; the thesis should explain every change."),
        "revenue_growth": growth,
        "gross_margin": flat(1 - ratio(hs["cogs"], rev)),
        "rnd_pct_revenue": flat(ratio(hs["rnd"], rev)),
        "sga_pct_revenue": flat(ratio(hs["sga"], rev)),
        "other_opex_pct_revenue": flat(ratio(
            [r - c - rd - s - e for r, c, rd, s, e in zip(rev, hs["cogs"], hs["rnd"], hs["sga"], hs["ebit"])],
            rev)),
        "da_pct_revenue": flat(ratio(hs["da"], rev)),
        "capex_pct_revenue": flat(ratio(hs["capex"], rev)),
        "sbc_pct_revenue": flat(ratio(hs["sbc"], rev)),
        "dso_days": flat(365 * ratio(hs["ar"], rev, 1)),
        "dio_days": flat(365 * ratio(hs["inventory"], hs["cogs"], 1)),
        "dpo_days": flat(365 * ratio(hs["ap"], hs["cogs"], 1)),
        "other_ca_pct_revenue": flat(ratio(hs["other_ca"], rev, 1)),
        "other_cl_pct_revenue": flat(ratio(hs["other_cl"], rev, 1)),
        "tax_rate": flat(tax_rate),
        "interest_rate_on_debt": flat(min(ratio(hs["interest_expense"], hs["debt"], 1) or 0.05, 0.12)),
        "interest_rate_on_cash": flat(0.035),
        "dividend_payout_pct_net_income": flat(ratio(hs["dividends"], hs["net_income"], 1)),
        "buybacks_musd": flat(avg(hs["buybacks"][-3:])),
        "net_debt_issuance_musd": flat(0.0),
        "share_price": None,
        "share_price_as_of": None,
        "diluted_shares_m": round((h.get("shares_outstanding_latest") or 0) / M, 2) or hs["diluted_shares"][-1],
        "risk_free_rate": 0.042,
        "equity_risk_premium": 0.05,
        "beta": 1.0,
        "pretax_cost_of_debt": 0.055,
        "terminal_growth": 0.025,
        "exit_ev_ebitda": 12.0,
    }


# --------------------------------------------------------------------------- build

BLUE = Font(name="Calibri", size=10, color="1F4FB4")
BLACK = Font(name="Calibri", size=10, color="000000")
GREEN = Font(name="Calibri", size=10, color="1E7B34")
BOLD = Font(name="Calibri", size=10, bold=True)
TITLE = Font(name="Calibri", size=14, bold=True)
HEAD = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="1F2A44")
PROJ_FILL = PatternFill("solid", fgColor="F2F5FA")
INPUT_FILL = PatternFill("solid", fgColor="FFF7D6")
TOP = Border(top=Side(style="thin", color="888888"))
NUM = '#,##0;(#,##0);"–"'
NUM1 = '#,##0.0;(#,##0.0);"–"'
PCT = '0.0%;(0.0%);"–"'
DAYS = '0.0'
USD = '$#,##0.00'
MULT = '0.0"x"'


class Sheet:
    """Rows addressed by key; columns shared across every statement sheet."""

    def __init__(self, wb, title, years, n_hist):
        self.ws = wb.create_sheet(title)
        self.title = title
        self.years = years
        self.n_hist = n_hist
        self.row = {}
        self.r = 4
        ws = self.ws
        ws.column_dimensions["A"].width = 38
        ws.column_dimensions["B"].width = 10
        ws.cell(1, 1, title).font = TITLE
        ws.cell(2, 1, "$ millions except per-share data").font = Font(size=9, italic=True, color="666666")
        for i, y in enumerate(years):
            c = ws.cell(3, 3 + i, y)
            c.font, c.fill = HEAD, HEAD_FILL
            c.alignment = Alignment(horizontal="center")
            ws.column_dimensions[get_column_letter(3 + i)].width = 12
        for c in (1, 2):
            ws.cell(3, c).fill = HEAD_FILL
        ws.cell(3, 1, "Fiscal year").font = HEAD
        ws.freeze_panes = "C4"

    def col(self, i):
        return get_column_letter(3 + i)

    def ref(self, key, i, sheet=None):
        a = f"{self.col(i)}{self.row[key]}"
        return f"'{self.title}'!{a}" if sheet else a

    def section(self, text):
        self.r += 1
        self.ws.cell(self.r, 1, text).font = BOLD
        self.r += 1

    def line(self, key, label, hist=None, proj=None, fmt=NUM, bold=False, total=False, unit=""):
        """hist: list of values or formulas (callable i->str) per historical column; proj: callable i->formula."""
        r = self.r
        self.row[key] = r
        ws = self.ws
        ws.cell(r, 1, label).font = BOLD if bold else BLACK
        ws.cell(r, 2, unit).font = Font(size=9, color="666666")
        n = len(self.years)
        for i in range(n):
            is_hist = i < self.n_hist
            src = hist if is_hist else proj
            if src is None:
                continue
            val = src(i) if callable(src) else src[i]
            if val is None:
                continue
            c = ws.cell(r, 3 + i, val)
            c.number_format = fmt
            if isinstance(val, str) and val.startswith("="):
                c.font = GREEN if "!" in val else BLACK
            else:
                c.font = BLUE
            if bold:
                c.font = Font(name="Calibri", size=10, bold=True, color=c.font.color.rgb)
            if not is_hist:
                c.fill = PROJ_FILL
            if total:
                c.border = TOP
        self.r += 1
        return r


def build_workbook(h, hs, a, path):
    n = len(h["fiscal_year_ends"])
    fy = [int(e[:4]) for e in h["fiscal_year_ends"]]
    years = [f"FY{y}A" for y in fy] + [f"FY{fy[-1] + k}E" for k in range(1, N_PROJ + 1)]
    H = range(n)
    P = range(n, n + N_PROJ)
    wb = Workbook()
    wb.remove(wb.active)

    cover = wb.create_sheet("Cover")
    A = Sheet(wb, "Assumptions", years, n)
    IS = Sheet(wb, "Income Statement", years, n)
    BS = Sheet(wb, "Balance Sheet", years, n)
    CF = Sheet(wb, "Cash Flow", years, n)
    col = IS.col

    def hv(key):
        return [hs[key][i] for i in H]

    def pa(key):
        return lambda i: a[key][i - n]

    # ---- Assumptions: inputs in projection columns, realized ratios in historical ones.
    isr = lambda k, i: f"'Income Statement'!{col(i)}{{{k}}}"
    A.section("Operating drivers")
    A_KEYS = [
        ("revenue_growth", "Revenue growth", PCT),
        ("gross_margin", "Gross margin", PCT),
        ("rnd_pct_revenue", "R&D % of revenue", PCT),
        ("sga_pct_revenue", "SG&A % of revenue", PCT),
        ("other_opex_pct_revenue", "Other operating expense % of revenue", PCT),
        ("da_pct_revenue", "D&A % of revenue", PCT),
        ("capex_pct_revenue", "Capex % of revenue", PCT),
        ("sbc_pct_revenue", "Stock comp % of revenue", PCT),
        ("tax_rate", "Tax rate", PCT),
    ]
    WC_KEYS = [
        ("dso_days", "DSO (days of revenue)", DAYS),
        ("dio_days", "DIO (days of COGS)", DAYS),
        ("dpo_days", "DPO (days of COGS)", DAYS),
        ("other_ca_pct_revenue", "Other current assets % of revenue", PCT),
        ("other_cl_pct_revenue", "Other current liabilities % of revenue", PCT),
    ]
    CAP_KEYS = [
        ("interest_rate_on_debt", "Interest rate on debt (on opening balance)", PCT),
        ("interest_rate_on_cash", "Interest rate on cash & investments", PCT),
        ("dividend_payout_pct_net_income", "Dividend payout % of net income", PCT),
        ("buybacks_musd", "Share repurchases ($m)", NUM),
        ("net_debt_issuance_musd", "Net debt issuance / (repayment) ($m)", NUM),
    ]
    for k, label, fmt in A_KEYS:
        A.line(k, label, None, pa(k), fmt)
    A.section("Working capital")
    for k, label, fmt in WC_KEYS:
        A.line(k, label, None, pa(k), fmt)
    A.section("Capital structure")
    for k, label, fmt in CAP_KEYS:
        A.line(k, label, None, pa(k), fmt)
    for k, *_ in A_KEYS + WC_KEYS + CAP_KEYS:
        for i in P:
            A.ws.cell(A.row[k], 3 + i).fill = INPUT_FILL
    A.r += 1
    A.ws.cell(A.r, 1, "Yellow cells are inputs. Historical columns on the other sheets show the realized ratios.").font = \
        Font(size=9, italic=True, color="666666")

    ar = lambda k, i: f"Assumptions!{col(i)}{A.row[k]}"

    # ---- Income statement
    IS.section("Income statement")
    IS.line("revenue", "Revenue", hv("revenue"),
            lambda i: f"={col(i-1)}{{revenue}}*(1+{ar('revenue_growth', i)})", bold=True)
    IS.line("growth", "  growth %", lambda i: None if i == 0 else f"={col(i)}{{revenue}}/{col(i-1)}{{revenue}}-1",
            lambda i: f"={col(i)}{{revenue}}/{col(i-1)}{{revenue}}-1", PCT)
    IS.line("cogs", "Cost of revenue", hv("cogs"),
            lambda i: f"={col(i)}{{revenue}}*(1-{ar('gross_margin', i)})")
    IS.line("gross_profit", "Gross profit", lambda i: f"={col(i)}{{revenue}}-{col(i)}{{cogs}}",
            lambda i: f"={col(i)}{{revenue}}-{col(i)}{{cogs}}", bold=True, total=True)
    IS.line("gm", "  gross margin %", lambda i: f"={col(i)}{{gross_profit}}/{col(i)}{{revenue}}",
            lambda i: f"={col(i)}{{gross_profit}}/{col(i)}{{revenue}}", PCT)
    IS.line("rnd", "Research & development", hv("rnd"), lambda i: f"={col(i)}{{revenue}}*{ar('rnd_pct_revenue', i)}")
    IS.line("sga", "Selling, general & administrative", hv("sga"),
            lambda i: f"={col(i)}{{revenue}}*{ar('sga_pct_revenue', i)}")
    IS.line("other_opex", "Other operating expense, net",
            [hs["revenue"][i] - hs["cogs"][i] - hs["rnd"][i] - hs["sga"][i] - hs["ebit"][i] for i in H],
            lambda i: f"={col(i)}{{revenue}}*{ar('other_opex_pct_revenue', i)}")
    IS.line("ebit", "Operating income (EBIT)",
            lambda i: f"={col(i)}{{gross_profit}}-{col(i)}{{rnd}}-{col(i)}{{sga}}-{col(i)}{{other_opex}}",
            lambda i: f"={col(i)}{{gross_profit}}-{col(i)}{{rnd}}-{col(i)}{{sga}}-{col(i)}{{other_opex}}",
            bold=True, total=True)
    IS.line("ebit_margin", "  operating margin %", lambda i: f"={col(i)}{{ebit}}/{col(i)}{{revenue}}",
            lambda i: f"={col(i)}{{ebit}}/{col(i)}{{revenue}}", PCT)
    IS.line("interest_expense", "Interest expense", hv("interest_expense"),
            lambda i: f"='Balance Sheet'!{col(i-1)}{{bs_debt}}*{ar('interest_rate_on_debt', i)}")
    IS.line("other_income", "Interest income & other, net",
            [hs["pretax"][i] - hs["ebit"][i] + hs["interest_expense"][i] for i in H],
            lambda i: f"=('Balance Sheet'!{col(i-1)}{{bs_cash}}+'Balance Sheet'!{col(i-1)}{{bs_sti}})"
                      f"*{ar('interest_rate_on_cash', i)}")
    IS.line("pretax", "Pre-tax income",
            lambda i: f"={col(i)}{{ebit}}-{col(i)}{{interest_expense}}+{col(i)}{{other_income}}",
            lambda i: f"={col(i)}{{ebit}}-{col(i)}{{interest_expense}}+{col(i)}{{other_income}}", total=True)
    IS.line("tax", "Income tax", hv("tax"), lambda i: f"={col(i)}{{pretax}}*{ar('tax_rate', i)}")
    IS.line("tax_rate", "  effective tax rate %", lambda i: f"=IF({col(i)}{{pretax}}=0,0,{col(i)}{{tax}}/{col(i)}{{pretax}})",
            lambda i: f"={col(i)}{{tax}}/{col(i)}{{pretax}}", PCT)
    IS.line("ni_other", "Minority interest, disc. ops & other",
            [hs["net_income"][i] - (hs["pretax"][i] - hs["tax"][i]) for i in H], lambda i: 0)
    IS.line("net_income", "Net income", lambda i: f"={col(i)}{{pretax}}-{col(i)}{{tax}}+{col(i)}{{ni_other}}",
            lambda i: f"={col(i)}{{pretax}}-{col(i)}{{tax}}+{col(i)}{{ni_other}}", bold=True, total=True)
    IS.line("ni_margin", "  net margin %", lambda i: f"={col(i)}{{net_income}}/{col(i)}{{revenue}}",
            lambda i: f"={col(i)}{{net_income}}/{col(i)}{{revenue}}", PCT)
    IS.section("Per share & memo")
    IS.line("shares", "Diluted shares (m)", hv("diluted_shares"),
            lambda i: f"=IF(DCF!$C$4=0,{col(i-1)}{{shares}},"
                      f"MAX({col(i-1)}{{shares}}-{ar('buybacks_musd', i)}/DCF!$C$4,{col(n-1)}{{shares}}*0.5))", NUM1)
    IS.line("eps", "Diluted EPS", lambda i: f"=IF({col(i)}{{shares}}=0,0,{col(i)}{{net_income}}/{col(i)}{{shares}})",
            lambda i: f"={col(i)}{{net_income}}/{col(i)}{{shares}}", USD)
    IS.line("da", "Depreciation & amortization", hv("da"), lambda i: f"={col(i)}{{revenue}}*{ar('da_pct_revenue', i)}")
    IS.line("ebitda", "EBITDA", lambda i: f"={col(i)}{{ebit}}+{col(i)}{{da}}",
            lambda i: f"={col(i)}{{ebit}}+{col(i)}{{da}}", bold=True)
    IS.line("ebitda_margin", "  EBITDA margin %", lambda i: f"={col(i)}{{ebitda}}/{col(i)}{{revenue}}",
            lambda i: f"={col(i)}{{ebitda}}/{col(i)}{{revenue}}", PCT)
    IS.line("sbc", "Stock-based compensation", hv("sbc"), lambda i: f"={col(i)}{{revenue}}*{ar('sbc_pct_revenue', i)}")

    # ---- Balance sheet
    B = "'Balance Sheet'!"
    BS.section("Assets")
    BS.line("bs_cash", "Cash & equivalents", hv("cash"), lambda i: f"='Cash Flow'!{col(i)}{{cf_end_cash}}")
    BS.line("bs_sti", "Short-term investments", hv("st_investments"), lambda i: f"={col(i-1)}{{bs_sti}}")
    BS.line("bs_ar", "Accounts receivable", hv("ar"),
            lambda i: f"='Income Statement'!{col(i)}{IS.row['revenue']}*{ar('dso_days', i)}/365")
    BS.line("bs_inv", "Inventory", hv("inventory"),
            lambda i: f"='Income Statement'!{col(i)}{IS.row['cogs']}*{ar('dio_days', i)}/365")
    BS.line("bs_oca", "Other current assets", hv("other_ca"),
            lambda i: f"='Income Statement'!{col(i)}{IS.row['revenue']}*{ar('other_ca_pct_revenue', i)}")
    tca = lambda i: f"=SUM({col(i)}{{bs_cash}}:{col(i)}{{bs_oca}})"
    BS.line("bs_tca", "Total current assets", tca, tca, bold=True, total=True)
    BS.line("bs_ppe", "Property, plant & equipment, net", hv("ppe"),
            lambda i: f"={col(i-1)}{{bs_ppe}}+'Cash Flow'!{col(i)}{{cf_capex_pos}}-'Income Statement'!{col(i)}{IS.row['da']}")
    BS.line("bs_onca", "Goodwill, intangibles & other", hv("other_nca"), lambda i: f"={col(i-1)}{{bs_onca}}")
    ta = lambda i: f"={col(i)}{{bs_tca}}+{col(i)}{{bs_ppe}}+{col(i)}{{bs_onca}}"
    BS.line("bs_ta", "Total assets", ta, ta, bold=True, total=True)
    BS.section("Liabilities & equity")
    BS.line("bs_ap", "Accounts payable", hv("ap"),
            lambda i: f"='Income Statement'!{col(i)}{IS.row['cogs']}*{ar('dpo_days', i)}/365")
    BS.line("bs_ocl", "Other current liabilities", hv("other_cl"),
            lambda i: f"='Income Statement'!{col(i)}{IS.row['revenue']}*{ar('other_cl_pct_revenue', i)}")
    BS.line("bs_debt", "Total debt (short + long term)", hv("debt"),
            lambda i: f"={col(i-1)}{{bs_debt}}+{ar('net_debt_issuance_musd', i)}")
    BS.line("bs_oncl", "Other non-current liabilities", hv("other_ncl"), lambda i: f"={col(i-1)}{{bs_oncl}}")
    tl = lambda i: f"=SUM({col(i)}{{bs_ap}}:{col(i)}{{bs_oncl}})"
    BS.line("bs_tl", "Total liabilities", tl, tl, bold=True, total=True)
    BS.line("bs_eq", "Total equity", hv("equity"),
            lambda i: f"={col(i-1)}{{bs_eq}}+'Income Statement'!{col(i)}{IS.row['net_income']}"
                      f"+'Income Statement'!{col(i)}{IS.row['sbc']}"
                      f"-'Cash Flow'!{col(i)}{{cf_div_pos}}-'Cash Flow'!{col(i)}{{cf_bb_pos}}")
    tle = lambda i: f"={col(i)}{{bs_tl}}+{col(i)}{{bs_eq}}"
    BS.line("bs_tle", "Total liabilities & equity", tle, tle, bold=True, total=True)
    chk = lambda i: f"=ROUND({col(i)}{{bs_ta}}-{col(i)}{{bs_tle}},3)"
    BS.line("bs_check", "Balance check (should be 0)", chk, chk, NUM1)
    BS.section("Memo")
    BS.line("bs_reported_ta", "Reported total assets (10-K)", hv("total_assets"))
    BS.line("bs_netdebt", "Net debt / (net cash)",
            lambda i: f"={col(i)}{{bs_debt}}-{col(i)}{{bs_cash}}-{col(i)}{{bs_sti}}",
            lambda i: f"={col(i)}{{bs_debt}}-{col(i)}{{bs_cash}}-{col(i)}{{bs_sti}}")
    nwc = lambda i: f"={col(i)}{{bs_ar}}+{col(i)}{{bs_inv}}+{col(i)}{{bs_oca}}-{col(i)}{{bs_ap}}-{col(i)}{{bs_ocl}}"
    BS.line("bs_nwc", "Net working capital (ex cash & debt)", nwc, nwc)

    # ---- Cash flow
    ISR = lambda k: (lambda i: f"='Income Statement'!{col(i)}{IS.row[k]}")
    delta_asset = lambda k: (lambda i: None if i == 0 else f"='Balance Sheet'!{col(i-1)}{{{k}}}-'Balance Sheet'!{col(i)}{{{k}}}")
    delta_liab = lambda k: (lambda i: None if i == 0 else f"='Balance Sheet'!{col(i)}{{{k}}}-'Balance Sheet'!{col(i-1)}{{{k}}}")
    CF.section("Operating activities")
    CF.line("cf_ni", "Net income", ISR("net_income"), ISR("net_income"))
    CF.line("cf_da", "Depreciation & amortization", ISR("da"), ISR("da"))
    CF.line("cf_sbc", "Stock-based compensation", ISR("sbc"), ISR("sbc"))
    CF.line("cf_ar", "(Increase) / decrease in receivables", delta_asset("bs_ar"), delta_asset("bs_ar"))
    CF.line("cf_inv", "(Increase) / decrease in inventory", delta_asset("bs_inv"), delta_asset("bs_inv"))
    CF.line("cf_oca", "(Increase) / decrease in other current assets", delta_asset("bs_oca"), delta_asset("bs_oca"))
    CF.line("cf_ap", "Increase / (decrease) in payables", delta_liab("bs_ap"), delta_liab("bs_ap"))
    CF.line("cf_ocl", "Increase / (decrease) in other current liabilities", delta_liab("bs_ocl"), delta_liab("bs_ocl"))
    cfo_parts = lambda i: f"SUM({col(i)}{{cf_ni}}:{col(i)}{{cf_ocl}})"
    CF.line("cf_other_op", "Other operating items (reported less above)",
            lambda i: f"={hs['cfo'][i]}-{cfo_parts(i)}", lambda i: 0)
    CF.line("cf_cfo", "Cash from operations", lambda i: f"=SUM({col(i)}{{cf_ni}}:{col(i)}{{cf_other_op}})",
            lambda i: f"=SUM({col(i)}{{cf_ni}}:{col(i)}{{cf_other_op}})", bold=True, total=True)
    CF.section("Investing activities")
    CF.line("cf_capex", "Capital expenditures", [-hs["capex"][i] for i in H], lambda i: f"=-{col(i)}{{cf_capex_pos}}")
    CF.line("cf_other_inv", "Acquisitions, investments & other", [hs["cfi"][i] + hs["capex"][i] for i in H], lambda i: 0)
    CF.line("cf_cfi", "Cash from investing", lambda i: f"={col(i)}{{cf_capex}}+{col(i)}{{cf_other_inv}}",
            lambda i: f"={col(i)}{{cf_capex}}+{col(i)}{{cf_other_inv}}", bold=True, total=True)
    CF.section("Financing activities")
    CF.line("cf_debt", "Net debt issuance / (repayment)", None, lambda i: f"={ar('net_debt_issuance_musd', i)}")
    CF.line("cf_div", "Dividends paid", [-hs["dividends"][i] for i in H], lambda i: f"=-{col(i)}{{cf_div_pos}}")
    CF.line("cf_bb", "Share repurchases", [-hs["buybacks"][i] for i in H], lambda i: f"=-{col(i)}{{cf_bb_pos}}")
    CF.line("cf_other_fin", "Debt & other financing (reported less above)",
            [hs["cff"][i] + hs["dividends"][i] + hs["buybacks"][i] for i in H], lambda i: 0)
    CF.line("cf_cff", "Cash from financing", lambda i: f"=SUM({col(i)}{{cf_debt}}:{col(i)}{{cf_other_fin}})",
            lambda i: f"=SUM({col(i)}{{cf_debt}}:{col(i)}{{cf_other_fin}})", bold=True, total=True)
    CF.r += 1
    CF.line("cf_net", "Net change in cash", lambda i: f"={col(i)}{{cf_cfo}}+{col(i)}{{cf_cfi}}+{col(i)}{{cf_cff}}",
            lambda i: f"={col(i)}{{cf_cfo}}+{col(i)}{{cf_cfi}}+{col(i)}{{cf_cff}}", bold=True)
    CF.line("cf_beg_cash", "Beginning cash", None, lambda i: f"='Balance Sheet'!{col(i-1)}{{bs_cash}}")
    CF.line("cf_end_cash", "Ending cash", None, lambda i: f"={col(i)}{{cf_beg_cash}}+{col(i)}{{cf_net}}", bold=True)
    CF.section("Memo")
    CF.line("cf_fcf", "Free cash flow (CFO - capex)", lambda i: f"={col(i)}{{cf_cfo}}+{col(i)}{{cf_capex}}",
            lambda i: f"={col(i)}{{cf_cfo}}+{col(i)}{{cf_capex}}", bold=True)
    CF.line("cf_capex_pos", "Capex (positive)", hv("capex"),
            lambda i: f"='Income Statement'!{col(i)}{IS.row['revenue']}*{ar('capex_pct_revenue', i)}")
    CF.line("cf_div_pos", "Dividends (positive)", hv("dividends"),
            lambda i: f"=MAX(0,'Income Statement'!{col(i)}{IS.row['net_income']}*{ar('dividend_payout_pct_net_income', i)})")
    CF.line("cf_bb_pos", "Buybacks (positive)", hv("buybacks"), lambda i: f"={ar('buybacks_musd', i)}")

    # Resolve {key} placeholders now that every row number is known.
    for S in (IS, BS, CF):
        keys = {**IS.row, **BS.row, **CF.row}
        for row in S.ws.iter_rows(min_row=4):
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("=") and "{" in c.value:
                    v = c.value
                    for k in sorted(keys, key=len, reverse=True):
                        v = v.replace("{" + k + "}", str(keys[k]))
                    c.value = v
                    if "!" in v:
                        c.font = Font(name="Calibri", size=10, color="1E7B34", bold=c.font.bold)

    # ---- DCF
    D = wb.create_sheet("DCF")
    D.column_dimensions["A"].width = 38
    D.column_dimensions["B"].width = 14
    for j in range(3, 3 + N_PROJ + 2):
        D.column_dimensions[get_column_letter(j)].width = 12
    D["A1"] = f"{h['name']} ({h['ticker']}) — discounted cash flow"
    D["A1"].font = TITLE
    D["A2"] = "$ millions except per-share data. Yellow = input."
    D["A2"].font = Font(size=9, italic=True, color="666666")

    last = col(n - 1)
    inputs = [
        ("Share price ($)", a.get("share_price") or 0, USD),                      # C4
        ("Price as of", a.get("share_price_as_of") or "", None),                  # C5
        ("Diluted shares (m)", a["diluted_shares_m"], NUM1),                       # C6
        ("Risk-free rate", a["risk_free_rate"], PCT),                              # C7
        ("Equity risk premium", a["equity_risk_premium"], PCT),                    # C8
        ("Beta", a["beta"], '0.00'),                                               # C9
        ("Pre-tax cost of debt", a["pretax_cost_of_debt"], PCT),                   # C10
        ("Tax rate (for WACC)", f"=Assumptions!{col(n)}{A.row['tax_rate']}", PCT),  # C11
        ("Terminal growth", a["terminal_growth"], PCT),                            # C12
        ("Exit EV / EBITDA", a["exit_ev_ebitda"], MULT),                           # C13
    ]
    for k, (label, val, fmt) in enumerate(inputs):
        r = 4 + k
        D.cell(r, 1, label)
        c = D.cell(r, 3, val)
        if fmt:
            c.number_format = fmt
        if isinstance(val, str) and val.startswith("="):
            c.font = GREEN
        else:
            c.font, c.fill = BLUE, INPUT_FILL
    calc = [
        ("Market capitalization", "=C4*C6", NUM),                                             # C15
        ("Net debt / (net cash), latest FY", f"='Balance Sheet'!{last}{BS.row['bs_netdebt']}", NUM),  # C16
        ("Gross debt, latest FY", f"='Balance Sheet'!{last}{BS.row['bs_debt']}", NUM),                # C17
        ("Cost of equity (CAPM)", "=C7+C9*C8", PCT),                                          # C18
        ("After-tax cost of debt", "=C10*(1-C11)", PCT),                                      # C19
        ("Equity weight", "=IF(C15+C17=0,1,C15/(C15+C17))", PCT),                             # C20
        ("WACC", "=C20*C18+(1-C20)*C19", PCT),                                                # C21
    ]
    for k, (label, f, fmt) in enumerate(calc):
        r = 15 + k
        D.cell(r, 1, label).font = BOLD if label == "WACC" else BLACK
        c = D.cell(r, 3, f)
        c.number_format = fmt
        c.font = GREEN if "!" in f else BLACK

    # Projection block, row 24 onward; columns C.. map to projection years.
    D.cell(23, 1, "Unlevered free cash flow").font = BOLD
    for j in range(N_PROJ):
        c = D.cell(24, 3 + j, years[n + j])
        c.font, c.fill = HEAD, HEAD_FILL
        c.alignment = Alignment(horizontal="center")
    D.cell(24, 1, "Fiscal year").font = HEAD
    D.cell(24, 1).fill = HEAD_FILL
    D.cell(24, 2).fill = HEAD_FILL
    isref = lambda key, j: f"'Income Statement'!{col(n + j)}{IS.row[key]}"
    ufcf_rows = [
        ("Revenue", lambda j: f"={isref('revenue', j)}"),
        ("EBITDA", lambda j: f"={isref('ebitda', j)}"),
        ("EBIT", lambda j: f"={isref('ebit', j)}"),
        ("Taxes on EBIT", lambda j: f"=-{get_column_letter(3+j)}27*Assumptions!{col(n+j)}{A.row['tax_rate']}"),
        ("NOPAT", lambda j: f"={get_column_letter(3+j)}27+{get_column_letter(3+j)}28"),
        ("Plus: D&A", lambda j: f"={isref('da', j)}"),
        ("Less: capex", lambda j: f"='Cash Flow'!{col(n+j)}{CF.row['cf_capex']}"),
        ("Less: increase in net working capital",
         lambda j: f"='Balance Sheet'!{col(n+j-1)}{BS.row['bs_nwc']}-'Balance Sheet'!{col(n+j)}{BS.row['bs_nwc']}"),
        ("Unlevered free cash flow", lambda j: f"=SUM({get_column_letter(3+j)}29:{get_column_letter(3+j)}32)"),
        ("Discount factor", lambda j: f"=1/(1+$C$21)^{j + 1}"),
        ("PV of UFCF", lambda j: f"={get_column_letter(3+j)}33*{get_column_letter(3+j)}34"),
    ]
    for k, (label, f) in enumerate(ufcf_rows):
        r = 25 + k
        bold = label in ("Unlevered free cash flow", "NOPAT", "PV of UFCF")
        D.cell(r, 1, label).font = BOLD if bold else BLACK
        for j in range(N_PROJ):
            c = D.cell(r, 3 + j, f(j))
            c.number_format = '0.000' if label == "Discount factor" else NUM
            c.font = GREEN if "!" in c.value else (BOLD if bold else BLACK)
    D.cell(36, 1, "Stock comp is treated as a real cost: it is not added back to free cash flow.").font = \
        Font(size=9, italic=True, color="666666")
    lc = get_column_letter(2 + N_PROJ)  # last projection column on the DCF sheet
    ufcf = f"C33:{lc}33"
    val = [
        ("", "Perpetuity growth", "Exit multiple"),                                                   # 38
        ("Sum of PV of UFCF", f"=SUM(C35:{lc}35)", f"=SUM(C35:{lc}35)"),                              # 39
        ("Terminal value", f"={lc}33*(1+C12)/(C21-C12)", f"={lc}26*C13"),                             # 40
        ("PV of terminal value", f"=B40*{lc}34", f"=C40*{lc}34"),                                     # 41
        ("Enterprise value", "=B39+B41", "=C39+C41"),                                                 # 42
        ("Less: net debt / plus net cash", "=-C16", "=-C16"),                                         # 43
        ("Equity value", "=B42+B43", "=C42+C43"),                                                     # 44
        ("Implied value per share ($)", "=B44/$C$6", "=C44/$C$6"),                                    # 45
        ("Upside / (downside) vs price", "=IF($C$4=0,0,B45/$C$4-1)", "=IF($C$4=0,0,C45/$C$4-1)"),     # 46
        ("Terminal value % of EV", "=B41/B42", "=C41/C42"),                                           # 47
        ("Implied terminal EV/EBITDA", f"=B40/{lc}26", f"=C40/{lc}26"),                               # 48
        ("Implied perpetuity growth", f"=(B40*C21-{lc}33)/(B40+{lc}33)", f"=(C40*C21-{lc}33)/(C40+{lc}33)"),  # 49
    ]
    fmts = [None, NUM, NUM, NUM, NUM, NUM, NUM, USD, PCT, PCT, MULT, PCT]
    D.cell(37, 1, "Valuation").font = BOLD
    for k, (label, b, c_) in enumerate(val):
        r = 38 + k
        D.cell(r, 1, label).font = BOLD if label.startswith(("Implied value", "Enterprise")) else BLACK
        for cc, v in ((2, b), (3, c_)):
            cell = D.cell(r, cc, v)
            if k == 0:
                cell.font, cell.fill = HEAD, HEAD_FILL
            elif fmts[k]:
                cell.number_format = fmts[k]
                if label.startswith("Implied value"):
                    cell.font = BOLD
    D.cell(51, 1, "Blended value per share (50/50)").font = BOLD
    D["C51"] = "=AVERAGE(B45,C45)"
    D["C51"].number_format = USD
    D["C51"].font = BOLD
    D.cell(52, 1, "Blended upside / (downside)").font = BOLD
    D["C52"] = "=IF(C4=0,0,C51/C4-1)"
    D["C52"].number_format = PCT

    D.cell(54, 1, "Market multiples at current price").font = BOLD
    mults = [
        ("EV / EBITDA, next FY", f"=(C15+C16)/C26"),
        ("EV / revenue, next FY", f"=(C15+C16)/C25"),
        ("P / E, next FY", f"=C4/'Income Statement'!{col(n)}{IS.row['eps']}"),
        ("FCF yield, next FY (UFCF / EV)", f"=C33/(C15+C16)"),
    ]
    for k, (label, f) in enumerate(mults):
        D.cell(55 + k, 1, label)
        c = D.cell(55 + k, 3, f)
        c.number_format = PCT if "yield" in label else MULT

    # Sensitivity: value per share across WACC x terminal growth (perpetuity method).
    D.cell(60, 1, "Sensitivity: value per share, WACC (down) x terminal growth (across)").font = BOLD
    gs = [-0.01, -0.005, 0, 0.005, 0.01]
    ws_ = [-0.01, -0.005, 0, 0.005, 0.01]
    for j, dg in enumerate(gs):
        c = D.cell(61, 3 + j, f"=$C$12+({dg})")
        c.number_format = PCT
        c.font = BOLD
    for k, dw in enumerate(ws_):
        r = 62 + k
        c = D.cell(r, 2, f"=$C$21+({dw})")
        c.number_format = PCT
        c.font = BOLD
        for j in range(len(gs)):
            w = f"$B{r}"
            g = f"{get_column_letter(3+j)}$61"
            f = (f"=(NPV({w},$C$33:${lc}$33)+${lc}$33*(1+{g})/({w}-{g})/(1+{w})^{N_PROJ}-$C$16)/$C$6")
            cell = D.cell(r, 3 + j, f)
            cell.number_format = USD
            if dw == 0 and dg == 0:
                cell.fill = INPUT_FILL

    # ---- Cover
    cover.column_dimensions["A"].width = 34
    cover.column_dimensions["B"].width = 60
    cover["A1"] = f"{h['name']} ({h['ticker']})"
    cover["A1"].font = Font(size=16, bold=True)
    rows = [
        ("Model", "Three-statement model with DCF"),
        ("Historical years", f"{years[0]} – {years[n-1]} (from 10-K XBRL data)"),
        ("Projection years", f"{years[n]} – {years[-1]}"),
        ("Units", "$ millions except per-share"),
        ("Source", h["source"]),
        ("Data fetched", h["fetched"]),
        ("Share price", f"=DCF!C4"),
        ("Value per share (perpetuity)", "=DCF!B45"),
        ("Value per share (exit multiple)", "=DCF!C45"),
        ("Value per share (blended)", "=DCF!C51"),
        ("Upside / (downside), blended", "=DCF!C52"),
        ("WACC", "=DCF!C21"),
        ("Balance sheet balances every year", f"=IF(SUMPRODUCT(ABS('Balance Sheet'!C{BS.row['bs_check']}:"
                                              f"{col(n+N_PROJ-1)}{BS.row['bs_check']}))<0.01,\"Yes\",\"NO — check\")"),
        ("", ""),
        ("Colour code", "Blue = hard-coded input · Black = formula · Green = link to another sheet"),
        ("How to use", "Change the yellow cells on Assumptions and DCF; everything else recalculates."),
        ("Simplifications", "Condensed balance sheet with 'other' lines plugged to reported totals; "
                            "interest on opening balances (no circularity); end-of-year discounting; "
                            "short-term investments, other non-current items held flat."),
    ]
    for k, (label, v) in enumerate(rows):
        cover.cell(3 + k, 1, label).font = BOLD
        c = cover.cell(3 + k, 2, v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if label.startswith(("Share price", "Value per share")):
            c.number_format = USD
        elif label.startswith(("Upside", "WACC")):
            c.number_format = PCT
    wb.move_sheet("DCF", offset=-(len(wb.sheetnames) - 2))
    wb.save(path)
    return {"IS": IS.row, "BS": BS.row, "CF": CF.row, "A": A.row, "n": n, "years": years}


def recalc(path):
    """Recalculate with headless LibreOffice so cached values exist for readers without Excel."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        sys.exit("LibreOffice (soffice) is required to recalculate the workbook")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([soffice, "--headless", "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML",
                        "--outdir", tmp, path], check=True, capture_output=True, timeout=180,
                       env={**os.environ, "HOME": tmp})
        out = os.path.join(tmp, os.path.basename(path))
        # LibreOffice drops some styling on round-trip, so keep the original file and only
        # read the computed values from the recalculated copy.
        return load_workbook(out, data_only=True)


def cmd_build(args):
    with open(os.path.join(args.workdir, "historicals.json")) as f:
        h = json.load(f)
    with open(os.path.join(args.workdir, "assumptions.json")) as f:
        a = json.load(f)
    for k, v in a.items():
        if isinstance(v, list) and len(v) != N_PROJ:
            sys.exit(f"assumption {k} needs {N_PROJ} values, got {len(v)}")
    hs = normalize(h)
    rows = build_workbook(h, hs, a, args.xlsx)
    wb = recalc(args.xlsx)
    n, years = rows["n"], rows["years"]
    get = lambda sheet, r, i: wb[sheet].cell(r, 3 + i).value
    series = lambda sheet, key, table: [get(sheet, rows[table][key], i) for i in range(len(years))]

    checks = series("Balance Sheet", "bs_check", "BS")
    bad = [(years[i], c) for i, c in enumerate(checks) if c is None or abs(c) > 0.01]
    D = wb["DCF"]
    errors = [f"{ws.title}!{c.coordinate}" for ws in wb for row in ws.iter_rows() for c in row
              if isinstance(c.value, str) and c.value.startswith(("#", "Err:"))]
    summary = {
        "ticker": h["ticker"], "name": h["name"], "years": years, "n_hist": n,
        "income_statement": {k: series("Income Statement", k, "IS") for k in
                             ("revenue", "growth", "gross_profit", "gm", "ebit", "ebit_margin",
                              "ebitda", "ebitda_margin", "net_income", "eps", "shares")},
        "balance_sheet": {k: series("Balance Sheet", k, "BS") for k in
                          ("bs_cash", "bs_sti", "bs_ta", "bs_debt", "bs_eq", "bs_netdebt", "bs_check")},
        "cash_flow": {k: series("Cash Flow", k, "CF") for k in ("cf_cfo", "cf_capex", "cf_fcf", "cf_div", "cf_bb")},
        "dcf": {
            "share_price": D["C4"].value, "price_as_of": D["C5"].value, "diluted_shares_m": D["C6"].value,
            "market_cap": D["C15"].value, "net_debt": D["C16"].value,
            "cost_of_equity": D["C18"].value, "wacc": D["C21"].value,
            "terminal_growth": D["C12"].value, "exit_multiple": D["C13"].value,
            "ufcf": [D.cell(33, 3 + j).value for j in range(N_PROJ)],
            "ev_perpetuity": D["B42"].value, "ev_exit": D["C42"].value,
            "value_per_share_perpetuity": D["B45"].value, "value_per_share_exit": D["C45"].value,
            "value_per_share_blended": D["C51"].value, "upside_blended": D["C52"].value,
            "tv_pct_ev_perpetuity": D["B47"].value, "implied_exit_multiple": D["B48"].value,
            "implied_growth_from_exit": D["C49"].value,
            "ev_ebitda_next": D["C55"].value, "pe_next": D["C57"].value,
            "sensitivity": {
                "wacc": [D.cell(62 + k, 2).value for k in range(5)],
                "growth": [D.cell(61, 3 + j).value for j in range(5)],
                "values": [[D.cell(62 + k, 3 + j).value for j in range(5)] for k in range(5)],
            },
        },
        "balance_check_ok": not bad, "formula_errors": errors,
    }
    out = os.path.join(args.workdir, "summary.json")
    with open(out, "w") as f:
        json.dump(summary, f, indent=1, default=str)
    print(f"wrote {args.xlsx} and {out}")
    d = summary["dcf"]
    if d["value_per_share_blended"] is not None:
        print(f"WACC {d['wacc']:.2%}  value/share: perpetuity ${d['value_per_share_perpetuity']:.2f}, "
              f"exit ${d['value_per_share_exit']:.2f}, blended ${d['value_per_share_blended']:.2f} "
              f"vs price ${d['share_price'] or 0:.2f}")
    if bad or errors:
        sys.exit(f"MODEL CHECK FAILED — unbalanced years: {bad}; formula errors: {errors[:10]}")
    print("balance sheet balances in every year; no formula errors")


PICKS_OPEN = '<script type="application/json" id="picks">'
REQUIRED_THESIS = ("date", "stance", "sector", "thesis", "catalysts", "risks", "kill_criteria",
                   "valuation_note", "sources")


def cmd_page(args):
    """Merge summary.json + thesis.json into one pick and insert it into the page's data block."""
    with open(os.path.join(args.workdir, "summary.json")) as f:
        s = json.load(f)
    with open(args.thesis) as f:
        t = json.load(f)
    missing = [k for k in REQUIRED_THESIS if not t.get(k)]
    if missing:
        sys.exit(f"thesis.json is missing: {', '.join(missing)}")
    for k in ("headline", "summary", "pillars", "variant"):
        if not t["thesis"].get(k):
            sys.exit(f"thesis.thesis.{k} is empty")
    if not s["balance_check_ok"] or s["formula_errors"]:
        sys.exit("summary.json shows a failed model check; fix the model before publishing")
    d = s["dcf"]
    if not d.get("share_price"):
        sys.exit("share_price is 0 in the model; set it in assumptions.json and rebuild")
    pid = f"{t['date']}-{s['ticker']}"
    fin = {"years": s["years"], "n_hist": s["n_hist"], "income_statement": s["income_statement"],
           "balance_sheet": s["balance_sheet"], "cash_flow": s["cash_flow"]}
    entry = {
        "id": pid, "date": t["date"], "ticker": s["ticker"], "name": t.get("name") or s["name"],
        "exchange": t.get("exchange"), "sector": t["sector"], "stance": t["stance"],
        "price": d["share_price"], "price_as_of": d["price_as_of"], "market_cap_m": d["market_cap"],
        "fair_value": {"perpetuity": d["value_per_share_perpetuity"], "exit": d["value_per_share_exit"],
                       "blended": d["value_per_share_blended"]},
        "upside": d["upside_blended"],
        "dcf": {"wacc": d["wacc"], "terminal_growth": d["terminal_growth"], "exit_multiple": d["exit_multiple"],
                "tv_pct_ev": d["tv_pct_ev_perpetuity"], "ev_ebitda_next": d["ev_ebitda_next"], "pe_next": d["pe_next"]},
        "sensitivity": d["sensitivity"],
        "thesis": t["thesis"], "catalysts": t["catalysts"], "risks": t["risks"],
        "kill_criteria": t["kill_criteria"], "valuation_note": t["valuation_note"],
        "model_note": t.get("model_note", ""), "sources": t["sources"],
        "financials": fin, "model_file": f"models/{pid}.xlsx",
    }
    with open(args.page) as f:
        html = f.read()
    start = html.index(PICKS_OPEN) + len(PICKS_OPEN)
    end = html.index("</script>", start)
    picks = json.loads(html[start:end])
    picks = [p for p in picks if p["id"] != pid] + [entry]
    picks.sort(key=lambda p: p["date"])
    blob = json.dumps(picks, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    with open(args.page, "w") as f:
        f.write(html[:start] + blob + html[end:])
    print(f"page now holds {len(picks)} pick(s); latest {pid}; publish {args.page} with files "
          f"{{\"{entry['model_file']}\": <path to the xlsx>}}")
    print("past tickers:", ", ".join(p["ticker"] for p in picks))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("page")
    g.add_argument("page")
    g.add_argument("workdir")
    g.add_argument("--thesis", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("ticker")
    f.add_argument("--out", required=True)
    b = sub.add_parser("build")
    b.add_argument("workdir")
    b.add_argument("--xlsx", required=True)
    args = p.parse_args()
    {"fetch": cmd_fetch, "build": cmd_build, "page": cmd_page}[args.cmd](args)


if __name__ == "__main__":
    main()
