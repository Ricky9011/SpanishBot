from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
import random
from datetime import time, datetime, timezone

import pytz

TOKEN = os.getenv("TOKEN")

# Conexión PostgreSQL
conn = psycopg2.connect(
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    host=os.getenv("DB_HOST", "localhost"),
    port=os.getenv("DB_PORT", "5432"),
)
cursor = conn.cursor()

# Crear tablas
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username VARCHAR(50),
        level VARCHAR(20) DEFAULT 'principiante',
        exercises INT DEFAULT 0,
        referrals INT DEFAULT 0,
        challenge_score INT DEFAULT 0
    )
""")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS challenges (
        challenge_id SERIAL PRIMARY KEY,
        description TEXT,
        start_date TIMESTAMP,
        end_date TIMESTAMP
    )
""")

conn.commit()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        cursor.execute(
            "INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user.id, user.username),
        )
        conn.commit()
        await update.message.reply_text(
            f"👋 ¡Hola {user.first_name}!\nUsa /ayuda para ver los comandos."
        )

        # Manejar referidos
        if context.args and context.args[0].startswith("ref_"):
            referrer_id = int(context.args[0].split("_")[1])
            cursor.execute(
                "UPDATE users SET referrals = referrals + 1 WHERE user_id = %s",
                (referrer_id,),
            )
            conn.commit()

    except Exception as e:
        print(f"Error en start: {e}")


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    📖 **Comandos Disponibles:**
    /ejercicio - Ejercicio diario personalizado
    /reto - ¡Compite en el desafío semanal!
    /progreso - Tu avance y estadísticas
    /nivel - Cambiar nivel (principiante/intermedio/avanzado)
    /invitar - Invita amigos y gana recompensas
    /premium - Información sobre contenido exclusivo
    /opinion - Enviar sugerencias o reportar errores
    """
    await update.message.reply_text(help_text)


async def ejercicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        cursor.execute("SELECT level FROM users WHERE user_id = %s", (user_id,))
        level = cursor.fetchone()[0]

        ejercicios = {
            "principiante": ["Traduce: 'Good morning'", "Conjuga 'comer' en presente"],
            "intermedio": [
                "Usa el subjuntivo en: 'Es importante que...'",
                "Diferencias ser/estar",
            ],
            "avanzado": ["Expresiones idiomáticas mexicanas", "Jerga técnica en TI"],
        }

        ejercicio = random.choice(ejercicios[level])
        cursor.execute(
            "UPDATE users SET exercises = exercises + 1 WHERE user_id = %s", (user_id,)
        )
        conn.commit()
        await update.message.reply_text(f"📝 **Ejercicio ({level}):**\n{ejercicio}")

    except Exception as e:
        print(f"Error en ejercicio: {e}")


async def progreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        cursor.execute(
            "SELECT level, exercises, referrals, challenge_score FROM users WHERE user_id = %s",
            (user_id,),
        )
        data = cursor.fetchone()

        progreso_text = f"""
        📈 **Tu Progreso:**
        - Nivel: {data[0].capitalize()}
        - Ejercicios completados: {data[1]}
        - Amigos invitados: {data[2]}
        - Puntos en retos: {data[3]}
        """
        await update.message.reply_text(progreso_text)

    except Exception as e:
        print(f"Error en progreso: {e}")


async def enviar_recordatorio(context: ContextTypes.DEFAULT_TYPE):
    try:
        # Crear nueva conexión y cursor para cada ejecución
        with psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id FROM users")
                for user in cursor.fetchall():
                    await context.bot.send_message(
                        chat_id=user[0],
                        text="⏰ ¡No olvides tu ejercicio diario! Usa /ejercicio",
                    )
    except Exception as e:
        print(f"Error en recordatorio: {e}")


def main():
    application = Application.builder().token(TOKEN).build()

    # Handlers principales
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("ejercicio", ejercicio))
    application.add_handler(CommandHandler("progreso", progreso))

    application.job_queue.run_daily(
        enviar_recordatorio, time=time(hour=21, tzinfo=timezone.utc)
    )

    application.run_polling()


if __name__ == "__main__":
    main()
