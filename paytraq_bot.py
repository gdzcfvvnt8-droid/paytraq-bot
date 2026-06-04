#!/usr/bin/env python3
"""
PayTraq CSV Report Bot
- Sends back Excel with 3 sheets: Data, Summary, Questions
- Text summary in chat
"""

import logging
import io
import csv
import datetime
from collections import defaultdict
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = "8649650117:AAHLbzcGjPu-ei-S0tWMksEs-fvUs1wof-c"

ALLOWED_USERS = {
    563973148,
}

# ─── REGION & ACCOUNT HELPERS ─────────────────────────────────────────────────

def get_region(project: str) -> str:
    p = project.strip().upper()
    if p in ("AM - ARMENIA", "AM-ARMENIA", "ARMENIA", "AM"):
        return "Armenia"
    if p in ("AZ - AZERBAIJAN", "AZ-AZERBAIJAN", "AZERBAIJAN", "AZ"):
        return "Azerbaijan"
    if p in ("UZ-UZBEKISTAN", "UZ - UZBEKISTAN", "UZBEKISTAN", "UZ"):
        return "Uzbekistan"
    if p == "":
        return ""
    return "Europe / Other"

REGION_ORDER = ["Armenia", "Azerbaijan", "Uzbekistan", "Europe / Other"]

REGION_FLAGS = {
    "Armenia": "🇦🇲 Armenia",
    "Azerbaijan": "🇦🇿 Azerbaijan",
    "Uzbekistan": "🇺🇿 Uzbekistan",
    "Europe / Other": "🌍 Europe / Other",
}

def normalize_account(account: str) -> str:
    a = account.strip()
    al = a.lower()
    if "revolut" in al:
        if "eur" in al: return "Revolut EUR"
        if "usd" in al: return "Revolut USD"
        return "Revolut"
    if "stripe" in al:
        if "eur" in al: return "Stripe EUR"
        if "usd" in al: return "Stripe USD"
        return "Stripe"
    if "paysera" in al: return "Paysera"
    if "coop" in al: return "Coop Pank"
    return a

# ─── CSV PARSING ──────────────────────────────────────────────────────────────

def parse_csv(content: bytes) -> list:
    text = content.decode("utf-8-sig", errors="replace")
    delimiter = "\t" if "\t" in text[:2000] else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [{k.strip(): (v.strip() if v else "") for k, v in row.items()} for row in reader]

def parse_amount(value: str) -> float:
    v = value.replace(" ", "").replace("\xa0", "")
    if "," in v and "." in v:
        v = v.replace(",", "")
    elif "," in v:
        parts = v.split(",")
        v = v.replace(",", ".") if len(parts) == 2 and len(parts[1]) <= 2 else v.replace(",", "")
    try:
        return abs(float(v))
    except ValueError:
        return 0.0

# ─── EXCEL BUILDER ────────────────────────────────────────────────────────────

# Colors
C_HEADER    = "1F3864"  # dark blue
C_ARMENIA   = "FFE0E0"  # light red
C_AZER      = "E0F0FF"  # light blue
C_UZBEK     = "E0FFE0"  # light green
C_EUROPE    = "FFF8E0"  # light yellow
C_QUESTION  = "FFF0CC"  # light orange
C_TOTAL     = "D9E1F2"  # blue-grey
C_WHITE     = "FFFFFF"

REGION_COLORS = {
    "Armenia":       C_ARMENIA,
    "Azerbaijan":    C_AZER,
    "Uzbekistan":    C_UZBEK,
    "Europe / Other": C_EUROPE,
}

def header_style(cell, bg=C_HEADER):
    cell.font = Font(bold=True, color="FFFFFF", size=10)
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

def region_style(cell, region):
    color = REGION_COLORS.get(region, C_WHITE)
    cell.fill = PatternFill("solid", fgColor=color)
    cell.alignment = Alignment(vertical="center")

def total_style(cell):
    cell.font = Font(bold=True, size=10)
    cell.fill = PatternFill("solid", fgColor=C_TOTAL)
    cell.alignment = Alignment(horizontal="right", vertical="center")

def thin_border():
    s = Side(style="thin", color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)

def set_col_width(ws, col, width):
    ws.column_dimensions[get_column_letter(col)].width = width

