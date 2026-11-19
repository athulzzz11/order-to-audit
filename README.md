# Order-to-Audit

**One system for a small snack factory, from a salesman's order to the auditor's monthly report.**
Every transaction is entered once, where it happens, and flows into stock, production, costing and accounts.

```
SALES LOOP       Salesman (phone, works offline) → Admin verifies + stock check → GST invoice → Dispatch (FEFO) ─┐ FG stock ↓
PRODUCTION LOOP  Purchase → Raw stock → Issue against BOM → Run output ─────────────────────────────────────────┘ FG stock ↑
BOTH             → Stock ledger → Variance → Cost per packet → Reports (Excel / CSV / PDF)
```

**Live demo:** _link added after deployment_ · logins below, password `demo123` (free hosting sleeps when idle, so the first load can take ~50 s)

---

## 1. Benchmark result (section 5): 11 / 11

Run it yourself: `python -m app.benchmark` (it resets the DB, plays the 9 steps and prints this table), or log in and open **Benchmark**, which checks the live database after you click through the steps by hand.

| Check | Expected | This system |
| --- | --- | --- |
| Flour closing balance | 120 kg | **120 kg** ✅ |
| Oil closing balance | 19.5 L | **19.5 l** ✅ |
| Pouches closing balance | 306 | **306 pcs** ✅ |
| Finished goods after run | 238 | **238 packets** ✅ |
| Finished goods after Retailer A dispatch | 88 | **88 packets** ✅ |
| Yield variance | 188 vs 200, −6%, flagged | **−6.00%, flagged** ✅ |
| Oil consumption variance | +5%, flagged | **+5.00%, flagged** ✅ |
| Retailer A invoice | ₹7,500 + ₹187.50 + ₹187.50 = ₹7,875 | **₹7,875.00** (CGST + SGST) ✅ |
| Retailer A outstanding | ₹3,875 | **₹3,875.00** ✅ |
| Distributor B order | shortage of 32, IGST if invoiced | **Short by 32, "make 1 batch"; invoice blocked; IGST ₹300 previewed** ✅ |
| Cost per packet | your figure | **₹31.14** (margin ₹18.86, 37.7%) |

**How ₹31.14 is worked out** (valuation: moving weighted average; overheads: charged to the run they were entered on)

| Cost line | Working | ₹ |
| --- | --- | ---: |
| Flour | (100 kg × 42 + 60 kg × 45) ÷ 160 = 43.125/kg × 40 kg | 1,725.00 |
| Oil | 10.5 L × 140 (actual issued, incl. the 0.5 L over) | 1,470.00 |
| Spice mix | 1.2 kg × 200 | 240.00 |
| Pouches | 194 × 2.50 (188 used + 6 damaged) | 485.00 |
| Cartons | 9 × 15 | 135.00 |
| Labour + gas/power | 1,200 + 600 | 1,800.00 |
| **Total ÷ 188 good packets** | 5,855 ÷ 188 | **31.14** |

With FIFO instead, flour would cost 40 × 42 = ₹1,680 and the packet ₹30.90. The ₹0.24 gap is the newer, dearer flour.

**Where my output goes beyond the table (all deliberate):**
- **Pouches are also flagged (+3.19%).** Packaging is planned per *good packet*: 188 packets need 188 pouches, and 194 were used. Six damaged pouches is 3.19%, above the 3% tolerance, so it is flagged. Planning per batch (200) would have hidden the damage behind the low yield.
- **Cartons show 0% variance.** 188 packets fill 9 whole cartons (8 packets stay loose), so the plan for count items is rounded down to whole pieces: 9 planned, 9 used.
- **Distributor B:** the order can be *approved*, but *invoicing is blocked* until stock covers it. The screen shows the shortage (32), the batches needed (1), and the IGST the invoice would carry (₹6,000 + ₹300).

---

## 2. Run it

**Hosted:** `render.yaml` deploys this repo to Render's free plan in one click (New → Blueprint → pick the repo).

**Docker (PostgreSQL):**
```bash
docker compose up --build        # → http://localhost:8000
```

**Local (SQLite, no setup, Python 3.11+):**

Double-click `run.bat` on Windows, or run `./run.sh` on Mac/Linux. Or do it step by step:
```bash
pip install -r requirements.txt
python -m uvicorn app.main:app   # → http://localhost:8000  (first start loads the section 5 data automatically)
                                 #   salesman app: /m/ (open it on a phone, or F12 → phone icon)
python -m app.seed               # optional: wipe and reload the opening data
python -m pytest                 # 17 tests: units, GST, ledger, variance, FEFO, credit notes, full benchmark over HTTP
```
Tested on Python 3.11, 3.12 and 3.13, with SQLite and PostgreSQL 16.

**Test logins** (password `demo123` for all; click a name on the login page to fill it in)

| Username | Role | Lands on |
| --- | --- | --- |
| `salesA`, `salesB` | Salesman | Mobile order app `/m/` (sees only own customers and orders) |
| `admin` | Factory admin | Orders → invoices → dispatch → payments |
| `prod` | Production supervisor | Purchases, production runs, overheads |
| `audit` | Accountant / auditor (read-only) | Reports and exports, audit log |
| `owner` | Owner | Dashboard (and read access everywhere) |

