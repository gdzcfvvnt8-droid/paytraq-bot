#!/usr/bin/env python3
"""
PayTraq CSV Report Bot
"""

import logging
import io
import csv
from collections import defaultdict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = "8649650117:AAHLbzcGjPu-ei-S0tWMksEs-fvUs1wof-c"

ALLOWED_USERS = {
    563973148,
}

def get_region(project: str) -> str:
    p = project.strip().upper()
    if p in ("AM - ARMENIA", "AM-ARMENIA", "ARMENIA", "AM"):
        return "🇦🇲 Armenia"
    if p in ("AZ - AZERBAIJAN", "AZ-AZERBAIJAN", "AZERBAIJAN", "AZ"):
        return "🇦🇿 Azerbaijan"
    if p in ("UZ-UZBEKISTAN", "UZ - UZBEKISTAN", "UZBEKISTAN", "UZ"):
        return "🇺🇿 Uzbekistan"
    if p == "":
        return "__EMPTY__"
    return "🌍 Europe / Other"

def normalize_account(account: str) -> str:
    """Normalize account names for consistent grouping."""
    a = account.strip()
    a_lower = a.lower()
    if 'revolut' in a_lower:
        if 'eur' in a_lower:
            return 'Revolut EUR'
        if 'usd' in a_lower:
            return 'Revolut USD'
        return 'Revolut'
    if 'stripe' in a_lower:
        if 'eur' in a_lower:
            return 'Stripe EUR'
        if 'usd' in a_lower:
            return 'Stripe USD'
        return 'Stripe'
    if 'paysera' in a_lower:
        return 'Paysera'
    if 'coop' in a_lower:
        return 'Coop Pank'
    return a

REGION_ORDER = ["🇦🇲 Armenia", "🇦🇿 Azerbaijan", "🇺🇿 Uzbekistan", "🌍 Europe / Other"]

def parse_csv(content: bytes) -> list:
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:2000]
    delimiter = "\t" if "\t" in sample else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for row in reader:
        rows.append({k.strip(): (v.strip() if v else "") for k, v in row.items()})
    return rows

def parse_amount(value: str) -> float:
    # Handle formats: 1234.56 / 1,234.56 / 1 234,56 / -1234.56
    v = value.replace(" ", "").replace("\xa0", "")
    # If both comma and dot present, comma is thousands separator
    if "," in v and "." in v:
        v = v.replace(",", "")
    elif "," in v:
        # Could be decimal separator (European) or thousands
        parts = v.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            v = v.replace(",", ".")
        else:
            v = v.replace(",", "")
    try:
        return abs(float(v))  # always positive — incoming amounts
    except ValueError:
        return 0.0