def build_excel(rows: list) -> bytes:
    wb = openpyxl.Workbook()

    # ── Process data ──────────────────────────────────────────────────────────
    in_rows = []
    question_rows = []
    out_rows = []
    skip_draft = 0

    for r in rows:
        doc_no  = r.get("Document No.", "").strip()
        status  = r.get("Status", "").strip().lower()
        project = r.get("Project", "").strip()
        amount  = parse_amount(r.get("Amount", "0"))
        account = normalize_account(r.get("Account", ""))
        currency= r.get("Currency", "").strip().upper()
        region  = get_region(project)
        narration = r.get("Narration", "").strip().split("\n")[0][:80]
        partner = r.get("Business partner", "").strip() or narration

        if status == "draft":
            skip_draft += 1
            continue

        enriched = dict(r)
        enriched["_region"]   = region
        enriched["_account"]  = account
        enriched["_amount"]   = amount
        enriched["_partner"]  = partner

        if doc_no.upper().startswith("IN/"):
            if region == "":
                question_rows.append(enriched)
            else:
                in_rows.append(enriched)
        elif doc_no.upper().startswith("OUT/"):
            out_rows.append(enriched)

    # ── SHEET 1: Data ─────────────────────────────────────────────────────────
    ws_data = wb.active
    ws_data.title = "Data"

    headers = ["Date", "Document No.", "Region", "Business Partner", "Account", "Currency", "Amount", "Project", "Status", "Narration"]
    col_widths = [12, 18, 18, 30, 16, 10, 14, 20, 10, 40]

    ws_data.row_dimensions[1].height = 30
    for col, (h, w) in enumerate(zip(headers, col_widths), 1):
        cell = ws_data.cell(row=1, column=col, value=h)
        header_style(cell)
        set_col_width(ws_data, col, w)

    # Sort by region order then date
    region_sort = {r: i for i, r in enumerate(REGION_ORDER)}
    sorted_rows = sorted(in_rows, key=lambda r: (region_sort.get(r["_region"], 99), r.get("Date", "")))

    for row_i, r in enumerate(sorted_rows, 2):
        region = r["_region"]
        vals = [
            r.get("Date", ""),
            r.get("Document No.", ""),
            region,
            r["_partner"],
            r["_account"],
            r.get("Currency", ""),
            r["_amount"],
            r.get("Project", ""),
            r.get("Status", ""),
            r.get("Narration", "").split("\n")[0][:80],
        ]
        for col, val in enumerate(vals, 1):
            cell = ws_data.cell(row=row_i, column=col, value=val)
            region_style(cell, region)
            cell.border = thin_border()
            if col == 7:  # Amount
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")

    ws_data.freeze_panes = "A2"
    ws_data.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"

    # ── SHEET 2: Summary ──────────────────────────────────────────────────────
    ws_sum = wb.create_sheet("Summary")

    # Build summary data
    summary = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for r in in_rows:
        summary[r["_region"]][r.get("Currency","?").upper()][r["_account"]] += r["_amount"]

    ws_sum.column_dimensions["A"].width = 22
    ws_sum.column_dimensions["B"].width = 12
    ws_sum.column_dimensions["C"].width = 20
    ws_sum.column_dimensions["D"].width = 16

    # Title
    ws_sum.merge_cells("A1:D1")
    title_cell = ws_sum["A1"]
    title_cell.value = f"PayTraq Payment Summary — {datetime.date.today().strftime('%d.%m.%Y')}"
    title_cell.font = Font(bold=True, size=13, color="1F3864")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws_sum.row_dimensions[1].height = 28

    # Headers
    sum_headers = ["Region", "Currency", "Account / Source", "Amount"]
    for col, h in enumerate(sum_headers, 1):
        cell = ws_sum.cell(row=2, column=col, value=h)
        header_style(cell)
    ws_sum.row_dimensions[2].height = 22

    row_i = 3
    grand_totals = defaultdict(float)

    for region in REGION_ORDER:
        if region not in summary:
            continue
        color = REGION_COLORS.get(region, C_WHITE)
        region_total = defaultdict(float)

        for currency in sorted(summary[region].keys()):
            accounts = summary[region][currency]
            cur_total = sum(accounts.values())
            region_total[currency] += cur_total
            grand_totals[currency] += cur_total

            # Currency subtotal row
            for col, val in enumerate([region, currency, "TOTAL", cur_total], 1):
                cell = ws_sum.cell(row=row_i, column=col, value=val)
                cell.font = Font(bold=True, size=10)
                cell.fill = PatternFill("solid", fgColor=color)
                cell.border = thin_border()
                if col == 4:
                    cell.number_format = '#,##0.00'
                    cell.alignment = Alignment(horizontal="right")
            row_i += 1

            # Account rows
            for account, amt in sorted(accounts.items(), key=lambda x: -x[1]):
                for col, val in enumerate(["", "", account, amt], 1):
                    cell = ws_sum.cell(row=row_i, column=col, value=val)
                    cell.fill = PatternFill("solid", fgColor=color)
                    cell.border = thin_border()
                    cell.font = Font(size=10)
                    if col == 4:
                        cell.number_format = '#,##0.00'
                        cell.alignment = Alignment(horizontal="right")
                row_i += 1

        row_i += 1  # blank between regions

    # Grand total
    for col, val in enumerate(["GRAND TOTAL", "", "", ""], 1):
        cell = ws_sum.cell(row=row_i, column=col, value=val)
        total_style(cell)
    for currency, total in sorted(grand_totals.items()):
        row_i += 1
        for col, val in enumerate(["", currency, "", total], 1):
            cell = ws_sum.cell(row=row_i, column=col, value=val)
            total_style(cell)
            if col == 4:
                cell.number_format = '#,##0.00'

    ws_sum.freeze_panes = "A3"

    # ── SHEET 3: Questions ────────────────────────────────────────────────────
    ws_q = wb.create_sheet("❓ Questions")

    q_headers = ["Date", "Document No.", "Account", "Currency", "Amount", "Business Partner / Narration", "Status"]
    q_widths =  [12,     18,             16,         10,         14,       50,                             10]

    ws_q.row_dimensions[1].height = 28
    for col, (h, w) in enumerate(zip(q_headers, q_widths), 1):
        cell = ws_q.cell(row=1, column=col, value=h)
        header_style(cell, bg="C55A11")  # orange header
        set_col_width(ws_q, col, w)

    if not question_rows:
        cell = ws_q.cell(row=2, column=1, value="✅ Все входящие платежи имеют проект")
        cell.font = Font(italic=True, color="888888")
    else:
        for row_i, r in enumerate(sorted(question_rows, key=lambda x: x.get("Date","")), 2):
            vals = [
                r.get("Date", ""),
                r.get("Document No.", ""),
                r["_account"],
                r.get("Currency", ""),
                r["_amount"],
                r["_partner"],
                r.get("Status", ""),
            ]
            for col, val in enumerate(vals, 1):
                cell = ws_q.cell(row=row_i, column=col, value=val)
                cell.fill = PatternFill("solid", fgColor=C_QUESTION)
                cell.border = thin_border()
                cell.font = Font(size=10)
                if col == 5:
                    cell.number_format = '#,##0.00'
                    cell.alignment = Alignment(horizontal="right")

        # Total for questions
        q_total = defaultdict(float)
        for r in question_rows:
            q_total[r.get("Currency","?").upper()] += r["_amount"]
        row_i = len(question_rows) + 3
        ws_q.cell(row=row_i, column=1, value="ИТОГО без проекта:").font = Font(bold=True)
        for currency, total in sorted(q_total.items()):
            row_i += 1
            cell_c = ws_q.cell(row=row_i, column=4, value=currency)
            cell_a = ws_q.cell(row=row_i, column=5, value=total)
            cell_a.number_format = '#,##0.00'
            cell_a.font = Font(bold=True)

    ws_q.freeze_panes = "A2"
    ws_q.auto_filter.ref = f"A1:{get_column_letter(len(q_headers))}1"

    # ── Save to bytes ─────────────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read(), len(in_rows), len(question_rows), skip_draft

