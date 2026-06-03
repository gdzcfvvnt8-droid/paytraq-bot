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

# Add Telegram user_ids of allowed users here.
# To find your ID: message @userinfobot in Telegram.
ALLOWED_USERS = {563973148,  # Admin
}

# ─── REGION MAPPING ───────────────────────────────────────────────────────────

def get_region(project: str) -> str:
    p = project.strip().upper()
    if p in ("AM", "ARMENIA"):
        return "🇦🇲 Armenia"
    elif p in ("AZ", "AZERBAIJAN"):
        return "🇦🇿 Azerbaijan"
    elif p in ("UZ", "UZBEKISTAN"):
        return "🇺🇿 Uzbekistan"
    else:
        return "🌍 Europe / Other"

REGION_ORDER = ["🇦🇲 Armenia", "🇦🇿 Azerbaijan", "🇺🇿 Uzbekistan", "🌍 Europe / Other"]

# ─── CSV PARSING ──────────────────────────────────────────────────────────────

def parse_csv(content: bytes) -> list[dict]:
    """Parse CSV bytes, try tab then comma delimiter."""
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:2000]
    delimiter = "\t" if "\t" in sample else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for row in reader:
        rows.append({k.strip(): (v.strip() if v else "") for k, v in row.items()})
    return rows

def parse_amount(value: str) -> float:
    """Convert string like '1 234,56' or '1234.56' to float."""
    v = value.replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0

# ─── REPORT GENERATION ────────────────────────────────────────────────────────

def build_report(rows: list[dict]) -> str:
    """Build a text report: breakdown by region → currency → account."""

    # Structure: region → currency → account → total
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    skipped = 0
    total_rows = 0

    for row in rows:
        project  = row.get("Project", "")
        currency = row.get("Currency", "?").upper()
        account  = row.get("Account", "?")
        amount_s = row.get("Amount", "0")
        status   = row.get("Status", "").lower()

        # Only process incoming payments (Document No. starts with IN/)
        doc_no = row.get("Document No.", "")
        if not doc_no.upper().startswith("IN/"):
            skipped += 1
            continue

        # Skip cancelled/void entries
        if status in ("cancelled", "void", "voided"):
            skipped += 1
            continue

        amount = parse_amount(amount_s)
        if amount == 0:
            continue

        region = get_region(project)
        data[region][currency][account] += amount
        total_rows += 1

    if total_rows == 0:
        return "⚠️ Не найдено ни одной строки с суммой. Проверь формат CSV."

    lines = ["📊 *Сводка по оплатам PayTraq*\n"]

    grand_total_by_currency: dict[str, float] = defaultdict(float)

    for region in REGION_ORDER:
        if region not in data:
            continue

        lines.append(f"*{region}*")
        region_total: dict[str, float] = defaultdict(float)

        currencies = sorted(data[region].keys())
        for currency in currencies:
            accounts = data[region][currency]
            cur_total = sum(accounts.values())
            region_total[currency] += cur_total
            grand_total_by_currency[currency] += cur_total

            lines.append(f"  💵 *{currency}*: `{cur_total:,.2f}`")
            for account, amt in sorted(accounts.items(), key=lambda x: -x[1]):
                lines.append(f"    • {account}: `{amt:,.2f}`")

        lines.append("")  # blank line between regions

    # Grand total
    lines.append("─────────────────")
    lines.append("*💰 Итого по всем регионам:*")
    for currency, total in sorted(grand_total_by_currency.items()):
        lines.append(f"  {currency}: `{total:,.2f}`")

    if skipped:
        lines.append(f"\n_ℹ️ Пропущено строк (исходящие OUT/ и отменённые): {skipped}_")

    return "\n".join(lines)

# ─── BOT HANDLERS ─────────────────────────────────────────────────────────────

def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True  # If list is empty — allow everyone (fill it in to restrict)
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
        "📎 Просто отправь CSV-файл из PayTraq (раздел Деньги → Оплаты → Экспорт).\n\n"
        "Бот покажет разбивку:\n"
        "• 🇦🇲 Armenia (проекты AM / Armenia)\n"
        "• 🇦🇿 Azerbaijan (проекты AZ / Azerbaijan)\n"
        "• 🇺🇿 Uzbekistan (проект UZ / Uzbekistan)\n"
        "• 🌍 Europe / Other (всё остальное)\n\n"
        "Внутри каждого региона — суммы по валютам и счетам (источникам).",
        parse_mode="Markdown"
    )

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper: lets new users find their Telegram ID to add to ALLOWED_USERS."""
    uid = update.effective_user.id
    name = update.effective_user.full_name
    await update.message.reply_text(
        f"👤 {name}\nТвой Telegram ID: `{uid}`\n\nПередай его администратору бота.",
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

        # Telegram message limit is 4096 chars; split if needed
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

    logging.info("Bot started. Waiting for messages...")
    app.run_polling()

if __name__ == "__main__":
    main()
