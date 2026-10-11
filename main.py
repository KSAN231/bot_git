import logging
import os
import re
from pathlib import Path
from datetime import datetime
for folder in ["data", "price", "logs"]:
    os.makedirs(folder, exist_ok=True)

import pandas as pd
import telebot
from dotenv import load_dotenv
from telebot import types

load_dotenv(Path(__file__).with_name('.env'))

from config import TOKEN, ADMIN_ID, ORDER_CHAT_ID, BUG_CHAT_ID, PRICE_CHAT_ID, PRICE_DIR, DB_FILE, DB_BACKUP_CHAT_ID
from db import Database
from logging_config import setup_logging
from utils import get_file_data, send_file, latest_price_file

setup_logging()
logger = logging.getLogger(__name__)
bot = telebot.TeleBot(TOKEN)
db = Database(DB_FILE, Path(__file__).resolve().parent / "data")

# Runtime-only state. Persistent data (users/orders) lives in SQLite.
user_states = {}
order_states = {}
announcement_data = {}
bug_states = {}
admin_messages = {}
shutdown_requested = False

contacts_text = """
📞 Контакты и время работы оптового склада Маркет:
ПН - ПТ : 10:00 - 10:30
Иван: +7 000 000-00-00
Иван: +7 000 000-00-00
Иван: +7 000 000-00-00
"""
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            "logs/bot.log",
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)

def is_admin(message):
    return message.from_user.id == ADMIN_ID


def require_admin(message):
    if not is_admin(message):
        bot.send_message(message.chat.id, "❌ У вас нет прав для этой команды.")
        return False
    return True

def save_price_file(message):

    filename = message.document.file_name

    # ищем дату в названии
    match = re.search(
        r'(\d{2})[.\-_](\d{2})[.\-_](\d{4})',
        filename
    )

    if not match:
        logger.warning(
            "В прайсе нет даты: %s",
            filename
        )
        return


    day, month, year = match.groups()

    new_date = datetime.strptime(
        f"{day}.{month}.{year}",
        "%d.%m.%Y"
    )


    os.makedirs(
        PRICE_DIR,
        exist_ok=True
    )


    # ищем старый прайс
    old_date = None

    for file in os.listdir(PRICE_DIR):

        old_match = re.search(
            r'(\d{2})[.\-_](\d{2})[.\-_](\d{4})',
            file
        )

        if old_match:
            d, m, y = old_match.groups()

            old_date = datetime.strptime(
                f"{d}.{m}.{y}",
                "%d.%m.%Y"
            )


    # если старый прайс новее или такой же
    if old_date and new_date <= old_date:

        logger.info(
            "Старый прайс пропущен: %s",
            filename
        )

        bot.send_message(
            message.chat.id,
            f"⚠️ Прайс {day}.{month}.{year} старее текущего"
        )

        return


    # скачиваем новый
    file_info = bot.get_file(
        message.document.file_id
    )

    downloaded = bot.download_file(
        file_info.file_path
    )


    # удаляем старый
    for file in os.listdir(PRICE_DIR):
        path = os.path.join(
            PRICE_DIR,
            file
        )

        if os.path.isfile(path):
            os.remove(path)


    save_path = os.path.join(
        PRICE_DIR,
        filename
    )


    with open(save_path, "wb") as f:
        f.write(downloaded)


    logger.info(
        "Обновлён прайс: %s",
        filename
    )


    bot.send_message(
        message.chat.id,
        f"✅ Прайс обновлён\n📅 Дата: {day}.{month}.{year}"
    )

