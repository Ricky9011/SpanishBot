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
import json
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


with open("ejercicios.json", "r", encoding="utf-8") as f:
    EJERCICIOS = json.load(f)


async def ejercicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        cursor.execute("SELECT level FROM users WHERE user_id = %s", (user_id,))
        nivel = cursor.fetchone()[0].lower()

        categorias = list(EJERCICIOS[nivel].keys())
        categoria = random.choice(categorias)
        ejercicio = random.choice(EJERCICIOS[nivel][categoria])

        context.user_data["respuesta_correcta"] = ejercicio["opciones"][
            ejercicio["respuesta"]
        ]

        mensaje = (
            f"📚 *Ejercicio de {categoria} ({nivel})*:\n{ejercicio['pregunta']}\n\n"
        )
        for idx, opcion in enumerate(ejercicio["opciones"]):
            mensaje += f"{idx + 1}. {opcion}\n"

        context.user_data["respuesta_correcta"] = ejercicio["respuesta"]
        context.user_data["opciones"] = ejercicio["opciones"]

        await update.message.reply_text(mensaje, parse_mode="Markdown")

    except Exception as e:
        print(f"Error en ejercicio: {e}")
        await update.message.reply_text(
            "⚠️ Ocurrió un error. Intenta de nuevo con /ejercicio"
        )
        # Rollback en caso de error de DB
        conn.rollback()


async def check_respuesta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        respuesta_usuario = update.message.text.strip()
        correcta_idx = context.user_data.get("respuesta_correcta", -1)
        opciones = context.user_data.get("opciones", [])

        # Convertir respuesta a número si es posible
        if respuesta_usuario.isdigit():
            respuesta_idx = int(respuesta_usuario) - 1
        else:
            respuesta_idx = next(
                (
                    i
                    for i, op in enumerate(opciones)
                    if op.lower() == respuesta_usuario.lower()
                ),
                -1,
            )

        if respuesta_idx == correcta_idx:
            mensaje = "✅ ¡Correcto! +1 punto"
            # Actualizar base de datos
            cursor.execute(
                "UPDATE users SET exercises = exercises + 1 WHERE user_id = %s",
                (update.effective_user.id,),
            )
            conn.commit()
        else:
            mensaje = (
                f"❌ Incorrecto. La respuesta correcta era: {opciones[correcta_idx]}"
            )

        await update.message.reply_text(mensaje)

    except Exception as e:
        print(f"Error en check_respuesta: {e}")
        await update.message.reply_text("⚠️ Error al verificar la respuesta")


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
