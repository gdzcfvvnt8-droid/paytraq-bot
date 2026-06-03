#!/usr/bin/env python3
"""
PayTraq CSV Report Bot
Sends a CSV file → gets breakdown by region, currency, and account source.
"""

import logging
import io
import csv
from collections import defaultdict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ─── CONFIG ───────────────────────────────────────────────────────────────────

BOT_TOKEN = "8649650117:AAHLbzcGjPu-ei-S0tWMksEs-fvUs1wof-c"

ALLOWED_USERS = {
    563973148,  # Admin
}

# ─── REGION MAPPING ───────────────────────────────────────────────────────────

def get_region(project: str) -> str:
    p = project.strip().upper()
    # Armenia
    if p in ("AM - ARMENIA", "AM-ARMENIA", "ARMENIA", "AM"):
        return "🇦🇲 Armenia"
    # Azerbaijan
    if p in ("AZ - AZERBAIJAN", "AZ-AZERBAIJAN", "AZERBAIJAN", "AZ"):
        return "🇦🇿 Azerbaijan"
    # Uzbekistan
    if p in ("UZ-UZBEKISTAN", "UZ - UZBEKISTAN", "UZBEKISTAN", "UZ"):
        return "🇺🇿 Uzbekistan"
    # Empty project — special bucket
    if p == "":
        return "__EMPTY__"
    # Everything else → Europe / Other
    return "🌍 Europe / Other"

REGION_ORDER = ["🇦🇲 Armenia", "🇦🇿 Azerbaijan", "🇺🇿 Uzbekistan", "🌍 Europe / Other"]

# ─── CSV PARSING ──────────────────────────────────────────────────────────────

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
    v = value.replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0

# ─── REPORT GENERATION ────────────────────────────────────────────────────────

def build_report(rows: list) -> str:
    # Structure: region → currency → account → total
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))

    # Empty project incoming: currency → account → total
    empty_in = defaultdict(lambda: defaultdict(float))
    empty_in_count = 0

    # Outgoing stats
    out_count = 0
    out_by_currency = defaultdict(float)

    total_in = 0

    for row in rows:
        doc_no   = row.get("Document No.", "")
        project  = row.get("Project", "").strip()
        currency = row.get("Currency", "?").upper()
        account  = row.get("Account", "?")
        amount_s = row.get("Amount", "0")
        status   = row.get("Status", "").lower()

        if status in ("cancelled", "void", "voided"):
            continue

        amount = parse_amount(amount_s)

        is_incoming = doc_no.upper().startswith("IN/")
        is_outgoing = doc_no.upper().startswith("OUT/")

        # Outgoing — just count
        if is_outgoing:
            out_count += 1
            out_by_currency[currency] += amount
            continue

        if not is_incoming:
            continue

        if amount == 0:
            continue

        total_in += 1

        region = get_region(project)

        if region == "__EMPTY__":
            empty_in_count += 1
            empty_in[currency][account] += amount
        else:
            data[region][currency][account] += amount

    if total_in == 0 and empty_in_count == 0:
        return "⚠️ Не найдено входящих платежей. Проверь формат CSV."

    lines = ["📊 *Сводка по входящим оплатам PayTraq*\n"]

    grand_total: dict = defaultdict(float)

    for region in REGION_ORDER:
        if region not in data:
            continue
        lines.append(f"*{region}*")
        currencies = sorted(data[region].keys())
        for currency in currencies:
            accounts = data[region][currency]
            cur_total = sum(accounts.values())
            grand_total[currency] += cur_total
            lines.append(f"  💵 *{currency}*: `{cur_total:,.2f}`")
            for account, amt in sorted(accounts.items(), key=lambda x: -x[1]):
                lines.append(f"    • {account}: `{amt:,.2f}`")
        lines.append("")

    # Empty project block
    if empty_in_count > 0:
        lines.append(f"*⚠️ Без проекта ({empty_in_count} платежей)*")
        for currency in sorted(empty_in.keys()):
            accounts = empty_in[currency]
            cur_total = sum(accounts.values())
            grand_total[currency] += cur_total
            lines.append(f"  💵 *{currency}*: `{cur_total:,.2f}`")
            for account, amt in sorted(accounts.items(), key=lambda x: -x[1]):
                lines.append(f"    • {account}: `{amt:,.2f}`")
        lines.append("")

    # Grand total incoming
    lines.append("─────────────────")
    lines.append("*💰 Итого входящих:*")
    for currency, total in sorted(grand_total.items()):
        lines.append(f"  {currency}: `{total:,.2f}`")

    # Outgoing summary
    if out_count > 0:
        lines.append(f"\n*↗️ Исходящих платежей: {out_count}*")
        for currency, amt in sorted(out_by_currency.items()):
            lines.append(f"  {currency}: `{amt:,.2f}`")

    return "\n".join(lines)

# ─── BOT HANDLERS ─────────────────────────────────────────────────────────────

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    your_id = update.effective_user.id
    await update.message.reply_text(
        f"👋 Привет! Я обрабатываю выгрузки PayTraq.\n\n"
        f"Отправь мне CSV-файл с оплатами — получишь сводку по регионам, валютам и источникам.\n\n"
        f"_Твой Telegram ID: `{your_id}`_",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "📎 Просто отправь CSV-файл из PayTraq.\n\n"
        "Бот покажет разбивку:\n"
        "• 🇦🇲 Armenia\n"
        "• 🇦🇿 Azerbaijan\n"
        "• 🇺🇿 Uzbekistan\n"
        "• 🌍 Europe / Other\n"
        "• ⚠️ Без проекта (пустые входящие)\n"
        "• ↗️ Исходящие (только количество)\n\n"
        "Внутри каждого блока — суммы по валютам и счетам.",
        parse_mode="Markdown"
    )

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.full_name
    await update.message.reply_text(
        f"👤 {name}\nТвой Telegram ID: `{uid}`",
        parse_mode="Markdown"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ У тебя нет доступа к этому боту.")
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("⚠️ Пожалуйста, отправь файл в формате CSV.")
        return

    await update.message.reply_text("⏳ Обрабатываю файл...")

    try:
        file = await context.bot.get_file(doc.file_id)
        content = await file.download_as_bytearray()
        rows = parse_csv(bytes(content))

        if not rows:
            await update.message.reply_text("❌ CSV пустой или не удалось его прочитать.")
            return

        report = build_report(rows)

        if len(report) <= 4096:
            await update.message.reply_text(report, parse_mode="Markdown")
        else:
            chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode="Markdown")

    except Exception as e:
        logging.exception("Error processing CSV")
        await update.message.reply_text(f"❌ Ошибка при обработке файла:\n`{e}`", parse_mode="Markdown")

async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text("📎 Отправь CSV-файл, и я сделаю сводку. Или /help для справки.")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.INFO
    )
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