def notify_order_chat(order, user, username=None, prefix="🆕 Новый заказ"):
    try:
        if not order:
            logger.error("Не удалось отправить заказ: order=None")
            return

        if not user:
            logger.error(
                "Не найден пользователь Telegram ID: %s",
                order.get("telegram_id")
            )

            user = {
                "first_name": "Неизвестно",
                "last_name": "",
                "ip_name": "Не указано",
                "shop_name": "Не указано",
                "manager": "Не указан"
            }

        username = username or "без username"

        text = order.get("text") or "файл"

        message = (
            f"{prefix}\n\n"
            f"🆔 Номер заказа: {order.get('order_id')}\n"
            f"📅 Дата: {order.get('timestamp')}\n\n"
            f"👤 Клиент: {user.get('first_name')} {user.get('last_name')}\n"
            f"🏢 ИП: {user.get('ip_name')}\n"
            f"🏪 Заведение: {user.get('shop_name')}\n"
            f"🧑‍💼 Менеджер: {user.get('manager')}\n"
            f"📱 Username: @{username}\n"
            f"🆔 Telegram ID: {order.get('telegram_id')}\n\n"
            f"📋 Заказ:\n{text}"
        )

        if order.get("comment"):
            message += f"\n\n💬 Комментарий:\n{order['comment']}"

        bot.send_message(
            ORDER_CHAT_ID,
            message
        )

        if order.get("file_id"):
            send_file(
                bot,
                ORDER_CHAT_ID,
                order["file_id"],
                order.get("file_type", "document")
            )

        logger.info(
            "Заказ №%s отправлен оператору",
            order.get("order_id")
        )

    except Exception:
        logger.exception("Ошибка отправки заказа оператору")

@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.from_user.id)

    # Отправляем стикер Люка
    bot.send_sticker(
        message.chat.id,
        "CAACAgIAAxkBAAER8KhqtR0mWULH1Z-TF7iQWe5IVJohKQACaawAAvi5mEkX6eR6rGv1HD0E"
    )

    user = db.get_user(uid)

    if user:
        bot.send_message(
            message.chat.id,
            f"👋 Привет, {user['first_name']}!\n\n"
            "Я — бот-помощник Маркет 🤖\n\n"
            "Я помогу тебе:\n"
            "🛒 оформить заказ\n"
            "📋 получить актуальный прайс\n"
            "🐞 сообщить о проблеме\n"
            "📞 найти контакты склада\n\n"
            "Рад снова тебя видеть! 😊"
        )

    else:
        bot.send_message(
            message.chat.id,
            "👋 Привет!\n\n"
            "Меня зовут Бот 🤖\n"
            "Я бот-помощник Маркет.\n\n"
            "Помогу быстро оформить заказ, "
            "получить прайс и связаться с нашей командой.\n\n"
            "Для начала давай познакомимся 😊\n"
            "Напиши своё имя:"
        )

        user_states[uid] = {
            "stage": "first_name"
        }


@bot.message_handler(commands=['restart'])
def restart(message):
    uid = str(message.from_user.id)
    db.delete_user(uid)
    user_states[uid] = {"stage": "first_name"}
    bot.send_message(message.chat.id, "🔁 Введите своё имя:")


@bot.message_handler(commands=['price'])
def send_price(message):

    price_files = [
        f for f in os.listdir(PRICE_DIR)
        if f.lower().startswith("прайс")
        and f.lower().endswith(".xlsx")
    ]

    if not price_files:
        bot.send_message(
            message.chat.id,
            "❌ Актуальный прайс отсутствует"
        )
        return

    file_path = os.path.join(
        PRICE_DIR,
        price_files[0]
    )

    with open(file_path, "rb") as f:
        bot.send_document(
            message.chat.id,
            f,
            caption=f"📋 {price_files[0]}"
        )


@bot.message_handler(commands=['order'])
def order(message):
    uid = str(message.from_user.id)
    order_states[uid] = {"stage": "waiting_for_order"}
    bot.send_message(message.chat.id, "📝 Введите ваш заказ (текст или файл):")


@bot.message_handler(commands=['contacts'])
def contacts(message):
    bot.send_message(message.chat.id, contacts_text)


