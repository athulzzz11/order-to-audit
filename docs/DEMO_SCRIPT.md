# Demo video script (target 7½ minutes)

Before recording: log in as `owner` → **Benchmark → Reset to opening data**. Open two windows: a desktop browser, and the phone (or Chrome DevTools device mode at 390 px) on `/m/`.

| Time | Step | Who / where | Show and say |
| --- | --- | --- | --- |
| 0:00 | Intro | Login page | "One system, five roles. Every transaction is entered once." Show the role table. |
| 0:30 | Opening position | `prod` → **Stock** | Flour 100 kg, oil 30 L, spice 5 kg, pouches 500, cartons 30, Murukku 50. "These come from the ledger, not a typed number." Click flour → ledger. |
| 1:00 | **1 · Purchase** | `prod` → **Purchases** | Flour, 60, kg, ₹45 → Save. Stock shows flour 160 kg. |
| 1:30 | **2 · Start run** | **Production → New run** | Quantities are pre-filled from the BOM. Change oil to **10.5**. Start. "Raw stock went down automatically." |
| 2:15 | **3 · Complete run** | Run page | Good packets **188**. Pouches auto-fill 188, damaged **6**. Cartons auto-fill **9**. Complete. Point at the variance table: oil +5% flagged, yield −6% flagged, pouches +3.19% (damage). Batch number and expiry. |
| 3:15 | **4 · Mobile order** | Phone, `salesA` | Tap Retailer A, type 150 packets, Submit, "Order SO/… received". *Offline trick:* turn on airplane mode (DevTools → Offline), place a test order, show "waiting for network", turn it back on, and it syncs. |
| 4:00 | **5 · Approve → invoice → dispatch** | `admin` → **Orders** | Open the order: stock check "Covered". GST preview CGST + SGST. Approve → Raise GST invoice → **₹7,875.00**. Download the PDF. Show the WhatsApp share. Record dispatch: batches taken are MUR-OPENING ×50 + new batch ×100 (FEFO). Stock is 88. |
| 5:00 | **6 · Shortage** | Phone `salesB` → admin | Distributor B, 6 cartons (= 120 packets). Admin: **Short by 32, make 1 batch**, preview shows **IGST** (Karnataka). Approve; the invoice button is disabled, and pressing it shows the error. |
| 5:45 | **7 · Payment** | **Payments** | Retailer A, against INV/…/0001, ₹4,000 → "Outstanding now ₹3,875.00". |
| 6:05 | **8 · Overheads** | `prod` → the run | Add labour 1,200 and gas/power 600 → **₹31.14 per packet, margin 37.7%**. Explain weighted average in one line. |
| 6:35 | **9 · Reports** | `audit` → **Reports** | Open the GST summary (GSTR-1/3B shape), stock summary (identity "OK" for each item), production report, outstanding with ageing. Click **Excel** on one. |
| 7:10 | Wrap | **Benchmark** | 11/11 green. "The same checks run as an automated test." Show `python -m pytest` passing. |