# ─── TEXT REPORT ──────────────────────────────────────────────────────────────

def build_text_report(rows: list) -> str:
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    empty_in_count = 0
    empty_totals = defaultdict(float)
    out_count = 0
    out_by_currency = defaultdict(float)
    skip_draft = 0

    for r in rows:
        doc_no   = r.get("Document No.", "").strip()
        status   = r.get("Status", "").strip().lower()
        project  = r.get("Project", "").strip()
        currency = r.get("Currency", "?").upper().strip()
        amount   = parse_amount(r.get("Amount", "0"))
        account  = normalize_account(r.get("Account", ""))

        if status == "draft":
            skip_draft += 1
            continue

        if doc_no.upper().startswith("OUT/"):
            out_count += 1
            out_by_currency[currency] += amount
            continue

        if not doc_no.upper().startswith("IN/") or amount == 0:
            continue

        region = get_region(project)
        if region == "":
            empty_in_count += 1
            empty_totals[currency] += amount
        else:
            data[region][currency][account] += amount

    lines = ["📊 *Сводка по входящим оплатам PayTraq*\n"]
    grand_total = defaultdict(float)

    for region in REGION_ORDER:
        if region not in data:
            continue
        flag = REGION_FLAGS[region]
        lines.append(f"*{flag}*")
        for currency in sorted(data[region].keys()):
            accounts = data[region][currency]
            cur_total = sum(accounts.values())
            grand_total[currency] += cur_total
            lines.append(f"  💵 *{currency}*: `{cur_total:,.2f}`")
            for account, amt in sorted(accounts.items(), key=lambda x: -x[1]):
                lines.append(f"    • {account}: `{amt:,.2f}`")
        lines.append("")

    if empty_in_count:
        lines.append(f"*⚠️ Без проекта: {empty_in_count} платежей*")
        for currency, total in sorted(empty_totals.items()):
            grand_total[currency] += total
            lines.append(f"  💵 *{currency}*: `{total:,.2f}`")
        lines.append("")

    lines.append("─────────────────")
    lines.append("*💰 Итого входящих:*")
    for currency, total in sorted(grand_total.items()):
        lines.append(f"  {currency}: `{total:,.2f}`")

    if out_count:
        lines.append(f"\n*↗️ Исходящих: {out_count}*")
        for currency, amt in sorted(out_by_currency.items()):
            lines.append(f"  {currency}: `{amt:,.2f}`")

    if skip_draft:
        lines.append(f"\n_ℹ️ Draft пропущено: {skip_draft}_")

    return "\n".join(lines)