@bot.message_handler(commands=['setstatus'])
def set_status(message):
    if not require_admin(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.send_message(message.chat.id, "⚠️ Формат: /setstatus {номер_заказа} {новый_статус}")
        return
    try:
        order_id = int(parts[1])
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ Номер заказа должен быть числом.")
        return
    new_status = parts[2].strip()
    if not db.set_status(order_id, new_status):
        bot.send_message(message.chat.id, f"❌ Заказ с номером {order_id} не найден.")
        return
    bot.send_message(message.chat.id, f"✅ Статус заказа №{order_id} обновлен на '{new_status}'.")

@bot.message_handler(commands=['bug'])
def bug_report(message):
    uid = str(message.from_user.id)

    if not db.get_user(uid):
        bot.send_message(
            message.chat.id,
            "❌ Сначала зарегистрируйтесь через /start"
        )
        return

    bug_states[uid] = True

    bot.send_message(
        message.chat.id,
        "🐞 Опишите проблему.\n"
        "Напишите, что произошло и что ожидалось."
    )
@bot.message_handler(
    func=lambda m: bug_states.get(str(m.from_user.id)),
    content_types=['text']
)
def handle_bug(message):

    uid = str(message.from_user.id)

    user = db.get_user(uid)

    if not user:
        bot.send_message(
            message.chat.id,
            "❌ Пользователь не найден"
        )
        return


    bug_text = message.text.strip()


    report = (
        "🐞 НОВЫЙ БАГ-РЕПОРТ\n\n"
        f"🆔 Telegram ID: {uid}\n"
        f"👤 Пользователь: {user.get('first_name')} {user.get('last_name')}\n"
        f"🏢 ИП: {user.get('ip_name')}\n"
        f"🏪 Заведение: {user.get('shop_name')}\n"
        f"🧑‍💼 Менеджер: {user.get('manager')}\n"
        f"📱 Username: @{message.from_user.username or 'нет'}\n\n"
        f"💬 Сообщение:\n{bug_text}\n\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )


    bot.send_message(
        BUG_CHAT_ID,
        report
    )


    bot.send_message(
        message.chat.id,
        "✅ Спасибо! Баг-репорт отправлен разработчику."
    )


    bug_states.pop(uid, None)

@bot.message_handler(commands=['crm'])
def crm(message):
    if not require_admin(message):
        return
    users = db.get_users()
    if not users:
        bot.send_message(message.chat.id, "❌ Нет зарегистрированных пользователей.")
        return
    response = ""
    for uid, data in users.items():
        response += (
            f"\n🆔 {uid}\n👤 {data['first_name']} {data['last_name']}\n"
            f"ИП: {data['ip_name']}\nЗаведение: {data['shop_name']}\n{'-' * 20}"
        )
    bot.send_message(message.chat.id, response[:4096])


@bot.message_handler(commands=['crm_export'])
def crm_export(message):
    if not require_admin(message):
        return
    users = db.get_users()
    data = [{"Telegram ID": uid, **info} for uid, info in users.items()]
    export_path = Path(__file__).resolve().parent / "crm_export.xlsx"
    pd.DataFrame(data).to_excel(export_path, index=False)
    with export_path.open("rb") as f:
        bot.send_document(message.chat.id, f)


@bot.message_handler(commands=['client'])
def client_info(message):
    if not require_admin(message):
        return
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "⚠️ Формат: /client {telegram_id}")
        return
    uid = parts[1]
    info = db.get_user(uid)
    if not info:
        bot.send_message(message.chat.id, "❌ Пользователь не найден")
        return
    orders = db.get_orders(uid)
    text = (
        f"👤 {info['first_name']} {info['last_name']}\n"
        f"ИП: {info['ip_name']}\nЗаведение: {info['shop_name']}\nЗаказов: {len(orders)}"
    )
    if orders:
        last = orders[-1]
        text += (
            f"\nПоследний заказ: {last['timestamp']}\n"
            f"Содержание: {last.get('text') or 'файл'}\n"
            f"Оплата: {'✅' if last['paid'] else '❌'}\n"
            f"📦 Статус: {last.get('status', 'Неизвестно')}"
        )
    bot.send_message(message.chat.id, text)


@bot.message_handler(commands=['setpaid'])
def set_paid(message):
    if not require_admin(message):
        return
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "⚠️ Формат: /setpaid {telegram_id}")
        return
    order = db.set_last_paid(parts[1])
    if order:
        bot.send_message(message.chat.id, "✅ Последний заказ помечен как оплаченный.")
    else:
        bot.send_message(message.chat.id, "❌ Нет заказов у пользователя.")


