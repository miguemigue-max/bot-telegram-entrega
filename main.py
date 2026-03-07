import os
import time
import telebot
from telebot import apihelper, types

# =========================================================
# CONFIGURACIÓN
# =========================================================
# En PythonAnywhere free normalmente se usa proxy
apihelper.proxy = {
    'http': 'http://proxy.server:3128',
    'https': 'http://proxy.server:3128'
}

TOKEN = "PON_AQUI_TU_TOKEN_NUEVO"
ADMIN_ID = 5220834019  # mejor como int
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# Archivos de persistencia
DB_FILE = "banco.txt"
PERFILES_FILE = "perfiles.txt"
HISTORIAL_FILE = "historial.txt"
SEGURIDAD_FILE = "seguridad.txt"

# =========================================================
# UTILIDADES DE ARCHIVOS
# =========================================================
def asegurar_archivo(path):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as _:
            pass

for f in [DB_FILE, PERFILES_FILE, HISTORIAL_FILE, SEGURIDAD_FILE]:
    asegurar_archivo(f)

def leer_datos():
    usuarios = {}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if ":" in linea:
                try:
                    uid, saldo = linea.split(":")
                    usuarios[uid] = float(saldo)
                except:
                    continue
    return usuarios

def guardar_datos(usuarios):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        for uid, saldo in usuarios.items():
            f.write(f"{uid}:{saldo}\n")

def leer_perfiles():
    perfiles = {}
    with open(PERFILES_FILE, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if "|" in linea:
                partes = linea.split("|", 2)
                if len(partes) == 3:
                    uid, username, nombre = partes
                    perfiles[uid] = {
                        "username": username,
                        "nombre": nombre
                    }
    return perfiles

def guardar_perfiles(perfiles):
    with open(PERFILES_FILE, "w", encoding="utf-8") as f:
        for uid, data in perfiles.items():
            f.write(f"{uid}|{data['username']}|{data['nombre']}\n")

def registrar_usuario_si_no_existe(message):
    uid = str(message.from_user.id)

    # saldo
    usuarios = leer_datos()
    if uid not in usuarios:
        usuarios[uid] = 0.0
        guardar_datos(usuarios)

    # perfil
    perfiles = leer_perfiles()
    if uid not in perfiles:
        username = message.from_user.username or ""
        nombre = message.from_user.first_name or "Usuario"
        perfiles[uid] = {
            "username": username,
            "nombre": nombre
        }
        guardar_perfiles(perfiles)

    # seguridad
    pin, intentos, estado = leer_seguridad(uid)
    if pin is None:
        guardar_seguridad(uid, "0000", 0, "ACTIVO")

def leer_seguridad(uid):
    uid = str(uid)
    with open(SEGURIDAD_FILE, "r", encoding="utf-8") as f:
        for linea in f:
            partes = linea.strip().split("|")
            if len(partes) == 4 and partes[0] == uid:
                return partes[1], int(partes[2]), partes[3]
    return None, None, None

def guardar_seguridad(uid, pin, intentos, estado):
    uid = str(uid)
    datos = {}

    with open(SEGURIDAD_FILE, "r", encoding="utf-8") as f:
        for linea in f:
            partes = linea.strip().split("|")
            if len(partes) == 4:
                datos[partes[0]] = [partes[1], partes[2], partes[3]]

    datos[uid] = [str(pin), str(intentos), str(estado)]

    with open(SEGURIDAD_FILE, "w", encoding="utf-8") as f:
        for user_id, d in datos.items():
            f.write(f"{user_id}|{d[0]}|{d[1]}|{d[2]}\n")

def agregar_historial(uid, texto):
    uid = str(uid)
    linea_nueva = f"{uid}|{time.strftime('%Y-%m-%d %H:%M:%S')}|{texto}\n"
    with open(HISTORIAL_FILE, "a", encoding="utf-8") as f:
        f.write(linea_nueva)

def leer_historial(uid, limite=10):
    uid = str(uid)
    movimientos = []
    with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
        for linea in f:
            partes = linea.strip().split("|", 2)
            if len(partes) == 3 and partes[0] == uid:
                movimientos.append(f"{partes[1]} - {partes[2]}")
    return movimientos[-limite:]

# =========================================================
# MENÚS
# =========================================================
def menu_usuario():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("💰 Mi Saldo"),
        types.KeyboardButton("🧾 Mi Extracto"),
        types.KeyboardButton("💸 Transferir"),
        types.KeyboardButton("⚙️ Ajustes")
    )
    return markup

def menu_admin():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("💳 Balance CUP"),
        types.KeyboardButton("🔓 Desbloquear ID"),
        types.KeyboardButton("🏠 Menú Usuario")
    )
    return markup

# =========================================================
# HELPERS
# =========================================================
def es_admin(user_id):
    return int(user_id) == ADMIN_ID