**Demo controls:** owner/admin can open **Benchmark → Reset to opening data** before recording, or **Play all 9 steps** to jump to the end state.

---

## 3. Stack and why

**Python · FastAPI · SQLAlchemy · PostgreSQL (SQLite locally) · server-rendered Jinja pages · one vanilla-JS PWA for salesmen.**
The hard part of this problem is correct bookkeeping, not UI, so I chose a small, readable stack where the business rules live in plain Python functions that the web pages, the CLI benchmark and the tests all call. Server-rendered pages keep the desktop screens simple. The salesman app is the one place that needs JavaScript, because it must work offline.

---

## 4. What is built (against section 6)

**Must have: all done**
- [x] Login with 5 roles; every route checks role (salesman can't open admin pages, auditor can't post anything)
- [x] Product, customer, supplier, raw/packaging item and **versioned BOM** masters
- [x] Mobile-friendly order entry (three taps: customer → quantity → submit) and admin approval with edits (logged)
- [x] GST tax invoice PDF: GSTIN, HSN, place of supply, CGST/SGST vs IGST, FY numbering `INV/2026-27/0001`, amount in words
- [x] Production run that moves raw stock down and finished stock up automatically
- [x] Planned vs actual consumption and yield, with variance %
- [x] Stock summary + stock ledger (every movement → source document)
- [x] Sales register and stock report, exported to **Excel, CSV and PDF**

**Should have: all done**
- [x] Purchases and supplier records
- [x] Payments against an invoice or on account; outstanding with **ageing** (0-30 / 31-60 / 61-90 / 90+)
- [x] Cost per packet with overheads (per run, or month-level shared by output)
- [x] GST summary arranged by **GSTR-1 tables 4A / 7 / 9B / 12 and GSTR-3B 3.1(a)**
- [x] Variance alerts beyond a configurable tolerance (3%)
- [x] Owner dashboard: today's orders, stock, variance alerts, cost per packet, dues, unfillable orders

**Stretch goals done**
- [x] **Offline order capture with sync.** Orders queue on the phone and are sent when the network returns. Each carries a UUID, so a resend never creates a duplicate.
- [x] **Batch and expiry tracking with FEFO dispatch.** Each run creates a batch (`MUR-261118-01`, best before +90 days), and dispatch takes the oldest expiry first. The invoice PDF lists the batches shipped.
- [x] **Reorder levels and low-stock alerts** on the stock page and dashboard
- [x] **Production planning from shortages:** "short by 32 → make 1 batch"
- [ ] Tally export, e-invoice/e-way bill, GPS visit log, Tamil UI: not done (see gaps)

**Also built in:** credit notes that reverse an invoice and return the same batches to stock; reversing a production run; physical-count adjustments; an audit log of every create/edit/approve/cancel with user and time; signed customer links for the invoice PDF (WhatsApp/email share).

---

## 5. Assumptions

1. **Opening finished goods (50 packets) are valued at ₹30.00/packet.** The problem gives no cost for them. This affects stock value only, not the run's cost per packet.
2. **Opening stock is dated the 1st of the current month,** so all the month's reports show opening → closing.
3. **Packaging is planned per good packet; ingredients per batch** (see section 1).
4. **Material cost includes wastage.** Damaged pouches and spilt oil are real costs, so they land in the run's cost per packet.
5. **Stock is valued at cost before GST.** Input GST is claimable as credit, so it is not part of the cost of stock.
6. **Invoice = goods leaving.** An invoice reserves stock until dispatch. You cannot invoice more than is available (on hand − reserved), which prevents billing goods you don't have.
7. **GST is computed per line and rounded half-up to the paisa;** invoice totals are the sum of the lines. No round-off to the rupee, because the benchmark expects ₹7,875.00 exactly.
8. **All customer GSTINs, names and phone numbers are invented** (dummy data, as required).
9. **HSN 21069099** (namkeen/savoury snacks) at 5%, as the problem statement says.

## 6. Known gaps

- No Tally XML export (the Excel exports can be imported into Tally with a mapping, but I did not build a native one).
- Partial dispatch and partial returns are not supported. A credit note reverses a whole invoice.
- One price list. There are no customer-specific prices or schemes, but admin can edit the price per order line before approval.
- Purchases have no supplier payables or ageing (only receivables).
- Work-in-progress is not modelled as a separate stock location. Ingredients leave raw stock at run start, and packets arrive at completion.
- The PWA caches the app and master data. A salesman must log in online once.

---

## 7. Code map

```
app/
  models.py            all tables in one file, read top to bottom
  units.py  money.py   unit conversion (kg/g/l/ml/pcs/packet/carton) and paise maths
  services/            the business rules, no web code
    stock.py           the ledger: post(), balance(), weighted-average value, FEFO
    production.py      start/complete/cancel run, planned vs actual, variance
    sales.py           orders, stock check, invoice, dispatch, credit note, payments, ageing
    purchase.py  costing.py  gst.py  reports.py  common.py (numbering, audit log)
  web/                 thin FastAPI routes per area + role checks (deps.py)
  templates/  static/  pages; mobile.html + sw.js = the offline salesman app
  benchmark.py         section 5 scenario + live checks     seed.py   master data
tests/                 17 tests (pytest)
docs/DESIGN.md         data model, stock and BOM maths, costing method
docs/DEMO_SCRIPT.md    the 8-minute walkthrough
```