@bot.message_handler(commands=['delmyorder'])
def user_delete_order(message):
    uid = str(message.from_user.id)
    deleted = db.delete_last_order(uid)
    if not deleted:
        bot.send_message(message.chat.id, "❌ У вас нет заказов для удаления.")
        return
    user = db.get_user(uid) or {"first_name": "Пользователь"}
    info_text = (
        "❗ Заказ удалён пользователем:\n"
        f"👤 {user['first_name']} (ID: {uid})\n"
        f"🆔 Номер заказа: {deleted.get('order_id', '(нет номера)')}\n"
        f"🗓 Дата: {deleted.get('timestamp', '(без даты)')}\n"
        f"📋 Текст заказа:\n{deleted.get('text') or '(файл)'}"
    )
    try:
        bot.send_message(ORDER_CHAT_ID, info_text)
        if deleted.get("file_id"):
            send_file(bot, ORDER_CHAT_ID, deleted["file_id"], deleted.get("file_type", "document"))
    except Exception:
        logger.exception("Ошибка отправки уведомления об удалении заказа")
    bot.send_message(message.chat.id, "✅ Ваш последний заказ удалён.")


@bot.message_handler(commands=['notify'])
def notify_users(message):
    if not require_admin(message):
        return
    msg = bot.send_message(message.chat.id, "✏️ Введите текст объявления:")
    bot.register_next_step_handler(msg, ask_for_photo)


def ask_for_photo(message):
    uid = str(message.from_user.id)
    announcement_data[uid] = (message.text or "").strip()
    bot.send_message(message.chat.id, "📷 Теперь отправьте изображение (фото новинки или товара).")


@bot.message_handler(func=lambda m: str(m.from_user.id) in announcement_data, content_types=['photo'])
def handle_broadcast_photo(message):
    uid = str(message.from_user.id)
    caption = f"📢 *Новое объявление от Маркет!*\n\n{announcement_data[uid]}"
    success = failed = 0
    for target_id in db.get_users():
        try:
            bot.send_photo(target_id, message.photo[-1].file_id, caption=caption, parse_mode="Markdown")
            success += 1
        except Exception:
            logger.exception("Не удалось отправить объявление пользователю %s", target_id)
            failed += 1
    bot.send_message(message.chat.id, f"✅ Рассылка завершена.\n📬 Успешно: {success}, ❌ Ошибки: {failed}")
    announcement_data.pop(uid, None)


@bot.message_handler(commands=['editmyorder'])
def user_edit_order(message):
    uid = str(message.from_user.id)
    old_order = db.get_last_order(uid)
    if not old_order:
        bot.send_message(message.chat.id, "❌ У вас нет заказов для редактирования.")
        return
    preview = ""
    if (old_order.get('text') or '').strip():
        preview += f"Текст заказа:\n{old_order['text']}\n\n"
    if old_order.get('file_id'):
        preview += f"В заказе есть файл (тип: {old_order.get('file_type', 'неизвестно')}).\n\n"
    if not preview:
        bot.send_message(message.chat.id, "Ошибка: в вашем заказе отсутствует текст и файл. Пожалуйста, создайте новый заказ.")
        return
    bot.send_message(
        message.chat.id,
        f"Ваш последний заказ:\n\n{preview}"
        "Отправьте новый текст заказа или файл для замены.\n"
        "Если хотите оставить текст без изменений, напишите '-'.\n"
        "Если хотите удалить файл — напишите '-file'."
    )
    order_states[uid] = {"stage": "editing_order", "order_id": old_order['order_id']}