# ─── BOT HANDLERS ─────────────────────────────────────────────────────────────

def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USERS or user_id in ALLOWED_USERS

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text(
        f"👋 Привет! Отправь CSV из PayTraq — получишь сводку в чате и Excel-файл с 3 листами:\n\n"
        f"• *Data* — все входящие с регионом\n"
        f"• *Summary* — сводная таблица\n"
        f"• *Questions* — платежи без проекта\n\n"
        f"_Твой ID: `{update.effective_user.id}`_",
        parse_mode="Markdown"
    )

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👤 ID: `{update.effective_user.id}`", parse_mode="Markdown")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("⚠️ Отправь файл в формате CSV.")
        return

    await update.message.reply_text("⏳ Обрабатываю, генерирую Excel...")

    try:
        file = await context.bot.get_file(doc.file_id)
        content = bytes(await file.download_as_bytearray())
        rows = parse_csv(content)

        if not rows:
            await update.message.reply_text("❌ CSV пустой.")
            return

        # Text summary
        report = build_text_report(rows)
        chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="Markdown")

        # Excel file
        excel_bytes, in_count, q_count, skip_draft = build_excel(rows)
        date_str = datetime.date.today().strftime("%Y-%m-%d")
        filename = f"paytraq_report_{date_str}.xlsx"

        await update.message.reply_document(
            document=io.BytesIO(excel_bytes),
            filename=filename,
            caption=f"📎 Excel-отчёт: {in_count} входящих, {q_count} без проекта"
                    + (f", {skip_draft} Draft пропущено" if skip_draft else "")
        )

    except Exception as e:
        logging.exception("Error")
        await update.message.reply_text(f"❌ Ошибка:\n`{e}`", parse_mode="Markdown")

async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("📎 Отправь CSV-файл. /start для справки.")

def main():
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown))
    logging.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
