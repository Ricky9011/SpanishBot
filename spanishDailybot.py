# spanishDailybot.py - Versión completa con todas las funcionalidades
import os
import json
import re
import random
import uuid
import logging
import pytz
import psycopg2
from datetime import datetime, time
from dotenv import load_dotenv
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler
)
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager

# Configuración inicial
load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========================================
# CONFIGURACIÓN Y UTILIDADES
# ========================================

class Config:
    TOKEN = os.getenv("TOKEN")
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

    # Configuración de la base de datos
    DB_CONFIG = {
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432")
    }

# Pool de conexiones para la base de datos
connection_pool = None

def init_db_pool():
    global connection_pool
    connection_pool = SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        **Config.DB_CONFIG
    )

@contextmanager
def db_cursor():
    conn = connection_pool.getconn()
    try:
        with conn.cursor() as cursor:
            yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error en DB: {e}")
        raise
    finally:
        connection_pool.putconn(conn)

def create_tables():
    with db_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(50),
                level VARCHAR(20) DEFAULT 'principiante',
                exercises INT DEFAULT 0,
                referrals INT DEFAULT 0,
                challenge_score INT DEFAULT 0,
                completed_exercises TEXT DEFAULT '',
                streak_days INT DEFAULT 0,
                last_practice DATE
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id SERIAL PRIMARY KEY,
                user_id BIGINT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blocked_users (
                user_id BIGINT PRIMARY KEY,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS achievements (
                achievement_id SERIAL PRIMARY KEY,
                name VARCHAR(50) UNIQUE,
                description TEXT,
                icon VARCHAR(20)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_achievements (
                user_id BIGINT,
                achievement_id INT,
                earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, achievement_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_reminders (
                user_id BIGINT PRIMARY KEY,
                reminder_time TIME,
                timezone VARCHAR(50)
            )
        """)

# Inicializar el pool de conexiones y crear tablas
init_db_pool()
create_tables()

# Carga de recursos
with open("ejercicios.json", "r", encoding="utf-8") as f:
    EJERCICIOS = json.load(f)

with open("curiosidades.json", "r", encoding="utf-8") as f:
    CURIOSIDADES = json.load(f)["curiosidades"]

# Estados para la conversación
FEEDBACK = 1
ADMIN_ACTION = 2

# ========================================
# FUNCIONES UTILITARIAS
# ========================================

def sanitize_text(text: str, max_length=200) -> str:
    """Escapa caracteres especiales y limita la longitud del texto"""
    if not text:
        return ""

    # Escapar caracteres especiales de Markdown
    text = re.sub(r"([_*\[\]()~`>#+\-=|{}\.!])", r"\\\1", text)

    # Eliminar posibles inyecciones SQL
    text = re.sub(r"[;\-\-]", "", text)

    # Limitar longitud
    if len(text) > max_length:
        text = text[:max_length-3] + "..."

    return text

def validate_input(text: str, max_length=1000) -> str:
    """Valida y limpia la entrada del usuario"""
    if len(text) > max_length:
        raise ValueError("La entrada es demasiado larga")
    return sanitize_text(text)

def get_reply_func(update: Update):
    """Obtiene la función de respuesta apropiada"""
    if update.message:
        return update.message.reply_text
    elif update.callback_query and update.callback_query.message:
        return update.callback_query.message.reply_text
    elif update.effective_message:
        return update.effective_message.reply_text
    return None

def is_admin(user_id: int) -> bool:
    """Verifica si el usuario es administrador"""
    return user_id == Config.ADMIN_USER_ID

def generate_progress_bar(percentage: int) -> str:
    """Genera una barra de progreso visual"""
    filled = '▓' * int(percentage / 5)
    empty = '░' * (20 - len(filled))
    return f"{filled}{empty} {percentage}%"

# ========================================
# MANEJO DE USUARIOS Y PROGRESO
# ========================================

async def check_user_blocked(user_id: int) -> bool:
    """Verifica si un usuario está bloqueado"""
    try:
        with db_cursor() as cursor:
            cursor.execute("SELECT 1 FROM blocked_users WHERE user_id = %s", (user_id,))
            return bool(cursor.fetchone())
    except Exception as e:
        logger.error(f"Error al verificar bloqueo: {e}")
        return False

async def register_user(user_id: int, username: str):
    """Registra un nuevo usuario en la base de datos"""
    try:
        with db_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                (user_id, username)
            )
    except Exception as e:
        logger.error(f"Error al registrar usuario: {e}")

async def update_streak(user_id: int):
    """Actualiza la racha de días consecutivos de práctica"""
    try:
        today = datetime.now().date()
        with db_cursor() as cursor:
            cursor.execute(
                "SELECT last_practice, streak_days FROM users WHERE user_id = %s",
                (user_id,)
            )
            result = cursor.fetchone()

            if result:
                last_practice, streak_days = result
                new_streak = 1 if not last_practice or (today - last_practice).days > 1 else streak_days + 1

                cursor.execute(
                    "UPDATE users SET streak_days = %s, last_practice = %s WHERE user_id = %s",
                    (new_streak, today, user_id)
                )
                return new_streak
    except Exception as e:
        logger.error(f"Error al actualizar racha: {e}")
    return 0

async def grant_achievement(user_id: int, achievement_name: str):
    """Otorga un logro a un usuario"""
    try:
        with db_cursor() as cursor:
            # Obtener ID del logro
            cursor.execute(
                "SELECT achievement_id FROM achievements WHERE name = %s",
                (achievement_name,)
            )
            achievement_id = cursor.fetchone()

            if achievement_id:
                achievement_id = achievement_id[0]
                # Verificar si el usuario ya tiene el logro
                cursor.execute(
                    "SELECT 1 FROM user_achievements WHERE user_id = %s AND achievement_id = %s",
                    (user_id, achievement_id)
                )
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO user_achievements (user_id, achievement_id) VALUES (%s, %s)",
                        (user_id, achievement_id)
                    )
                    return True
    except Exception as e:
        logger.error(f"Error al otorgar logro: {e}")
    return False

# ========================================
# HANDLERS PRINCIPALES (COMPLETOS)
# ========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Verificar si el usuario está bloqueado
    if await check_user_blocked(user_id):
        await update.message.reply_text("⛔ Tu acceso a este bot ha sido bloqueado.")
        return

    # Registrar usuario
    await register_user(user_id, user.username)

    # Manejar referidos
    ref_bonus = False
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0].split("_")[1])
            with db_cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET referrals = referrals + 1 WHERE user_id = %s",
                    (referrer_id,)
                )
            # Otorgar logro por referir
            await grant_achievement(referrer_id, "Embajador")
            ref_bonus = True
        except Exception as e:
            logger.error(f"Error en referencia: {e}")

    # Mensaje de bienvenida
    welcome_msg = (
        f"👋 ¡Hola {user.first_name}! {'🎉 Has sido referido por un amigo. ' if ref_bonus else ''}"
        "Bienvenido a tu práctica diaria de español.\n\n"
        "Usa /ayuda para ver los comandos disponibles."
    )

    # Teclado principal con botones visuales
    keyboard = [
        ["📝 Ejercicio", "🏆 Reto Diario"],
        ["📊 Progreso", "🎖️ Mis Logros"],
        ["⚙️ Cambiar Nivel", "📚 Curiosidad"],
        ["👥 Invitar Amigos", "💎 Premium"],
        ["💬 Enviar Opinión"]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        input_field_placeholder="Elige una opción"
    )

    await update.message.reply_text(welcome_msg, reply_markup=reply_markup)

    # Otorgar logro de nuevo usuario
    await grant_achievement(user_id, "Nuevo Estudiante")

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
    /logros - Ver tus logros obtenidos
    """
    await update.message.reply_text(help_text)

async def ejercicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reply_func = get_reply_func(update)

    # Verificar usuario bloqueado
    if await check_user_blocked(user_id):
        await reply_func("⛔ Tu acceso está bloqueado.")
        return

    try:
        # Actualizar racha de práctica
        streak = await update_streak(user_id)

        with db_cursor() as cursor:
            # Obtener nivel y ejercicios completados
            cursor.execute(
                "SELECT level, completed_exercises FROM users WHERE user_id = %s",
                (user_id,)
            )
            result = cursor.fetchone()
            nivel = result[0].lower() if result else 'principiante'
            completed_exercises = result[1].split(",") if result and result[1] else []

            # Obtener todos los ejercicios disponibles para el nivel
            all_exercises = []
            for categoria, ejercicios in EJERCICIOS[nivel].items():
                for idx, ejercicio in enumerate(ejercicios):
                    exercise_id = f"{categoria}_{idx}"
                    all_exercises.append((categoria, idx, ejercicio, exercise_id))

            # Filtrar ejercicios no completados
            available_exercises = [ex for ex in all_exercises if ex[3] not in completed_exercises]

            # Si no hay ejercicios disponibles, reiniciar el progreso
            if not available_exercises:
                await reply_func("🎉 ¡Has completado todos los ejercicios! Reiniciando progreso...")
                cursor.execute(
                    "UPDATE users SET completed_exercises = '' WHERE user_id = %s",
                    (user_id,)
                )
                available_exercises = all_exercises

            # Seleccionar un ejercicio aleatorio
            categoria, idx, ejercicio, exercise_id = random.choice(available_exercises)

            # Sanitizar y formatear mensaje
            categoria_safe = sanitize_text(categoria)
            nivel_safe = sanitize_text(nivel)
            pregunta_safe = sanitize_text(ejercicio["pregunta"])

            mensaje = (
                f"📚 *Ejercicio de {categoria_safe} ({nivel_safe})*\n"
                f"🔥 Racha actual: {streak} días\n\n"
                f"{pregunta_safe}\n\n"
            )

            for opt_idx, opcion in enumerate(ejercicio["opciones"]):
                opcion_safe = sanitize_text(opcion)
                mensaje += f"{opt_idx + 1}. {opcion_safe}\n"

            # Guardar en contexto
            context.user_data["current_exercise"] = {
                "id": exercise_id,
                "correct": ejercicio["respuesta"],
                "options": ejercicio["opciones"]
            }

            await reply_func(mensaje, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en ejercicio: {e}")
        await reply_func("⚠️ Error al cargar ejercicio. Intenta nuevamente.")

async def check_respuesta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = context.user_data

    # Verificar usuario bloqueado
    if await check_user_blocked(user_id):
        return

    # Validar que hay un ejercicio activo
    if "current_exercise" not in user_data:
        await update.message.reply_text("❌ No hay ejercicio activo. Usa /ejercicio.")
        return

    # Validar entrada del usuario
    try:
        respuesta_usuario = validate_input(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Entrada no válida. Por favor usa el número de opción.")
        return

    # Obtener datos del ejercicio
    ejercicio_data = user_data["current_exercise"]
    correcta_idx = ejercicio_data["correct"]
    opciones = ejercicio_data["options"]
    exercise_id = ejercicio_data["id"]

    # Inicializar respuesta_idx con valor por defecto
    respuesta_idx = -1

    # Convertir respuesta a índice
    try:
        if respuesta_usuario.isdigit():
            respuesta_idx = int(respuesta_usuario) - 1
        else:
            # Buscar coincidencia exacta (ignorando mayúsculas)
            for idx, opcion_text in enumerate(opciones):
                if respuesta_usuario.lower() == opcion_text.lower():
                    respuesta_idx = idx
                    break
    except Exception as e:
        logger.error(f"Error al convertir respuesta: {e}")

    if respuesta_idx == correcta_idx:
        # Respuesta correcta
        try:
            with db_cursor() as cursor:
                # Actualizar progreso
                cursor.execute(
                    "UPDATE users SET exercises = exercises + 1 WHERE user_id = %s",
                    (user_id,)
                )

                # Registrar ejercicio completado
                cursor.execute(
                    "SELECT completed_exercises FROM users WHERE user_id = %s",
                    (user_id,)
                )
                completed = cursor.fetchone()[0] or ""
                completed_list = completed.split(",") if completed else []

                if exercise_id not in completed_list:
                    completed_list.append(exercise_id)
                    cursor.execute(
                        "UPDATE users SET completed_exercises = %s WHERE user_id = %s",
                        (",".join(completed_list), user_id)
                    )

                # Obtener nuevo total
                cursor.execute("SELECT exercises FROM users WHERE user_id = %s", (user_id,))
                nuevos_ejercicios = cursor.fetchone()[0]

            # Mensaje de éxito
            keyboard = [
                [InlineKeyboardButton("➡️ Siguiente Ejercicio", callback_data="next_exercise")],
                [
                    InlineKeyboardButton("📊 Ver Progreso", callback_data="show_progress"),
                    InlineKeyboardButton("🏆 Reto Diario", callback_data="daily_challenge")
                ]
            ]

            # Verificar logros
            achievement_msg = ""
            if nuevos_ejercicios == 10:
                await grant_achievement(user_id, "Aprendiz")
                achievement_msg = "\n\n🎉 ¡Logro desbloqueado: Aprendiz!"
            elif nuevos_ejercicios == 50:
                await grant_achievement(user_id, "Experto")
                achievement_msg = "\n\n🏆 ¡Logro desbloqueado: Experto!"

            await update.message.reply_text(
                f"✅ ¡Correcto! +1 punto\n🏆 Total: {nuevos_ejercicios}{achievement_msg}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

            # Mostrar curiosidad
            await show_curiosity(update, context)

            # Limpiar datos del ejercicio
            user_data.pop("current_exercise", None)

        except Exception as e:
            logger.error(f"Error en respuesta correcta: {e}")
            await update.message.reply_text("⚠️ Error al actualizar tu progreso")
    else:
        # Respuesta incorrecta
        correct_option = opciones[correcta_idx]
        await update.message.reply_text(
            f"✨ Casi lo logras. La respuesta correcta era: *{correct_option}*",
            parse_mode="Markdown"
        )

        # Incrementar intentos fallidos
        user_data["attempts"] = user_data.get("attempts", 0) + 1

        if user_data["attempts"] > 2:
            await update.message.reply_text(
                "🔁 Demasiados intentos. Prueba un nuevo ejercicio con /ejercicio"
            )
            user_data.pop("current_exercise", None)
            user_data.pop("attempts", None)
        else:
            # Botón para reintentar
            keyboard = [[InlineKeyboardButton("🔄 Intentar de nuevo", callback_data="retry_exercise")]]
            await update.message.reply_text(
                "¿Quieres intentar este ejercicio otra vez?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

async def show_curiosity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra una curiosidad aleatoria sobre el español"""
    curiosidad = random.choice(CURIOSIDADES)
    mensaje = (
        f"🧠 *Curiosidad del español ({curiosidad['categoria']}):*\n\n"
        f"{curiosidad['texto']}"
    )
    reply_func = get_reply_func(update)
    await reply_func(mensaje, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja acciones de botones inline"""
    query = update.callback_query
    await query.answer()

    if query.data == "next_exercise":
        context.user_data.pop("current_exercise", None)
        context.user_data.pop("attempts", None)
        await ejercicio(update, context)
    elif query.data == "show_progress":
        await progreso(update, context)
    elif query.data == "daily_challenge":
        await reto(update, context)
    elif query.data == "retry_exercise":
        await ejercicio(update, context)

    # Eliminar mensaje anterior con botones
    try:
        await query.message.delete()
    except:
        pass

async def progreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reply_func = get_reply_func(update)

    try:
        with db_cursor() as cursor:
            cursor.execute(
                """
                SELECT level, exercises, referrals, challenge_score, streak_days
                FROM users WHERE user_id = %s
                """,
                (user_id,)
            )
            data = cursor.fetchone()

            if not data:
                await reply_func("❌ No se encontraron datos de progreso.")
                return

            nivel, ejercicios, referidos, puntos_reto, racha = data

            # Calcular porcentaje de completitud
            cursor.execute(
                "SELECT COUNT(*) FROM user_achievements WHERE user_id = %s",
                (user_id,)
            )
            logros = cursor.fetchone()[0]

            # Generar barra de progreso
            nivel_base = 50 if nivel == "principiante" else 100 if nivel == "intermedio" else 150
            porcentaje = min(100, int((ejercicios / nivel_base) * 100))
            progress_bar = generate_progress_bar(porcentaje)

            progreso_text = (
                f"📈 **Tu Progreso**\n\n"
                f"📊 Nivel: {nivel.capitalize()}\n"
                f"✅ Ejercicios completados: {ejercicios}\n"
                f"🔥 Racha actual: {racha} días\n"
                f"👥 Amigos invitados: {referidos}\n"
                f"🏆 Puntos en retos: {puntos_reto}\n"
                f"🎖️ Logros obtenidos: {logros}\n\n"
                f"📊 Progreso del nivel:\n{progress_bar}"
            )

            await reply_func(progreso_text)

    except Exception as e:
        logger.error(f"Error en progreso: {e}")
        await reply_func("⚠️ Error al obtener tu progreso")

async def logros(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra los logros obtenidos por el usuario"""
    user_id = update.effective_user.id
    reply_func = get_reply_func(update)

    try:
        with db_cursor() as cursor:
            cursor.execute(
                """
                SELECT a.name, a.description, a.icon
                FROM user_achievements ua
                JOIN achievements a ON ua.achievement_id = a.achievement_id
                WHERE ua.user_id = %s
                """,
                (user_id,)
            )
            logros = cursor.fetchall()

            if not logros:
                await reply_func("🎯 Aún no has obtenido logros. ¡Sigue practicando!")
                return

            logros_text = "🏆 **Logros Obtenidos:**\n\n"
            for nombre, descripcion, icono in logros:
                logros_text += f"{icono} *{nombre}*\n{descripcion}\n\n"

            await reply_func(logros_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en logros: {e}")
        await reply_func("⚠️ Error al obtener tus logros")

async def nivel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra opciones para cambiar el nivel del usuario"""
    keyboard = [
        ["Principiante", "Intermedio"],
        ["Avanzado"]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        one_time_keyboard=True,
        resize_keyboard=True,
        input_field_placeholder="Elige tu nivel"
    )

    await update.message.reply_text(
        "📊 Selecciona tu nuevo nivel de español:",
        reply_markup=reply_markup
    )

async def set_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Actualiza el nivel del usuario en la base de datos"""
    try:
        user_id = update.effective_user.id
        new_level = update.message.text.strip().lower()
        valid_levels = ["principiante", "intermedio", "avanzado"]

        if new_level not in valid_levels:
            await update.message.reply_text(
                "❌ Nivel no válido. Por favor selecciona una opción válida del teclado."
            )
            return

        with db_cursor() as cursor:
            cursor.execute(
                "UPDATE users SET level = %s, completed_exercises = '' WHERE user_id = %s",
                (new_level, user_id)
            )

        # Teclado principal para continuar
        keyboard = [
            ["📝 Ejercicio", "🏆 Reto Diario"],
            ["📊 Progreso", "🎖️ Mis Logros"]
        ]
        reply_markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            input_field_placeholder="Continúa practicando"
        )

        await update.message.reply_text(
            f"✅ Nivel actualizado a *{new_level.capitalize()}*!",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error al cambiar nivel: {e}")
        await update.message.reply_text(
            "⚠️ Error al actualizar tu nivel. Intenta nuevamente.",
            reply_markup=ReplyKeyboardRemove()
        )

async def invitar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        bot_username = context.bot.username
        ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"

        mensaje = f"""
        📨 ¡Invita a tus amigos y gana recompensas!

        Comparte este enlace único:
        {ref_link}

        Por cada amigo que se una usando tu enlace:
        - ✅ Obtendrás 1 punto de referido
        - 🎁 Tu amigo recibirá un bonus especial
        - 📈 Aparecerás en el ranking de invitaciones (/progreso)
        """
        await update.message.reply_text(mensaje)

    except Exception as e:
        print(f"Error en invitar: {e}")
        await update.message.reply_text("⚠️ Error al generar enlace de invitación")

async def reto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        reply_func = get_reply_func(update)

        # Verificar usuario bloqueado
        if await check_user_blocked(user_id):
            await reply_func("⛔ Tu acceso está bloqueado.")
            return

        # Usar nivel avanzado para retos
        nivel_reto = "avanzado"
        categoria = random.choice(list(EJERCICIOS[nivel_reto].keys()))
        ejercicio = random.choice(EJERCICIOS[nivel_reto][categoria])

        # Sanitizar y formatear
        categoria_safe = sanitize_text(categoria)
        pregunta_safe = sanitize_text(ejercicio["pregunta"])

        mensaje = (
            f"🔥 *Reto Diario - {categoria_safe} ({nivel_reto.capitalize()})*\n\n"
            f"{pregunta_safe}\n\n"
        )

        for idx, opcion in enumerate(ejercicio["opciones"]):
            opcion_safe = sanitize_text(opcion)
            mensaje += f"{idx + 1}. {opcion_safe}\n"

        mensaje += "\n🏆 ¡Responde correctamente para ganar puntos extra!"

        # Guardar en contexto como reto
        context.user_data["current_exercise"] = {
            "id": f"reto_{uuid.uuid4().hex[:6]}",
            "correct": ejercicio["respuesta"],
            "options": ejercicio["opciones"],
            "is_challenge": True  # Marcar como reto especial
        }

        await reply_func(mensaje, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en reto: {e}")
        reply_func = get_reply_func(update)
        await reply_func("⚠️ Error al cargar el reto diario. Intenta más tarde.")

async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = """
    💎 *Contenido Premium* 💎

    Próximamente tendrás acceso a:
    - Ejercicios exclusivos de alta dificultad
    - Explicaciones detalladas paso a paso
    - Sesiones de mentoría personalizada
    - Certificados de progreso

    ¡Estamos trabajando para ofrecerte la mejor experiencia de aprendizaje!
    """
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def opinion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Por favor, escribe tu opinión, sugerencia o reporte de error."
    )
    return FEEDBACK

async def recibir_opinion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        feedback_text = validate_input(update.message.text, max_length=1000)

        with db_cursor() as cursor:
            cursor.execute(
                "INSERT INTO feedback (user_id, message) VALUES (%s, %s)",
                (user_id, feedback_text)
            )

        # Mensaje de agradecimiento
        await update.message.reply_text(
            "✅ ¡Gracias por tu opinión! Tu feedback nos ayuda a mejorar."
        )

        # Mostrar teclado principal
        keyboard = [
            ["📝 Ejercicio", "🏆 Reto Diario"],
            ["📊 Progreso", "🎖️ Mis Logros"]
        ]
        reply_markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            input_field_placeholder="Continúa practicando"
        )
        await update.message.reply_text(
            "¿Te gustaría continuar practicando?",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error al guardar opinión: {e}")
        await update.message.reply_text(
            "⚠️ Error al guardar tu opinión. Por favor intenta nuevamente.",
            reply_markup=ReplyKeyboardRemove()
        )

    return ConversationHandler.END

# ========================================
# MANEJO DE BOTONES DEL TECLADO PRINCIPAL
# ========================================

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones del teclado principal"""
    text = update.message.text

    if text == "📝 Ejercicio":
        await ejercicio(update, context)
    elif text == "🏆 Reto Diario":
        await reto(update, context)
    elif text == "📊 Progreso":
        await progreso(update, context)
    elif text == "🎖️ Mis Logros":
        await logros(update, context)
    elif text == "⚙️ Cambiar Nivel":
        await nivel(update, context)
    elif text == "📚 Curiosidad":
        await show_curiosity(update, context)
    elif text == "👥 Invitar Amigos":
        await invitar(update, context)
    elif text == "💎 Premium":
        await premium(update, context)
    elif text == "💬 Enviar Opinión":
        await opinion(update, context)
    else:
        # Si no es un botón reconocido, intentar verificar como respuesta
        await check_respuesta(update, context)
# ========================================
# FUNCIÓN PARA RECORDATORIOS DIARIOS (AÑADIR ANTES DE main())
# ========================================

async def enviar_recordatorio(context: ContextTypes.DEFAULT_TYPE):
    """Envía recordatorios diarios a los usuarios"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Obtener todos los usuarios
        cursor.execute("SELECT user_id FROM users")
        for user in cursor.fetchall():
            try:
                await context.bot.send_message(
                    chat_id=user[0],
                    text="⏰ ¡No olvides practicar hoy! Usa /ejercicio para tu práctica diaria.",
                )
            except Exception as e:
                print(f"Error enviando recordatorio a {user[0]}: {e}")

    except Exception as e:
        print(f"Error en recordatorio: {e}")
    finally:
        cursor.close()
        conn.close()

# ========================================
# CONFIGURACIÓN PRINCIPAL
# ========================================

def main():
    application = Application.builder().token(Config.TOKEN).build()

    # Handlers principales
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("ejercicio", ejercicio))
    application.add_handler(CommandHandler("progreso", progreso))
    application.add_handler(CommandHandler("logros", logros))
    application.add_handler(CommandHandler("invitar", invitar))
    application.add_handler(CommandHandler("reto", reto))
    application.add_handler(CommandHandler("premium", premium))
    application.add_handler(CommandHandler("nivel", nivel))

    # Handler para botones inline
    application.add_handler(CallbackQueryHandler(button_handler))

    # Handler para botones del teclado principal
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_main_menu
    ))

    # Handler para opiniones
    opinion_conv = ConversationHandler(
        entry_points=[CommandHandler("opinion", opinion)],
        states={
            FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_opinion)]
        },
        fallbacks=[]
    )
    application.add_handler(opinion_conv)

    # Handler para cambiar nivel
    application.add_handler(MessageHandler(
        filters.Regex(r"^(Principiante|Intermedio|Avanzado)$"),
        set_level
    ))

    # Programar recordatorios diarios
    application.job_queue.run_daily(
        enviar_recordatorio,
        time=time(hour=9, minute=0, tzinfo=pytz.utc),  # 9:00 UTC
        days=(0, 1, 2, 3, 4, 5, 6)
    )

    # Iniciar el bot
    application.run_polling()

if __name__ == "__main__":
    main()