# ЕДИНСТВЕННЫЙ обработчик редактирования заказа.
# Он заменяет оба старых обработчика process_edit_order и handle_edit_order.
@bot.message_handler(
    func=lambda m: order_states.get(str(m.from_user.id), {}).get("stage") == "editing_order",
    content_types=['text', 'document', 'photo', 'video', 'audio']
)
def process_edit_order(message):
    uid = str(message.from_user.id)
    state = order_states.get(uid)
    if not state:
        return
    old_order = db.get_last_order(uid)
    if not old_order:
        order_states.pop(uid, None)
        bot.send_message(message.chat.id, "❌ Заказ не найден.")
        return

    updated = False
    file_removed = False
    changes = {}

    if message.content_type == 'text':
        new_text = message.text.strip()
        if new_text.lower() == '-file':
            if old_order.get('file_id'):
                changes = {"file_id": None, "file_type": None}
                file_removed = True
                updated = True
            else:
                bot.send_message(message.chat.id, "У вас нет файла в заказе для удаления.")
                return
        elif new_text not in ('-', ''):
            changes = {"text": new_text}
            updated = True
    else:
        file_id, file_type = get_file_data(message)
        if not file_id:
            bot.send_message(message.chat.id, "Ошибка: неподдерживаемый тип файла.")
            return
        changes = {"file_id": file_id, "file_type": file_type}
        updated = True

    if not updated:
        bot.send_message(message.chat.id, "Вы не внесли изменений в заказ.")
        order_states.pop(uid, None)
        return

    db.update_order(old_order['order_id'], **changes)
    updated_order = db.get_order_by_id(old_order['order_id'])
    bot.send_message(message.chat.id, f"✅ Ваш заказ №{old_order['order_id']} успешно отредактирован.")

    try:
        text_part = (updated_order.get('text') or '').strip() or '(текст заказа отсутствует)'
        msg = (
            f"✏️ Заказ отредактирован:\n"
            f"🆔 Номер заказа: {old_order['order_id']}\n"
            f"📋 Текст заказа:\n{text_part}\n"
        )
        if file_removed:
            msg += "❌ Файл из заказа был удалён.\n"
        bot.send_message(ORDER_CHAT_ID, msg)
        if not file_removed and updated_order.get('file_id'):
            bot.send_message(ORDER_CHAT_ID, "Обновленный файл заказа:")
            send_file(bot, ORDER_CHAT_ID, updated_order['file_id'], updated_order.get('file_type', 'document'))
    except Exception:
        logger.exception("Ошибка отправки обновления заказа в чат заказов")

    order_states.pop(uid, None)


@bot.message_handler(func=lambda m: str(m.from_user.id) in user_states)
def registration(message):
    uid = str(message.from_user.id)
    if message.content_type != 'text':
        bot.send_message(message.chat.id, "❌ Пожалуйста, отправьте текст.")
        return
    text = message.text.strip()
    state = user_states[uid]
    if state['stage'] == 'first_name':
        state['first_name'] = text
        state['stage'] = 'last_name'
        bot.send_message(message.chat.id, "Введите фамилию:")
    elif state['stage'] == 'last_name':
        state['last_name'] = text
        state['stage'] = 'ip_name'
        bot.send_message(message.chat.id, "Введите ИП:")
    elif state['stage'] == 'ip_name':
        state['ip_name'] = text
        state['stage'] = 'shop_name'
        bot.send_message(message.chat.id, "Введите название заведения:")
    elif state['stage'] == 'shop_name':
        state['shop_name'] = text
        state['stage'] = 'representative'
        bot.send_message(message.chat.id, "Введите имя торгового представителя, через которого вы работаете:")
    elif state['stage'] == 'representative':
        db.upsert_user(uid, {
            'first_name': state['first_name'], 'last_name': state['last_name'],
            'ip_name': state['ip_name'], 'shop_name': state['shop_name'], 'manager': text
        })
        user_states.pop(uid, None)
        bot.send_message(message.chat.id, "✅ Регистрация завершена!")


@bot.message_handler(func=lambda m: order_states.get(str(m.from_user.id), {}).get('stage') == 'waiting_for_order', content_types=['text'])
def handle_order(message):
    uid = str(message.from_user.id)
    if not db.get_user(uid):
        bot.send_message(message.chat.id, "❌ Пожалуйста, сначала зарегистрируйтесь через /start")
        order_states.pop(uid, None)
        return
    order_states[uid] = {'stage': 'waiting_for_comment', 'order_text': message.text.strip()}
    bot.send_message(message.chat.id, "✏️ Хотите оставить комментарий к заказу? Например, адрес точки. Введите его или напишите «-», если комментарий не нужен.")