def cuenta_activa(uid):
    pin, intentos, estado = leer_seguridad(uid)
    return estado == "ACTIVO"

def pedir_menu_principal(chat_id, texto="🏦 <b>MBanks</b> operativo. Elige una opción:"):
    bot.send_message(chat_id, texto, reply_markup=menu_usuario())

# =========================================================
# /START Y COMANDOS
# =========================================================
@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.from_user.id)
    registrar_usuario_si_no_existe(message)

    pin, intentos, estado = leer_seguridad(uid)

    if estado == "SUSPENDIDO":
        bot.send_message(
            message.chat.id,
            "❌ Tu cuenta está <b>SUSPENDIDA</b>.\n"
            "Debes contactar al administrador para desbloquearla."
        )
        return

    nombre = message.from_user.first_name or "Usuario"
    bot.send_message(
        message.chat.id,
        f"👋 Hola, <b>{nombre}</b>.\nBienvenido a <b>MBanks</b>.",
        reply_markup=menu_usuario()
    )

@bot.message_handler(commands=['admin'])
def admin_directo(message):
    if es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "🛠 <b>Panel Admin</b>:", reply_markup=menu_admin())
    else:
        bot.send_message(message.chat.id, "⛔ No tienes acceso al panel admin.")

# =========================================================
# CALLBACKS
# =========================================================
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = str(call.from_user.id)

    if call.data == "cambiar_pin":
        pin, intentos, estado = leer_seguridad(uid)
        if estado == "SUSPENDIDO":
            bot.answer_callback_query(call.id, "Cuenta suspendida.")
            bot.send_message(call.message.chat.id, "❌ Tu cuenta está suspendida.")
            return

        msg = bot.send_message(call.message.chat.id, "🔐 Introduce tu PIN actual:")
        bot.register_next_step_handler(msg, validar_pin_actual_para_cambio)

    elif call.data == "entrar_admin":
        if es_admin(call.from_user.id):
            bot.send_message(call.message.chat.id, "🛠 <b>Panel Admin</b>:", reply_markup=menu_admin())
        else:
            bot.send_message(call.message.chat.id, "⛔ No autorizado.")

    bot.answer_callback_query(call.id)

# =========================================================
# FLUJO: CAMBIO DE PIN
# =========================================================
def validar_pin_actual_para_cambio(message):
    uid = str(message.from_user.id)

    if message.text and message.text.startswith("/"):
        start(message)
        return

    pin_real, intentos, estado = leer_seguridad(uid)

    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta suspendida.")
        return

    pin_ingresado = (message.text or "").strip()

    if pin_ingresado != pin_real:
        intentos += 1
        if intentos >= 3:
            guardar_seguridad(uid, pin_real, intentos, "SUSPENDIDO")
            bot.send_message(
                message.chat.id,
                "🚫 Has fallado 3 veces. Tu cuenta fue <b>SUSPENDIDA</b>."
            )
            return
        else:
            guardar_seguridad(uid, pin_real, intentos, "ACTIVO")
            msg = bot.send_message(
                message.chat.id,
                f"❌ PIN incorrecto. Intento {intentos}/3.\nVuelve a introducir tu PIN actual:"
            )
            bot.register_next_step_handler(msg, validar_pin_actual_para_cambio)
            return

    # PIN correcto: reset intentos
    guardar_seguridad(uid, pin_real, 0, "ACTIVO")
    msg = bot.send_message(message.chat.id, "✅ PIN correcto.\nIntroduce tu nuevo PIN (4 a 6 dígitos):")
    bot.register_next_step_handler(msg, recibir_nuevo_pin)

def recibir_nuevo_pin(message):
    uid = str(message.from_user.id)

    if message.text and message.text.startswith("/"):
        start(message)
        return

    nuevo_pin = (message.text or "").strip()

    if not nuevo_pin.isdigit() or not (4 <= len(nuevo_pin) <= 6):
        msg = bot.send_message(message.chat.id, "❌ PIN inválido. Debe tener entre 4 y 6 dígitos. Inténtalo de nuevo:")
        bot.register_next_step_handler(msg, recibir_nuevo_pin)
        return

    msg = bot.send_message(message.chat.id, "🔁 Confirma tu nuevo PIN:")
    bot.register_next_step_handler(msg, confirmar_nuevo_pin, nuevo_pin)

def confirmar_nuevo_pin(message, nuevo_pin):
    uid = str(message.from_user.id)

    if message.text and message.text.startswith("/"):
        start(message)
        return

    confirmacion = (message.text or "").strip()

    if confirmacion != nuevo_pin:
        bot.send_message(message.chat.id, "❌ Los PIN no coinciden. Operación cancelada.", reply_markup=menu_usuario())
        return

    _, _, estado = leer_seguridad(uid)
    guardar_seguridad(uid, nuevo_pin, 0, estado)
    agregar_historial(uid, "PIN cambiado correctamente")
    bot.send_message(message.chat.id, "✅ Tu PIN fue actualizado correctamente.", reply_markup=menu_usuario())