def build_report(rows: list) -> str:
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    empty_in = defaultdict(lambda: defaultdict(float))
    empty_in_rows = []  # store details for unrecognized incoming

    out_count = 0
    out_by_currency = defaultdict(float)

    skip_draft = 0
    skip_zero = 0
    skip_no_docno = 0
    unknown_projects = set()  # projects that went to Europe/Other

    for row in rows:
        doc_no   = row.get("Document No.", "").strip()
        project  = row.get("Project", "").strip()
        currency = row.get("Currency", "?").upper().strip()
        account  = normalize_account(row.get("Account", "?"))
        amount_s = row.get("Amount", "0").strip()
        status   = row.get("Status", "").strip()
        date     = row.get("Date", "").strip()
        partner  = row.get("Business partner", "").strip()

        # Skip drafts
        if status.lower() == "draft":
            skip_draft += 1
            continue

        is_incoming = doc_no.upper().startswith("IN/")
        is_outgoing = doc_no.upper().startswith("OUT/")

        if is_outgoing:
            amount = parse_amount(amount_s)
            out_count += 1
            out_by_currency[currency] += amount
            continue

        if not is_incoming:
            skip_no_docno += 1
            continue

        amount = parse_amount(amount_s)
        if amount == 0:
            skip_zero += 1
            continue

        region = get_region(project)

        if region == "__EMPTY__":
            empty_in[currency][account] += amount
            empty_in_rows.append({
                "date": date,
                "doc": doc_no,
                "partner": partner or row.get("Narration", "").strip().split(chr(10))[0][:60],
                "amount": amount,
                "currency": currency,
                "account": account,
            })
        else:
            if region == "🌍 Europe / Other" and project:
                unknown_projects.add(project)
            data[region][currency][account] += amount

    lines = ["📊 *Сводка по входящим оплатам PayTraq*\n"]

    grand_total = defaultdict(float)

    for region in REGION_ORDER:
        if region not in data:
            continue
        lines.append(f"*{region}*")
        for currency in sorted(data[region].keys()):
            accounts = data[region][currency]
            cur_total = sum(accounts.values())
            grand_total[currency] += cur_total
            lines.append(f"  💵 *{currency}*: `{cur_total:,.2f}`")
            for account, amt in sorted(accounts.items(), key=lambda x: -x[1]):
                lines.append(f"    • {account}: `{amt:,.2f}`")
        lines.append("")

    # Empty project — detailed
    if empty_in_rows:
        lines.append(f"*⚠️ Входящие без проекта ({len(empty_in_rows)} платежей)*")
        for currency in sorted(empty_in.keys()):
            accounts = empty_in[currency]
            cur_total = sum(accounts.values())
            grand_total[currency] += cur_total
            lines.append(f"  💵 *{currency}*: `{cur_total:,.2f}`")
            for account, amt in sorted(accounts.items(), key=lambda x: -x[1]):
                lines.append(f"    • {account}: `{amt:,.2f}`")
        lines.append("")
        lines.append("  _Детали:_")
        for r in empty_in_rows[:20]:  # max 20 rows
            lines.append(f"  `{r['date']}` {r['doc']} | {r['partner'] or '—'} | {r['amount']:,.2f} {r['currency']} | {r['account']}")
        if len(empty_in_rows) > 20:
            lines.append(f"  _...и ещё {len(empty_in_rows)-20} платежей_")
        lines.append("")

    lines.append("─────────────────")
    lines.append("*💰 Итого входящих:*")
    for currency, total in sorted(grand_total.items()):
        lines.append(f"  {currency}: `{total:,.2f}`")

    if out_count > 0:
        lines.append(f"\n*↗️ Исходящих: {out_count}*")
        for currency, amt in sorted(out_by_currency.items()):
            lines.append(f"  {currency}: `{amt:,.2f}`")

    # Debug info
    debug = []
    if skip_draft:
        debug.append(f"Draft пропущено: {skip_draft}")
    if skip_zero:
        debug.append(f"Нулевые суммы: {skip_zero}")
    if skip_no_docno:
        debug.append(f"Без номера документа: {skip_no_docno}")
    if debug:
        lines.append(f"\n_ℹ️ {' | '.join(debug)}_")

    return "\n".join(lines)

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        f"👋 Привет! Отправь CSV-файл из PayTraq — получишь сводку по регионам, валютам и источникам.\n\n"
        f"_Твой Telegram ID: `{update.effective_user.id}`_",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "📎 Отправь CSV-файл из PayTraq.\n\n"
        "Бот покажет разбивку:\n"
        "• 🇦🇲 Armenia\n• 🇦🇿 Azerbaijan\n• 🇺🇿 Uzbekistan\n• 🌍 Europe / Other\n"
        "• ⚠️ Входящие без проекта (с деталями)\n• ↗️ Исходящие (количество и суммы)\n\n"
        "Статус Draft автоматически исключается.",
        parse_mode="Markdown"
    )

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"👤 {update.effective_user.full_name}\nID: `{uid}`", parse_mode="Markdown")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ У тебя нет доступа.")
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("⚠️ Отправь файл в формате CSV.")
        return

    await update.message.reply_text("⏳ Обрабатываю...")

    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        rows = parse_csv(bytes(content))

        if not rows:
            await update.message.reply_text("❌ CSV пустой или не удалось прочитать.")
            return

        report = build_report(rows)

        if len(report) <= 4096:
            await update.message.reply_text(report, parse_mode="Markdown")
        else:
            chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode="Markdown")

    except Exception as e:
        logging.exception("Error")
        await update.message.reply_text(f"❌ Ошибка:\n`{e}`", parse_mode="Markdown")

async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text("📎 Отправь CSV-файл. /help для справки.")

def main():
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown))
    logging.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