@bot.message_handler(func=lambda m: order_states.get(str(m.from_user.id), {}).get('stage') == 'waiting_for_comment', content_types=['text'])
def handle_order_comment(message):
    uid = str(message.from_user.id)
    state = order_states[uid]
    comment = message.text.strip()
    user = db.get_user(uid)
    order = db.create_order(uid, text=state['order_text'], comment='' if comment == '-' else comment)
    logger.info("Создан заказ №%s пользователем %s", order['order_id'], uid)
    try:
        notify_order_chat(order, user, message.from_user.username)
        bot.send_message(message.chat.id, f"✅ Номер вашего заказа {order['order_id']}. Ваш заказ принят в обработку!")
    except Exception:
        logger.exception("Ошибка отправки заказа №%s", order['order_id'])
        bot.send_message(message.chat.id, "❌ Ошибка отправки заказа.")
    order_states.pop(uid, None)


@bot.message_handler(content_types=['document'])
def handle_file(message):

    logger.info(
        "Получен документ: %s | чат: %s",
        message.document.file_name,
        message.chat.id
    )

    # обработка прайса из рабочего чата
    if message.chat.id == PRICE_CHAT_ID:

        filename = message.document.file_name

        if (
            filename.lower().endswith(".xlsx")
            and filename.lower().startswith("прайс")
        ):
            save_price_file(message)
            return


    # дальше идёт твоя старая логика заказа
    uid = str(message.from_user.id)
    user = db.get_user(uid)

    if not user:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, зарегистрируйтесь через /start"
        )
        return

    if order_states.get(uid, {}).get('stage') == 'waiting_for_order':
        order_states[uid] = {
            'stage': 'waiting_for_comment_file',
            'file_id': message.document.file_id,
            'file_type': 'document'
        }

        bot.send_message(
            message.chat.id,
            "✏️ Хотите оставить комментарий к заказу (файл)?"
        )


@bot.message_handler(func=lambda m: order_states.get(str(m.from_user.id), {}).get('stage') == 'waiting_for_comment_file', content_types=['text'])
def handle_order_file_comment(message):
    uid = str(message.from_user.id)
    state = order_states[uid]
    comment = message.text.strip()
    user = db.get_user(uid)
    order = db.create_order(uid, comment='' if comment == '-' else comment, file_id=state['file_id'], file_type=state['file_type'])
    logger.info("Создан файловый заказ №%s пользователем %s", order['order_id'], uid)
    try:
        notify_order_chat(order, user, message.from_user.username)
        bot.send_message(message.chat.id, f"✅ Номер вашего заказа {order['order_id']}. Ваш заказ принят в обработку!")
    except Exception:
        logger.exception("Ошибка отправки файлового заказа №%s", order['order_id'])
        bot.send_message(message.chat.id, "❌ Ошибка отправки заказа.")
    order_states.pop(uid, None)

@bot.message_handler(commands=['message'])
def admin_message_start(message):
    if message.from_user.id != ADMIN_ID:
        return
    users = db.get_users()
    if not users:
        bot.send_message(
            message.chat.id,
            "Клиентов нет"
        )
        return
    text = "👥 Клиенты:\n\n"
    for uid, user in users.items():
        text += (
            f"{user['first_name']} {user['last_name']}\n"
            f"ID: `{uid}`\n"
            f"Магазин: {user['shop_name']}\n\n"
        )
    bot.send_message(
        message.chat.id,
        text,
        parse_mode="Markdown"
    )
    admin_messages[message.from_user.id] = {
        "stage": "waiting_client_id"
    }

@bot.message_handler(
    func=lambda m:
    m.from_user.id == ADMIN_ID
    and admin_messages.get(m.from_user.id, {}).get("stage") == "waiting_client_id"
)
def admin_choose_client(message):

    uid = message.text.strip()

    user = db.get_user(uid)

    if not user:
        bot.send_message(
            message.chat.id,
            "❌ Клиент с таким ID не найден"
        )
        return

    admin_messages[message.from_user.id] = {
        "stage": "waiting_client_text",
        "user_id": uid
    }

    bot.send_message(
        message.chat.id,
        f"✅ Выбран клиент:\n"
        f"{user['first_name']} {user['last_name']}\n"
        f"Магазин: {user['shop_name']}\n\n"
        "✏️ Напишите сообщение:"
    )


