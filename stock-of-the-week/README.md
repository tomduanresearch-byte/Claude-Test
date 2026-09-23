# The Friday Pick

An agent that picks one stock every Friday after the US close and publishes:

- an investment thesis,
- a linked three-statement model with bear, base and bull scenarios and a DCF
  valuation, as a downloadable Excel file.

Both go on one private page, which also keeps the archive of every past pick.

**Page:** <https://claude.ai/artifact/8QuVK4xBJmZVd6f6ACYLKt>

**Routine:** `trig_01WVGAtWfHFpcmMvqRCTjoRr`, which starts a fresh session each run.

## How it runs

A scheduled Routine fires every **Friday at 6 PM US Eastern** (`0 22 * * 5` in
UTC) and starts a fresh session. The cron is in UTC and doesn't follow daylight
saving: switch it to `0 23 * * 5` when clocks fall back in November, and back in
March. That session reads its runbook from the page and
follows it. The runbook is `procedure.md`, published on the page as
`tools/procedure.md`. It:

1. **Picks** a US-listed 10-K filer with a market cap above $2B. It skips
   financials, REITs and anything picked in the past 52 weeks, and rotates
   sectors.
2. **Pulls five years of financials** from the SEC's XBRL API, then checks them
   against the company's reported figures.
3. **Sets assumptions** from guidance, recent quarters and peers, and looks up
   the market inputs: price, 10-year Treasury yield and beta.
4. **Builds the model** with `build_model.py`.
5. **Writes the thesis:**
   - headline and summary,
   - 3–4 pillars,
   - where it differs from consensus,
   - catalysts, risks and kill criteria,
   - sources.
6. **Publishes** the pick onto the page and uploads the `.xlsx` next to it.

The fresh session has no repository checked out, so the runbook and the model
script are published on the page itself (`tools/`). **To change how the agent
works, edit the files here and republish them to the page.** The Routine's
prompt only points at them.

## The model

`build_model.py` builds a formula-driven workbook with these sheets:

| Sheet | What it holds |
| --- | --- |
| Cover | Outputs and checks: balance sheet balances, cash stays positive |
| Scenarios | The selector (cell C5): 1 = Bear, 2 = Base, 3 = Bull. Also each case's probability, terminal growth, exit multiple and narrative, and a side-by-side table of every scenario's outputs with the probability-weighted value. |
| Assumptions | For each scenario-driven line: a live row plus Bear/Base/Bull input rows. Also the shared drivers. |
| Income Statement | Includes a share-count schedule: stock-comp issuance, and buybacks retired at the current price grown at the cost of equity |
| Balance Sheet | Includes a debt schedule and checks |
| Cash Flow | Linked to the income statement and balance sheet |
| Ratios | ROIC, ROE, FCF margin and conversion, leverage, coverage, working-capital days |
| DCF | WACC build, unlevered FCF with an optional mid-year convention, perpetuity and exit-multiple values, trading multiples, and two sensitivity grids (WACC × growth, WACC × exit multiple) |
| Data | Every source figure and where it came from |

**How the statements link.** Cash on the balance sheet comes from the cash-flow
statement. Equity rolls forward on net income, stock comp, dividends and
buybacks. PP&E rolls forward on capex and D&A. Debt follows its schedule.

**Checks.** The build calculates each scenario in turn with LibreOffice. It fails
if any year of any scenario doesn't balance or any formula errors.

**Fair value** on the page is the probability-weighted value across the three
scenarios.

**The thesis is checked too.** The `page` step rejects a thesis that doesn't meet
all of these:

- every pillar has a numeric proof,
- every catalyst is dated,
- every kill criterion has a measurable threshold,
- it contains no filler phrases.

**Delivery.** Artifacts can't host `.xlsx` files, so the workbook is embedded in
the page. The download button rebuilds it through the page's `downloads`
permission.

```bash
pip install openpyxl            # plus: apt-get install -y libreoffice-calc
python3 build_model.py fetch MSFT --out work
# edit work/assumptions.json (scenarios + shared drivers + market inputs)
python3 build_model.py build work --xlsx work/model.xlsx
python3 build_model.py page page.html work --thesis work/thesis.json --xlsx work/model.xlsx
```

## Test run

`test-run/` holds the inputs and outputs of the first end-to-end test, on
Trimble (TRMB), 23 Sep 2026. It's published separately at
<https://claude.ai/artifact/LDQvDTSR4sR7PX6tr9BR1p>, so the live archive stays
clean.

This session couldn't reach the SEC API. The historicals were therefore collected
from Trimble's 10-K and earnings releases by web search, and checked against four
accounting identities. The derived items are listed in `thesis.json` →
`model_note`.

## Requirements on the environment

The environment's network policy must allow **`www.sec.gov`** and
**`data.sec.gov`**. Without them the weekly run stops and publishes nothing
rather than inventing numbers. Share prices, yields and news come through web
search.

## Not investment advice

The page and models are a research starting point generated by an automated
agent. Verify against the filings before acting on anything.
