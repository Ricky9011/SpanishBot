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

import sqlite3
import random
from datetime import datetime, timedelta
from apscheduler.schedulers.background import backgroundScheduler

TOKEN = "7780645540:AAGvJBC0-83R2fl69aDjddT2DDImuU9BIs4"
conn = sqlite3.connect("spanish_bot.db")
cursor = conn.cursor()


# Crear tablas (ejecutar solo una vez)
cursor.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    level TEXT DEFAULT 'principiante',
    exercises INTEGER DEFAULT 0,
    referrals INTEGER DEFAULT 0,
    challenge_score INTEGER DEFAULT 0
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS challenges (
    challenge_id INTEGER PRIMARY KEY,
    description TEXT,
    start_date TEXT,
    end_date TEXT
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS paises (
    pais_id INTEGER PRIMARY KEY,
    nombre TEXT
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS frases_viajero (
    frase_id INTEGER PRIMARY KEY,
    pais_id INTEGER,
    frase TEXT,
    traduccion TEXT
)""")


# Datos iniciales (personaliza)
cursor.execute("INSERT OR IGNORE INTO paises VALUES (1, 'México'), (2, 'España')")
cursor.executemany(
    "INSERT OR IGNORE INTO frases_viajero (pais_id, frase, traduccion) VALUES (?, ?, ?)",
    [
        (1, "¿Dónde está el baño?", "Where is the bathroom?"),
        (2, "Una caña, por favor", "A beer, please"),
    ],
)
conn.commit()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Registrar usuario
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
        (user.id, user.username),
    )
    conn.commit()

    await update.message.reply_text(
        f"👋 ¡Hola {user.first_name}!\nUsa /ayuda para ver todos los comandos."
    )


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    📖 **Comandos Disponibles:**
    /ejercicio - Ejercicio diario personalizado
    /reto - ¡Compite en el desafío semanal!
    /viajero - Frases útiles para tu próximo viaje
    /progreso - Tu avance y estadísticas
    /nivel - Cambiar nivel (principiante/intermedio/avanzado)
    /invitar - Invita amigos y gana recompensas
    /premium - Información sobre contenido exclusivo
    /opinion - Enviar sugerencias o reportar errores
    """
    await update.message.reply_text(help_text)


async def ejercicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Obtener nivel del usuario
    cursor.execute("SELECT level FROM users WHERE user_id = ?", (user_id,))
    level = cursor.fetchone()[0]

    # Lógica de ejercicios según nivel
    ejercicios = {
        "principiante": ["Traduce: 'Good morning'", "Conjuga 'comer' en presente"],
        "intermedio": [
            "Usa el subjuntivo en: 'Es importante que...'",
            "Diferencias ser/estar",
        ],
        "avanzado": ["Expresiones idiomáticas mexicanas", "Jerga técnica en TI"],
    }

    ejercicio = random.choice(ejercicios[level])
    # Actualizar contador
    cursor.execute(
        "UPDATE users SET exercises = exercises + 1 WHERE user_id = ?", (user_id,)
    )
    conn.commit()

    await update.message.reply_text(f"📝 **Ejercicio ({level}):**\n{ejercicio}")


async def progreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor.execute(
        "SELECT level, exercises, referrals, challenge_score FROM users WHERE user_id = ?",
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


async def reto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Obtener reto activo
    hoy = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT * FROM challenges WHERE end_date >= ?", (hoy,))
    challenge = cursor.fetchone()

    if challenge:
        text = f"🏅 **Reto Actual:**\n{challenge[1]}\n\nEnvía tus respuestas con /reto_respuesta"
    else:
        text = "⚠️ No hay retos activos. ¡Vuelve pronto!"

    await update.message.reply_text(text)


async def viajero(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🇲🇽 México", callback_data="1"),
            InlineKeyboardButton("🇪🇸 España", callback_data="2"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Elige un país:", reply_markup=reply_markup)


async def viajero_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pais_id = query.data

    cursor.execute(
        "SELECT frase, traduccion FROM frases_viajero WHERE pais_id = ? ORDER BY RANDOM() LIMIT 1",
        (pais_id,),
    )
    frase = cursor.fetchone()

    await query.edit_message_text(
        f"🗣 **Frase útil:**\n{frase[0]}\n\n🇺🇸 Traducción:\n{frase[1]}"
    )


async def nivel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Principiante 🟢", "Intermedio 🟡", "Avanzado 🔴"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    await update.message.reply_text("Elige tu nivel:", reply_markup=reply_markup)


async def nivel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    nuevo_nivel = update.message.text.lower().split()[0]

    cursor.execute(
        "UPDATE users SET level = ? WHERE user_id = ?", (nuevo_nivel, user_id)
    )
    conn.commit()
    await update.message.reply_text(
        f"✅ Nivel actualizado a: {nuevo_nivel.capitalize()}"
    )


async def invitar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    enlace = f"https://t.me/{bot_username}?start=ref_{user_id}"

    await update.message.reply_text(
        f"🎁 Invita amigos y gana +5 ejercicios premium por cada uno que se una!\n"
        f"Tu enlace único:\n{enlace}"
    )


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌟 **Contenido Premium:**\n"
        "- Ejercicios con inteligencia artificial\n"
        "- Clases en vivo semanales\n"
        "- Certificado de progreso\n\n"
        "Precio: $9.99/mes\n"
        "Usa /invitar para obtener descuentos!"
    )


async def opinion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    feedback = " ".join(context.args)
    cursor.execute(
        "INSERT INTO feedback (user_id, comment) VALUES (?, ?)",
        (update.effective_user.id, feedback),
    )
    conn.commit()
    await update.message.reply_text("📩 ¡Gracias por tu opinión! Mejoraremos contigo.")


def main():
    application = Application.builder().token(TOKEN).build()

    # Comandos básicos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", ayuda))

    # Sistema de ejercicios
    application.add_handler(CommandHandler("ejercicio", ejercicio))
    application.add_handler(CommandHandler("progreso", progreso))

    # Retos y viajero
    application.add_handler(CommandHandler("reto", reto))
    application.add_handler(CommandHandler("viajero", viajero))
    application.add_handler(CallbackQueryHandler(viajero_handler))

    # Nivel e invitaciones
    application.add_handler(CommandHandler("nivel", nivel))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, nivel_handler)
    )
    application.add_handler(CommandHandler("invitar", invitar))

    # Premium y feedback
    application.add_handler(CommandHandler("premium", premium))
    application.add_handler(CommandHandler("opinion", opinion))

    # Tareas automáticas
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(enviar_recordatorio, "cron", hour=21)  # 9 PM UTC = 5 PM CDMX
    scheduler.start()

    application.run_polling()


if __name__ == "__main__":
    main()


async def enviar_recordatorio(context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT user_id FROM users")
    for user in cursor.fetchall():
        await context.bot.send_message(
            chat_id=user[0], text="⏰ ¡No olvides tu ejercicio diario! Usa /ejercicio"
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (código anterior)

    # Detectar referidos
    if context.args and context.args[0].startswith("ref_"):
        referrer_id = int(context.args[0].split("_")[1])
        cursor.execute(
            "UPDATE users SET referrals = referrals + 1 WHERE user_id = ?",
            (referrer_id,),
        )
        conn.commit()