@bot.message_handler(
    func=lambda m:
    m.from_user.id == ADMIN_ID
    and admin_messages.get(m.from_user.id, {}).get("stage") == "waiting_client_text"
)
def admin_send_client_message(message):
    uid = admin_messages[message.from_user.id]["user_id"]
    bot.send_message(
        int(uid),
        "💬 Сообщение от Маркет:\n\n"
        + message.text
    )
    bot.send_message(
        message.chat.id,
        "✅ Сообщение отправлено клиенту"
    )
    admin_messages.pop(message.from_user.id, None)

@bot.message_handler(commands=['stop'])
def stop_bot(message):
    global shutdown_requested
    if not require_admin(message):
        return
    if not DB_BACKUP_CHAT_ID:
        bot.send_message(
            message.chat.id,
            "❌ Не указан DB_BACKUP_CHAT_ID в .env"
        )
        return
    bot.send_message(
        message.chat.id,
        "⏳ Сохраняю базу данных..."
    )
    try:
        backup_dir = Path(__file__).resolve().parent / "data" / "backups"
        backup_path = db.backup(backup_dir)

        logger.info("Создан backup базы: %s", backup_path)

        caption = (
            "🗄 Резервная копия базы данных\n\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"📦 {backup_path.name}"
        )

        with backup_path.open("rb") as file:
            bot.send_document(
                DB_BACKUP_CHAT_ID,
                file,
                caption=caption
            )

        logger.info(
            "Backup базы отправлен в Telegram: %s",
            backup_path.name
        )

        bot.send_message(
            message.chat.id,
            "✅ База данных сохранена.\n"
            f"📦 {backup_path.name}\n"
            "☁️ Копия отправлена в Telegram.\n\n"
            "🛑 Бот выключается..."
        )

        shutdown_requested = True
        bot.stop_polling()

    except Exception:
        logger.exception("Ошибка при создании или отправке backup")

        bot.send_message(
            message.chat.id,
            "❌ Не удалось сохранить базу или отправить backup.\n"
            "🛑 Бот НЕ выключен."
        )


if __name__ == '__main__':
    common_commands = [
        types.BotCommand("start", "🚀 Запустить бота"),
        types.BotCommand("restart", "🔁 Зарегистрироваться заново"),
        types.BotCommand("price", "📋 Получить прайс-лист"),
        types.BotCommand("order", "🛒 Сделать заказ"),
        types.BotCommand("contacts", "📞 Контакты склада"),
        types.BotCommand("delmyorder", "🗑 Удалить последний заказ"),
        types.BotCommand("editmyorder", "✏️ Редактировать последний заказ"),
        types.BotCommand("bug", "🐞 Сообщить о баге")
    ]
    admin_only_commands = [
        types.BotCommand("crm", "🟣 [АДМИН] Список клиентов"),
        types.BotCommand("crm_export", "🟣 [АДМИН] Экспорт CRM"),
        types.BotCommand("client", "🟣 [АДМИН] История заказов клиента"),
        types.BotCommand("setpaid", "🟣 [АДМИН] Отметить оплату"),
        types.BotCommand("notify", "🟣 [АДМИН] Объявление"),
        types.BotCommand("setstatus", "🟣 [АДМИН] Изменить статус заказа"),
        types.BotCommand("message", "🟣 [АДМИН] Написать клиенту"),
        types.BotCommand("stop", "🛑 [АДМИН] Сохранить БД и выключить")
    ]
    bot.set_my_commands(common_commands, scope=types.BotCommandScopeDefault())
    bot.set_my_commands(common_commands + admin_only_commands, scope=types.BotCommandScopeChat(chat_id=ADMIN_ID))
    logger.info("Бот запускается")
    while not shutdown_requested:
        try:
            bot.polling(skip_pending=True)
        except Exception:
            if shutdown_requested:
                break
            logger.exception("Ошибка обработки события")
    logger.info("Бот остановлен")
