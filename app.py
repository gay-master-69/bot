import logging
import json
import os
import re
import datetime
import uuid
from typing import Optional, List

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, Date, ForeignKey, BigInteger
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from sqlalchemy.types import TypeDecorator
from sqlalchemy import TypeDecorator, String as SQLA_String

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.error import TelegramError

TOKEN = os.getenv('TOKEN')
DEVELOPER_IDS = [5150559970]

ANKET_CHANNEL_ID = -1003394079022

ALLOWED_CHAT_IDS = [
    -1003431402721,
    -1003355542910,
    -1003300824366,
    -1003394079022,
    -1003062290367,
]

DB_NAME = "omniverse_rp.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
(
    STATE_SUPPORT_MESSAGE,
    STATE_SUPPORT_REPLY,
    STATE_PLAYERBOARD_MESSAGE,
    STATE_PLAYERBOARD_ROLES,
    STATE_ANKETA_MESSAGE,
    STATE_SEND_INFO_CONTENT,
    STATE_ANKETA_CLARIFY,
) = range(7)

DATABASE_URL = os.getenv('DATABASE_URL', '').replace('postgres://', 'postgresql://')

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
    logger.info("Используется PostgreSQL база данных")
else:
    engine = create_engine(f"sqlite:///{DB_NAME}", connect_args={"check_same_thread": False})
    logger.info("Используется локальная SQLite база данных")

Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class StringList(TypeDecorator):
    impl = SQLA_String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return json.dumps([])
        if not isinstance(value, list):
            logger.warning(f"StringList process_bind_param received non-list value: {type(value)} - {value}. Wrapping in a list.")
            value = [str(value)] if value is not None else []
        value = [str(item) if item is not None else '' for item in value]
        return json.dumps(value, ensure_ascii=False)

    def process_result_param(self, value, dialect):
        if value is None:
            return []
        try:
            deserialized_value = json.loads(value)
            if isinstance(deserialized_value, list):
                return deserialized_value
            else:
                logger.warning(f"StringList expected a JSON list, but got type {type(deserialized_value)} for value '{value}'. Returning empty list.")
                return []
        except json.JSONDecodeError:
            logger.error(f"StringList failed to JSON decode value: '{value}'. Returning empty list.", exc_info=True)
            return []
        except Exception as e:
            logger.error(f"Unexpected error in StringList process_result_param for value '{value}': {e}. Returning empty list.", exc_info=True)
            return []

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String, index=True)
    status_rp = Column(String, default="Участник")
    unique_code = Column(String, unique=True, index=True)
    is_developer = Column(Boolean, default=False)
    is_moderator = Column(Boolean, default=False)
    is_anketnik = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)

    roles = relationship("Role", back_populates="user", cascade="all, delete-orphan")
    support_requests = relationship("SupportRequest", back_populates="user", cascade="all, delete-orphan")
    posts = relationship("Post", back_populates="user", cascade="all, delete-orphan")
    anketa_requests = relationship("AnketaRequest", back_populates="user", cascade="all, delete-orphan")
    info_subscriptions = relationship("InfoSubscription", back_populates="user", uselist=False, cascade="all, delete-orphan")
    playerboard_entries = relationship("PlayerBoardEntry", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"

class Role(Base):
    __tablename__ = "roles"
    id = Column(BigInteger, primary_key=True, autoincrement=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    name = Column(String)
    hashtag = Column(String, index=True)
    last_active = Column(Date, default=datetime.date.today)
    last_warning_sent = Column(Date, nullable=True)

    user = relationship("User", back_populates="roles")

class PlayerBoardEntry(Base):
    __tablename__ = "player_board_entries"
    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    message = Column(Text)
    roles_needed = Column(StringList)
    created_at = Column(DateTime, default=datetime.datetime.now)

    user = relationship("User", back_populates="playerboard_entries")

class SupportRequest(Base):
    __tablename__ = "support_requests"
    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    request_content = Column(StringList)
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.datetime.now)
    recipient_messages = Column(StringList, default=[])

    user = relationship("User", back_populates="support_requests")

class Post(Base):
    __tablename__ = "posts"
    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    content = Column(Text)
    hashtag = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.datetime.now)
    message_id = Column(BigInteger, nullable=True)
    chat_id = Column(BigInteger, nullable=True)

    user = relationship("User", back_populates="posts")

class AnketaRequest(Base):
    __tablename__ = "anketa_requests"
    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    anketa_content = Column(Text)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.datetime.now)
    admin_message_id = Column(BigInteger, nullable=True)
    admin_chat_id = Column(BigInteger, nullable=True)

    user = relationship("User", back_populates="anketa_requests")

class InfoSubscription(Base):
    __tablename__ = "info_subscriptions"
    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), unique=True)
    subscribed = Column(Boolean, default=False)

    user = relationship("User", back_populates="info_subscriptions")