# =========================================================
# FLUJO: TRANSFERENCIA
# =========================================================
def iniciar_transferencia(message):
    uid = str(message.from_user.id)
    if not cuenta_activa(uid):
        bot.send_message(message.chat.id, "❌ Cuenta suspendida.")
        return

    msg = bot.send_message(
        message.chat.id,
        "💸 Introduce el ID del destinatario:"
    )
    bot.register_next_step_handler(msg, recibir_destinatario_transferencia)

def recibir_destinatario_transferencia(message):
    remitente = str(message.from_user.id)

    if message.text and message.text.startswith("/"):
        start(message)
        return

    destinatario = (message.text or "").strip()

    if not destinatario.isdigit():
        msg = bot.send_message(message.chat.id, "❌ ID inválido. Introduce un ID numérico:")
        bot.register_next_step_handler(msg, recibir_destinatario_transferencia)
        return

    if destinatario == remitente:
        msg = bot.send_message(message.chat.id, "❌ No puedes transferirte a ti mismo. Introduce otro ID:")
        bot.register_next_step_handler(msg, recibir_destinatario_transferencia)
        return

    usuarios = leer_datos()
    if destinatario not in usuarios:
        msg = bot.send_message(message.chat.id, "❌ Ese usuario no existe. Introduce otro ID:")
        bot.register_next_step_handler(msg, recibir_destinatario_transferencia)
        return

    msg = bot.send_message(message.chat.id, "💵 Introduce el monto a transferir:")
    bot.register_next_step_handler(msg, recibir_monto_transferencia, destinatario)

def recibir_monto_transferencia(message, destinatario):
    remitente = str(message.from_user.id)

    if message.text and message.text.startswith("/"):
        start(message)
        return

    texto = (message.text or "").strip().replace(",", ".")

    try:
        monto = float(texto)
    except ValueError:
        msg = bot.send_message(message.chat.id, "❌ Monto inválido. Introduce un número válido:")
        bot.register_next_step_handler(msg, recibir_monto_transferencia, destinatario)
        return

    if monto <= 0:
        msg = bot.send_message(message.chat.id, "❌ El monto debe ser mayor que 0. Inténtalo de nuevo:")
        bot.register_next_step_handler(msg, recibir_monto_transferencia, destinatario)
        return

    usuarios = leer_datos()
    saldo = usuarios.get(remitente, 0.0)

    if monto > saldo:
        bot.send_message(message.chat.id, f"❌ Fondos insuficientes. Tu saldo es ${saldo:.2f} USD.", reply_markup=menu_usuario())
        return

    msg = bot.send_message(message.chat.id, "🔐 Introduce tu PIN para confirmar la transferencia:")
    bot.register_next_step_handler(msg, confirmar_transferencia_pin, destinatario, monto)

def confirmar_transferencia_pin(message, destinatario, monto):
    remitente = str(message.from_user.id)

    if message.text and message.text.startswith("/"):
        start(message)
        return

    pin_real, intentos, estado = leer_seguridad(remitente)

    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta suspendida.")
        return

    pin_ingresado = (message.text or "").strip()

    if pin_ingresado != pin_real:
        intentos += 1
        if intentos >= 3:
            guardar_seguridad(remitente, pin_real, intentos, "SUSPENDIDO")
            bot.send_message(
                message.chat.id,
                "🚫 PIN incorrecto 3 veces. Tu cuenta fue <b>SUSPENDIDA</b>."
            )
            return
        else:
            guardar_seguridad(remitente, pin_real, intentos, "ACTIVO")
            msg = bot.send_message(
                message.chat.id,
                f"❌ PIN incorrecto. Intento {intentos}/3.\nVuelve a introducir tu PIN:"
            )
            bot.register_next_step_handler(msg, confirmar_transferencia_pin, destinatario, monto)
            return

    # PIN correcto
    guardar_seguridad(remitente, pin_real, 0, "ACTIVO")

    usuarios = leer_datos()
    usuarios[remitente] = usuarios.get(remitente, 0.0) - monto
    usuarios[destinatario] = usuarios.get(destinatario, 0.0) + monto
    guardar_datos(usuarios)

    agregar_historial(remitente, f"Transferencia enviada a {destinatario}: -${monto:.2f} USD")
    agregar_historial(destinatario, f"Transferencia recibida de {remitente}: +${monto:.2f} USD")

    bot.send_message(
        message.chat.id,
        f"✅ Transferencia realizada.\nDestinatario: <code>{destinatario}</code>\nMonto: <b>${monto:.2f} USD</b>",
        reply_markup=menu_usuario()
    )

