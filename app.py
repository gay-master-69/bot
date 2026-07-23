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
DEVELOPER_IDS = [6283690984]

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
    id = Column(BigInteger, primary_key=True, index=True)
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
    id = Column(BigInteger, primary_key=True, index=True)
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

Добро пожаловать в бота Омниверса! 🌟

Я помогу тебе:
• 📝 Оформить анкету
• 🎭 Управлять ролями

Чтобы начать, используй команды:
/anketa - создать или просмотреть анкету
/profile - просмотреть свой профиль
/help - список всех команд

Удачи в Омниверсе! 🎮
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
/profile - Просмотр профиля
/anketa - Создать или просмотреть анкету

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

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик неизвестных команд"""
    await update.message.reply_text(
        "❌ Неизвестная команда. Используйте /help для списка доступных команд."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Операция отменена.")

def main():
    """Основная функция запуска бота"""
    # Создаем таблицы в базе данных
    create_tables()
    
    # Создаем приложение
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
    
    # Обработчик неизвестных команд
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    
    # TODO: Добавить остальные обработчики команд и ConversationHandler
    
    # Запускаем бота
    logger.info("Бот Омниверс запущен!")
    application.run_polling()

# Flask для Render (этот блок ДОЛЖЕН быть вне main())
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