def create_tables():
    """Создание таблиц в базе данных"""
    Base.metadata.create_all(bind=engine)
    logger.info("Таблицы базы данных созданы или уже существуют.")

def get_or_create_user(session, user_id, username=None):
    """Получить или создать пользователя в базе данных"""
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        user = User(
            id=user_id,
            username=username or str(user_id),
            unique_code=str(uuid.uuid4())[:8]
        )
        session.add(user)
        session.commit()
        
        # Добавляем базовую роль для нового пользователя
        default_role = Role(
            user_id=user.id,
            name="Участник",
            hashtag="участник"
        )
        session.add(default_role)
        session.commit()
        
        logger.info(f"Создан новый пользователь: {user_id} ({username})")
    return user

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь администратором"""
    if user_id in DEVELOPER_IDS:
        return True
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if user and (user.is_developer or user.is_moderator):
            return True
        return False
    finally:
        session.close()

def is_anketnik(user_id: int) -> bool:
    """Проверка, является ли пользователь анкетником"""
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if user and user.is_anketnik:
            return True
        return False
    finally:
        session.close()

def is_developer(user_id: int) -> bool:
    """Проверка, является ли пользователь разработчиком"""
    return user_id in DEVELOPER_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    if not user:
        return
    
    session = SessionLocal()
    try:
        db_user = get_or_create_user(session, user.id, user.username)
        
        welcome_text = f"""
👋 Привет, {user.first_name}!

Добро пожаловать в Омниверс.

Чтобы начать, используй команды:
/anketa - создать анкету
/profile - просмотреть свой профиль
/help - список всех команд

Приятного времяпровождения. 
"""
        await update.message.reply_text(welcome_text)
    finally:
        session.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📚 *Список команд бота*

Основные команды:
/start - Запуск бота
/help - Показать это сообщение
/rules - Правила
/profile - Просмотр профиля
/anketa - Создать анкету

Команды для администраторов:
/warn - Выдать предупреждение пользователю
/deletemessages - Удалить сообщения пользователя

Команды для анкетников:
/anketa_review - Просмотр анкет на модерацию

Для получения дополнительной информации обратитесь к администрации.
"""
    await update.message.reply_text(help_text)

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать профиль пользователя"""
    user = update.effective_user
    if not user:
        return
    
    session = SessionLocal()
    try:
        db_user = get_or_create_user(session, user.id, user.username)
        
        # Получаем роли пользователя
        roles = session.query(Role).filter_by(user_id=db_user.id).all()
        roles_text = ", ".join([role.name for role in roles]) if roles else "Нет ролей"
        
        profile_text = f"""
👤 *Профиль пользователя*

ID: `{db_user.id}`
Имя: {user.first_name}
Username: @{user.username or 'не указан'}

🎭 Роли: {roles_text}
📊 Статус: {db_user.status_rp}

📝 Анкета: {'✅ Заполнена' if db_user.anketa_requests else '❌ Не заполнена'}
"""
        await update.message.reply_text(profile_text, parse_mode='Markdown')
    finally:
        session.close()

async def anketa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /anketa"""
    user = update.effective_user
    if not user:
        return
    
    # Проверяем, не забанен ли пользователь
    session = SessionLocal()
    try:
        db_user = get_or_create_user(session, user.id, user.username)
        if db_user.is_banned:
            await update.message.reply_text("⛔ Вы забанены и не можете использовать этого бота.")
            return
    finally:
        session.close()
    
    # TODO: Реализовать логику работы с анкетами
    await update.message.reply_text(
        "📝 Функция создания анкеты находится в разработке.\n"
        "Скоро вы сможете создать свою анкету для игры в Омниверсе!"
    )

async def anketa_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /anketa_review для анкетников"""
    user = update.effective_user
    if not user:
        return
    
    if not is_anketnik(user.id):
        await update.message.reply_text("⛔ У вас нет прав для просмотра анкет.")
        return
    
    # TODO: Реализовать логику просмотра анкет
    await update.message.reply_text("📋 Список анкет на модерацию пока пуст.")

async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдать предупреждение пользователю (только для администраторов)"""
    user = update.effective_user
    if not user:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    # Проверяем, передан ли аргумент
    if not context.args:
        await update.message.reply_text(
            "⚠️ Использование: /warn @username [причина]\n"
            "Пример: /warn @user Нарушение правил"
        )
        return
    
    # TODO: Реализовать логику выдачи предупреждений
    target_username = context.args[0]
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Причина не указана"
    
    await update.message.reply_text(
        f"⚠️ Пользователю {target_username} выдано предупреждение.\n"
        f"Причина: {reason}"
    )