# =========================================================
# ADMIN: DESBLOQUEAR
# =========================================================
def pedir_id_desbloqueo(message):
    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    msg = bot.send_message(message.chat.id, "🔓 Envía el ID que quieres desbloquear:")
    bot.register_next_step_handler(msg, procesar_desbloqueo_id)

def procesar_desbloqueo_id(message):
    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    uid_objetivo = (message.text or "").strip()

    if not uid_objetivo.isdigit():
        msg = bot.send_message(message.chat.id, "❌ ID inválido. Introduce un ID numérico:")
        bot.register_next_step_handler(msg, procesar_desbloqueo_id)
        return

    pin, _, _ = leer_seguridad(uid_objetivo)
    if pin is None:
        bot.send_message(message.chat.id, "❌ Ese usuario no existe en seguridad.")
        return

    guardar_seguridad(uid_objetivo, "000010", 0, "ACTIVO")
    agregar_historial(uid_objetivo, "Cuenta desbloqueada por admin. PIN reseteado a 000010")

    bot.send_message(
        message.chat.id,
        f"✅ Usuario <code>{uid_objetivo}</code> desbloqueado.\nPIN reseteado a <code>000010</code>.",
        reply_markup=menu_admin()
    )

# =========================================================
# BOTONES DE MENÚ
# =========================================================
@bot.message_handler(func=lambda m: m.text == "💰 Mi Saldo")
def ver_saldo(message):
    uid = str(message.from_user.id)
    registrar_usuario_si_no_existe(message)

    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta bloqueada.")
        return

    saldo = leer_datos().get(uid, 0.0)
    bot.send_message(message.chat.id, f"💰 Tu saldo actual es: <b>${saldo:.2f} USD</b>")

@bot.message_handler(func=lambda m: m.text == "🧾 Mi Extracto")
def ver_extracto(message):
    uid = str(message.from_user.id)
    registrar_usuario_si_no_existe(message)

    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta bloqueada.")
        return

    movimientos = leer_historial(uid, limite=10)
    if not movimientos:
        bot.send_message(message.chat.id, "🧾 No hay movimientos registrados.")
        return

    texto = "🧾 <b>Últimos movimientos:</b>\n\n" + "\n".join(f"• {m}" for m in movimientos)
    bot.send_message(message.chat.id, texto)

@bot.message_handler(func=lambda m: m.text == "💸 Transferir")
def boton_transferir(message):
    registrar_usuario_si_no_existe(message)
    iniciar_transferencia(message)

@bot.message_handler(func=lambda m: m.text == "⚙️ Ajustes")
def ajustes(message):
    uid = str(message.from_user.id)
    registrar_usuario_si_no_existe(message)

    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta bloqueada.")
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("🔑 Cambiar PIN", callback_data="cambiar_pin"))

    if es_admin(message.from_user.id):
        mk.add(types.InlineKeyboardButton("🛠 PANEL ADMIN", callback_data="entrar_admin"))

    bot.send_message(message.chat.id, "⚙️ <b>Ajustes</b>:", reply_markup=mk)

# =========================================================
# BOTONES ADMIN
# =========================================================
@bot.message_handler(func=lambda m: m.text == "🏠 Menú Usuario")
def admin_ir_menu_usuario(message):
    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return
    pedir_menu_principal(message.chat.id)

@bot.message_handler(func=lambda m: m.text == "🔓 Desbloquear ID")
def admin_desbloquear(message):
    pedir_id_desbloqueo(message)

@bot.message_handler(func=lambda m: m.text == "💳 Balance CUP")
def admin_balance_cup(message):
    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    usuarios = leer_datos()
    total = sum(usuarios.values())
    bot.send_message(message.chat.id, f"💳 Balance global registrado: <b>${total:.2f} USD</b>")

# =========================================================
# FALLBACK DE TEXTO
# =========================================================
@bot.message_handler(content_types=['text'])
def fallback_texto(message):
    # Si el usuario manda un comando desconocido
    if message.text and message.text.startswith("/"):
        if message.text != "/start" and message.text != "/admin":
            bot.send_message(
                message.chat.id,
                "❓ Comando no reconocido.\nUsa /start para volver al menú principal."
            )
        return

    bot.send_message(
        message.chat.id,
        "Selecciona una opción del menú o usa /start.",
        reply_markup=menu_usuario()
    )

# =========================================================
# INICIO
# =========================================================
def iniciar():
    while True:
        try:
            print("Bot iniciado...")
            bot.infinity_polling(timeout=40, long_polling_timeout=30)
        except Exception as e:
            print(f"Error en polling: {e}")
            time.sleep(10)

if __name__ == "__main__":
    iniciar()