async def deletemessages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить сообщения пользователя (только для администраторов)"""
    user = update.effective_user
    if not user:
        return
    
    if not is_admin(user.id):
        await update.message.reply_text("⛔ У вас нет прав для выполнения этой команды.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ Использование: /deletemessages @username [количество]\n"
            "Пример: /deletemessages @user 10"
        )
        return
    
    # TODO: Реализовать логику удаления сообщений
    target_username = context.args[0]
    count = int(context.args[1]) if len(context.args) > 1 else 5
    
    await update.message.reply_text(
        f"🗑️ Удалено {count} последних сообщений пользователя {target_username}."
    )

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /rules"""
    user = update.effective_user
    if not user:
        return
    
    rules_text = """ https://telegra.ph/Konstituciya-Omniversa-05-15 """
    await update.message.reply_text(rules_text, parse_mode='Markdown')

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик неизвестных команд"""
    await update.message.reply_text(
        "❌ Неизвестная команда. Используйте /help для списка доступных команд."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Операция отменена.")

async def lore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для вызова сообщения с URL-кнопками"""
    user = update.effective_user
    if not user:
        return
    text = """События из истории Омниреальности"""

    keyboard = [
    [InlineKeyboardButton("Война Дума", url="https://telegra.ph/Vojna-Duma-07-27")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /feedback - отправка предложений/жалоб"""
    user = update.effective_user
    if not user:
        return
    
    text = ' '.join(context.args)
    if not text:
        await update.message.reply_text(
            "📝 *Как отправить жалобу:*\n\n"
            "Напиши команду и текст:\n"
            "`/feedback Текст предложения или жалобы`\n\n"
            "Пример:\n"
            "`/feedback Хочу чтобы добавили команду для розыгрышей!`",
            parse_mode='Markdown'
        )
        return
    
    session = SessionLocal()
    try:
        db_user = get_or_create_user(session, user.id, user.username)
        
        ADMIN_CHAT_ID = 5150559970  # ← ТВОЙ ID
        
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"📩 *Новое обращение!*\n\n"
                 f"👤 От: @{user.username or user.first_name}\n"
                 f"🆔 ID: `{user.id}`\n\n"
                 f"📝 Текст:\n{text}",
            parse_mode='Markdown'
        )
        
        await update.message.reply_text(
            "✅ Спасибо за обратную связь!\n\n"
            "Ваше сообщение отправлено администрации."
        )
    finally:
        session.close()

# ========== АНКЕТЫ ==========
MODERATOR_IDS = [1720557031, 5150559970]  # ← оба твоих ID

async def anketa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание новой анкеты"""
    user = update.effective_user
    if not user:
        return
    
    session = SessionLocal()
    try:
        db_user = get_or_create_user(session, user.id, user.username)
        if db_user.is_banned:
            await update.message.reply_text("⛔ Вы забанены.")
            return
        
        # Проверяем, есть ли уже анкета на рассмотрении
        existing = session.query(AnketaRequest).filter_by(
            user_id=user.id, 
            status="pending"
        ).first()
        
        if existing:
            await update.message.reply_text(
                "⏳ У вас уже есть анкета на рассмотрении.\n"
                "Дождитесь ответа администрации."
            )
            return
        
        # Запрашиваем текст анкеты
        context.user_data['anketa_step'] = 'waiting_content'
        await update.message.reply_text(
            "📝 *Создание анкеты*\n\n"
            "Напишите текст вашей анкеты.\n"
            "Укажите: имя, возраст, описание роли и другую важную информацию.\n\n"
            "Просто отправьте текст анкеты прямо сейчас:",
            parse_mode='Markdown'
        )
    finally:
        session.close()


async def anketa_handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    
    if context.user_data.get('anketa_step') != 'waiting_content':
        return
    
    text = update.message.text
    if len(text) < 10:
        await update.message.reply_text("⚠️ Анкета слишком короткая. Напишите хотя бы 10 символов.")
        return
    
    logger.info(f"📝 Получен текст анкеты от {user.id}: {text[:50]}...")
    
    session = SessionLocal()
    try:
        new_anketa = AnketaRequest(
            user_id=user.id,
            anketa_content=text,
            status="pending"
        )
        session.add(new_anketa)
        session.commit()
        logger.info(f"✅ Анкета сохранена в БД, id={new_anketa.id}")
        
        for mod_id in MODERATOR_IDS:
            try:
                await context.bot.send_message(
                    chat_id=mod_id,
                    text=f"📋 *Новая анкета!*\n\n"
                         f"👤 От: @{user.username or user.first_name}\n"
                         f"🆔 ID: `{user.id}`\n\n"
                         f"📝 Текст анкеты:\n{text}",
                    parse_mode='Markdown'
                )
                logger.info(f"✅ Отправлено модератору {mod_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки модератору {mod_id}: {e}")
        
        await update.message.reply_text("✅ Анкета отправлена на модерацию!")
        context.user_data.pop('anketa_step', None)
    except Exception as e:
        logger.error(f"❌ Ошибка в anketa_handle_text: {e}")
        await update.message.reply_text("❌ Ошибка при сохранении анкеты.")
    finally:
        session.close()

async def anketa_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр анкет на модерации (для анкетников)"""
    user = update.effective_user
    if not user:
        return
    
    if not is_anketnik(user.id):
        await update.message.reply_text("⛔ У вас нет прав для просмотра анкет.")
        return
    
    session = SessionLocal()
    try:
        pending = session.query(AnketaRequest).filter_by(status="pending").all()
        
        if not pending:
            await update.message.reply_text("📭 Нет анкет на модерации.")
            return
        
        for anketa in pending:
            user_info = session.query(User).filter_by(id=anketa.user_id).first()
            keyboard = [
                [
                    InlineKeyboardButton("✅ Одобрить", callback_data=f"anketa_approve_{anketa.id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"anketa_reject_{anketa.id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"📋 *Анкета #{anketa.id}*\n\n"
                f"👤 Пользователь: @{user_info.username or user_info.id}\n"
                f"🆔 ID: `{anketa.user_id}`\n\n"
                f"📝 Текст:\n{anketa.anketa_content}",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
    finally:
        session.close()


async def anketa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок одобрения/отклонения анкет"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    if not is_anketnik(user.id):
        await query.edit_message_text("⛔ У вас нет прав для модерации анкет.")
        return
    
    data = query.data
    parts = data.split('_')
    action = parts[1]
    anketa_id = int(parts[2])
    
    session = SessionLocal()
    try:
        anketa = session.query(AnketaRequest).filter_by(id=anketa_id).first()
        if not anketa:
            await query.edit_message_text("❌ Анкета не найдена.")
            return
        
        if action == "approve":
            anketa.status = "approved"
            await query.edit_message_text(f"✅ Анкета #{anketa_id} одобрена.")
            
            await context.bot.send_message(
                chat_id=anketa.user_id,
                text=f"✅ Ваша анкета была одобрена!\n\n"
                     f"Теперь вы можете участвовать в игре. Удачи!"
            )
        else:
            anketa.status = "rejected"
            await query.edit_message_text(f"❌ Анкета #{anketa_id} отклонена.")
            
            await context.bot.send_message(
                chat_id=anketa.user_id,
                text=f"❌ Ваша анкета была отклонена.\n\n"
                     f"Причина: не указана. Обратитесь к администрации."
            )
        
        session.commit()
    finally:
        session.close()

async def add_anketnik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Назначить пользователя анкетником (только для разработчиков)"""
    user = update.effective_user
    if not user:
        return
    
    if user.id not in DEVELOPER_IDS:
        await update.message.reply_text("⛔ Только для разработчиков.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ Использование: /addanketnik @username\n"
            "или: /addanketnik ID"
        )
        return
    
    session = SessionLocal()
    try:
        arg = context.args[0].replace("@", "")
        target = None
        
        if arg.isdigit():
            target = session.query(User).filter_by(id=int(arg)).first()
        else:
            target = session.query(User).filter_by(username=arg).first()
        
        if not target:
            await update.message.reply_text(
                "❌ Пользователь не найден. Попросите его написать /start боту."
            )
            return
        
        target.is_anketnik = True
        session.commit()
        await update.message.reply_text(
            f"✅ Пользователь @{target.username or target.id} назначен анкетником!"
        )
    finally:
        session.close()

def is_anketnik(user_id: int) -> bool:
    """Проверка, является ли пользователь анкетником"""
    session = SessionLocal()
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if user and user.is_anketnik:
            return True
        return False
    finally:
        session.close()

def create_tables():
    """Создание таблиц в базе данных"""
    Base.metadata.create_all(bind=engine)
    logger.info("Таблицы базы данных созданы или уже существуют.")

def main():
    
    
    create_tables()

    
    
    application = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("anketa", anketa))
    application.add_handler(CommandHandler("anketa_review", anketa_review))
    application.add_handler(CommandHandler("warn", warn))
    application.add_handler(CommandHandler("deletemessages", deletemessages))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("rules", rules))
    application.add_handler(CommandHandler("lore", lore))
    application.add_handler(CommandHandler("feedback", feedback))
    application.add_handler(CommandHandler("addanketnik", add_anketnik))
    
    # Обработчик текста для анкет
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, anketa_handle_text))
    
    # Обработчик Callback (кнопки)
    application.add_handler(CallbackQueryHandler(anketa_callback, pattern="^anketa_"))
    
    # Обработчик неизвестных команд
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    
    logger.info("Бот Омниверс запущен!")
    application.run_polling()
    
    # Обработчик неизвестных команд
    
# Flask для Render
from flask import Flask
import threading

flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=10000)

threading.Thread(target=run_flask, daemon=True).start()

if __name__ == "__main__":
    main()