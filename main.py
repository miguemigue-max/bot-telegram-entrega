import os
import time
import json
import telebot
from telebot import apihelper, types

# =========================================================
# CONFIG
# =========================================================
apihelper.proxy = {
    "http": "http://proxy.server:3128",
    "https": "http://proxy.server:3128",
}

TOKEN = os.getenv("8033243001:AAFZMqr1GiHAE0mAF25yRcrfLNPp3H-nnv0")
ADMIN_ID = 5220834019

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

DB_FILE = "banco.txt"
PERFILES_FILE = "perfiles.txt"
HISTORIAL_FILE = "historial.txt"
SEGURIDAD_FILE = "seguridad.txt"
PENDIENTES_FILE = "pendientes_depositos.json"

CUENTA_DEPOSITO = "MBanks / Cuenta destino 123456789 / Banco Demo"

# =========================================================
# ARCHIVOS
# =========================================================
def asegurar_archivo(path, default_text=""):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(default_text)

asegurar_archivo(DB_FILE)
asegurar_archivo(PERFILES_FILE)
asegurar_archivo(HISTORIAL_FILE)
asegurar_archivo(SEGURIDAD_FILE)
asegurar_archivo(PENDIENTES_FILE, "{}")

# =========================================================
# HELPERS GENERALES
# =========================================================
def es_admin(user_id):
    return int(user_id) == ADMIN_ID

def cancelar_flujo(chat_id):
    try:
        bot.clear_step_handler_by_chat_id(chat_id)
    except Exception:
        pass

def texto_msg(message):
    return (message.text or "").strip()

def es_cancelacion(message):
    return texto_msg(message) in ["❌ Cancelar", "/cancel", "Cancelar"]

def parse_monto(txt):
    try:
        return float(txt.replace(",", ".").strip())
    except Exception:
        return None

def timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")

# =========================================================
# PERSISTENCIA
# =========================================================
def leer_datos():
    usuarios = {}
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if ":" in linea:
                    try:
                        uid, saldo = linea.split(":")
                        usuarios[uid] = float(saldo)
                    except Exception:
                        continue
    return usuarios

def guardar_datos(usuarios):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        for uid, saldo in usuarios.items():
            f.write(f"{uid}:{saldo}\n")

def leer_perfiles():
    perfiles = {}
    if os.path.exists(PERFILES_FILE):
        with open(PERFILES_FILE, "r", encoding="utf-8") as f:
            for linea in f:
                partes = linea.strip().split("|", 2)
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

def leer_seguridad(uid):
    uid = str(uid)
    if os.path.exists(SEGURIDAD_FILE):
        with open(SEGURIDAD_FILE, "r", encoding="utf-8") as f:
            for linea in f:
                partes = linea.strip().split("|")
                if len(partes) == 4 and partes[0] == uid:
                    return partes[1], int(partes[2]), partes[3]
    return None, None, None

def guardar_seguridad(uid, pin, intentos, estado):
    uid = str(uid)
    datos = {}

    if os.path.exists(SEGURIDAD_FILE):
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
    with open(HISTORIAL_FILE, "a", encoding="utf-8") as f:
        f.write(f"{uid}|{timestamp()}|{texto}\n")

def leer_historial(uid, limite=10):
    movimientos = []
    if os.path.exists(HISTORIAL_FILE):
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
            for linea in f:
                partes = linea.strip().split("|", 2)
                if len(partes) == 3 and partes[0] == str(uid):
                    movimientos.append(f"{partes[1]} - {partes[2]}")
    return movimientos[-limite:]

def leer_pendientes():
    try:
        with open(PENDIENTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def guardar_pendientes(data):
    with open(PENDIENTES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =========================================================
# USUARIOS
# =========================================================
def registrar_usuario_si_no_existe(message):
    uid = str(message.from_user.id)

    usuarios = leer_datos()
    if uid not in usuarios:
        usuarios[uid] = 0.0
        guardar_datos(usuarios)

    perfiles = leer_perfiles()
    if uid not in perfiles:
        perfiles[uid] = {
            "username": message.from_user.username or "",
            "nombre": message.from_user.first_name or "Usuario"
        }
        guardar_perfiles(perfiles)

    pin, intentos, estado = leer_seguridad(uid)
    if pin is None:
        guardar_seguridad(uid, "0000", 0, "ACTIVO")

def cuenta_activa(uid):
    pin, intentos, estado = leer_seguridad(uid)
    return estado == "ACTIVO"

# =========================================================
# MENÚS
# =========================================================
def menu_usuario():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    mk.add(
        types.KeyboardButton("💰 Mi Saldo"),
        types.KeyboardButton("🧾 Mi Extracto"),
        types.KeyboardButton("💸 Transferir"),
        types.KeyboardButton("🆔 Mi ID"),
        types.KeyboardButton("➕ Depositar"),
        types.KeyboardButton("⚙️ Ajustes"),
    )
    return mk

def menu_admin():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    mk.add(
        types.KeyboardButton("🔓 Desbloquear ID"),
        types.KeyboardButton("📥 Depósitos Pendientes"),
        types.KeyboardButton("🏠 Menú Usuario")
    )
    return mk

def menu_cancelar():
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    mk.add(types.KeyboardButton("❌ Cancelar"))
    return mk

# =========================================================
# RESPUESTAS COMUNES
# =========================================================
def enviar_menu_principal(chat_id, nombre="Usuario"):
    bot.send_message(
        chat_id,
        f"👋 Hola, <b>{nombre}</b>.\nBienvenido a <b>MBanks</b>.",
        reply_markup=menu_usuario()
    )

def responder_suspendido(chat_id):
    bot.send_message(chat_id, "❌ Tu cuenta está <b>SUSPENDIDA</b>.")

# =========================================================
# /START Y /ADMIN
# =========================================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)

    if estado == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    nombre = message.from_user.first_name or "Usuario"
    enviar_menu_principal(message.chat.id, nombre)

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    cancelar_flujo(message.chat.id)

    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    bot.send_message(message.chat.id, "🛠 <b>Panel Admin</b>", reply_markup=menu_admin())

# =========================================================
# AJUSTES
# =========================================================
@bot.message_handler(func=lambda m: texto_msg(m) == "⚙️ Ajustes")
def ajustes(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("🔑 Cambiar PIN", callback_data="cambiar_pin"))
    bot.send_message(message.chat.id, "⚙️ <b>Ajustes</b>", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data == "cambiar_pin")
def cb_cambiar_pin(call):
    uid = str(call.from_user.id)
    _, _, estado = leer_seguridad(uid)

    if estado == "SUSPENDIDO":
        bot.answer_callback_query(call.id, "Cuenta suspendida")
        bot.send_message(call.message.chat.id, "❌ Cuenta suspendida.")
        return

    cancelar_flujo(call.message.chat.id)
    msg = bot.send_message(
        call.message.chat.id,
        "🔐 Escribe tu PIN actual o pulsa ❌ Cancelar:",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_pin_actual)
    bot.answer_callback_query(call.id)

def paso_pin_actual(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Cambio de PIN cancelado.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    uid = str(message.from_user.id)
    pin_real, intentos, estado = leer_seguridad(uid)

    if estado == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    if texto_msg(message) != pin_real:
        intentos += 1
        if intentos >= 3:
            guardar_seguridad(uid, pin_real, intentos, "SUSPENDIDO")
            bot.send_message(message.chat.id, "🚫 Has fallado 3 veces. Tu cuenta fue suspendida.", reply_markup=menu_usuario())
            return
        guardar_seguridad(uid, pin_real, intentos, "ACTIVO")
        msg = bot.send_message(
            message.chat.id,
            f"❌ PIN incorrecto. Intento {intentos}/3.\nEscribe tu PIN actual o pulsa ❌ Cancelar:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_pin_actual)
        return

    guardar_seguridad(uid, pin_real, 0, "ACTIVO")
    msg = bot.send_message(
        message.chat.id,
        "✅ PIN correcto.\nEscribe tu nuevo PIN de 4 a 6 dígitos:",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_nuevo_pin)

def paso_nuevo_pin(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Cambio de PIN cancelado.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    nuevo_pin = texto_msg(message)

    if not nuevo_pin.isdigit() or not (4 <= len(nuevo_pin) <= 6):
        msg = bot.send_message(
            message.chat.id,
            "❌ PIN inválido. Debe tener entre 4 y 6 dígitos:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_nuevo_pin)
        return

    msg = bot.send_message(
        message.chat.id,
        "🔁 Confirma el nuevo PIN:",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_confirmar_pin, nuevo_pin)

def paso_confirmar_pin(message, nuevo_pin):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Cambio de PIN cancelado.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    if texto_msg(message) != nuevo_pin:
        bot.send_message(message.chat.id, "❌ Los PIN no coinciden. Operación cancelada.", reply_markup=menu_usuario())
        return

    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)
    guardar_seguridad(uid, nuevo_pin, 0, estado)
    agregar_historial(uid, "PIN cambiado correctamente")
    bot.send_message(message.chat.id, "✅ PIN actualizado correctamente.", reply_markup=menu_usuario())

# =========================================================
# BOTONES USUARIO
# =========================================================
@bot.message_handler(func=lambda m: texto_msg(m) == "💰 Mi Saldo")
def mi_saldo(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    saldo = leer_datos().get(uid, 0.0)
    bot.send_message(message.chat.id, f"💰 Tu saldo actual es: <b>${saldo:.2f} USD</b>", reply_markup=menu_usuario())

@bot.message_handler(func=lambda m: texto_msg(m) == "🧾 Mi Extracto")
def mi_extracto(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    movimientos = leer_historial(uid, limite=10)
    if not movimientos:
        bot.send_message(message.chat.id, "🧾 No hay movimientos registrados.", reply_markup=menu_usuario())
        return

    texto = "🧾 <b>Últimos movimientos:</b>\n\n" + "\n".join(f"• {m}" for m in movimientos)
    bot.send_message(message.chat.id, texto, reply_markup=menu_usuario())

@bot.message_handler(func=lambda m: texto_msg(m) == "🆔 Mi ID")
def mi_id(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    uid = str(message.from_user.id)
    bot.send_message(message.chat.id, f"🆔 Tu ID es: <code>{uid}</code>", reply_markup=menu_usuario())

# =========================================================
# TRANSFERENCIAS
# =========================================================
@bot.message_handler(func=lambda m: texto_msg(m) == "💸 Transferir")
def iniciar_transferencia(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    msg = bot.send_message(
        message.chat.id,
        "💸 Introduce el ID del destinatario o pulsa ❌ Cancelar:",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_destinatario_transferencia)

def paso_destinatario_transferencia(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Transferencia cancelada.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    remitente = str(message.from_user.id)
    destinatario = texto_msg(message)

    if not destinatario.isdigit():
        msg = bot.send_message(
            message.chat.id,
            "❌ ID inválido. Introduce un ID numérico o pulsa ❌ Cancelar:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_destinatario_transferencia)
        return

    if destinatario == remitente:
        msg = bot.send_message(
            message.chat.id,
            "❌ No puedes transferirte a ti mismo. Introduce otro ID:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_destinatario_transferencia)
        return

    usuarios = leer_datos()
    if destinatario not in usuarios:
        msg = bot.send_message(
            message.chat.id,
            "❌ Ese usuario no existe. Introduce otro ID o pulsa ❌ Cancelar:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_destinatario_transferencia)
        return

    msg = bot.send_message(
        message.chat.id,
        "💵 Introduce el monto a transferir:",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_monto_transferencia, destinatario)

def paso_monto_transferencia(message, destinatario):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Transferencia cancelada.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    monto = parse_monto(texto_msg(message))
    if monto is None or monto <= 0:
        msg = bot.send_message(
            message.chat.id,
            "❌ Monto inválido. Escribe un número válido:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_monto_transferencia, destinatario)
        return

    remitente = str(message.from_user.id)
    usuarios = leer_datos()
    saldo = usuarios.get(remitente, 0.0)

    if monto > saldo:
        bot.send_message(message.chat.id, f"❌ Fondos insuficientes. Tu saldo es ${saldo:.2f} USD.", reply_markup=menu_usuario())
        return

    msg = bot.send_message(
        message.chat.id,
        "🔐 Introduce tu PIN para confirmar la transferencia:",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_pin_transferencia, destinatario, monto)

def paso_pin_transferencia(message, destinatario, monto):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Transferencia cancelada.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    remitente = str(message.from_user.id)
    pin_real, intentos, estado = leer_seguridad(remitente)

    if estado == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    if texto_msg(message) != pin_real:
        intentos += 1
        if intentos >= 3:
            guardar_seguridad(remitente, pin_real, intentos, "SUSPENDIDO")
            bot.send_message(message.chat.id, "🚫 PIN incorrecto 3 veces. Cuenta suspendida.", reply_markup=menu_usuario())
            return

        guardar_seguridad(remitente, pin_real, intentos, "ACTIVO")
        msg = bot.send_message(
            message.chat.id,
            f"❌ PIN incorrecto. Intento {intentos}/3.\nIntroduce tu PIN:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_pin_transferencia, destinatario, monto)
        return

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

    try:
        bot.send_message(
            int(destinatario),
            f"💸 Has recibido una transferencia.\nDe: <code>{remitente}</code>\nMonto: <b>${monto:.2f} USD</b>",
            reply_markup=menu_usuario()
        )
    except Exception:
        pass

# =========================================================
# DEPOSITAR
# =========================================================
@bot.message_handler(func=lambda m: texto_msg(m) == "➕ Depositar")
def iniciar_deposito(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    msg = bot.send_message(
        message.chat.id,
        "➕ ¿Cuánto quieres depositar? Escribe solo números.\nEjemplo: 25 o 25.50",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_monto_deposito)

def paso_monto_deposito(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Depósito cancelado.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    monto = parse_monto(texto_msg(message))
    if monto is None or monto <= 0:
        msg = bot.send_message(
            message.chat.id,
            "❌ Monto inválido. Intenta de nuevo:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_monto_deposito)
        return

    msg = bot.send_message(
        message.chat.id,
        "📷 Ahora manda una foto del comprobante.\n\n"
        f"Realiza la transferencia a:\n<b>{CUENTA_DEPOSITO}</b>\n\n"
        f"Monto declarado: <b>${monto:.2f} USD</b>\n\n"
        "Cuando envíes la foto, será enviada al administrador para aprobación.",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_comprobante_deposito, monto)

def paso_comprobante_deposito(message, monto):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Depósito cancelado.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    if not message.photo:
        msg = bot.send_message(
            message.chat.id,
            "❌ Debes enviar una foto del comprobante.",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_comprobante_deposito, monto)
        return

    uid = str(message.from_user.id)
    perfiles = leer_perfiles()
    nombre = perfiles.get(uid, {}).get("nombre", "Usuario")
    username = perfiles.get(uid, {}).get("username", "")

    deposit_id = f"dep_{int(time.time())}_{uid}"
    photo_file_id = message.photo[-1].file_id

    pendientes = leer_pendientes()
    pendientes[deposit_id] = {
        "deposit_id": deposit_id,
        "user_id": uid,
        "chat_id": message.chat.id,
        "monto": monto,
        "photo_file_id": photo_file_id,
        "estado": "PENDIENTE",
        "fecha": timestamp(),
    }
    guardar_pendientes(pendientes)

    mk = types.InlineKeyboardMarkup()
    mk.add(
        types.InlineKeyboardButton("✅ Aprobar", callback_data=f"aprobar_dep|{deposit_id}"),
        types.InlineKeyboardButton("❌ Rechazar", callback_data=f"rechazar_dep|{deposit_id}")
    )

    caption = (
        "📥 <b>Nuevo depósito pendiente</b>\n\n"
        f"ID depósito: <code>{deposit_id}</

# =========================================================
# EXTRACTO
# =========================================================
@bot.message_handler(func=lambda m: m.text == "🧾 Mi Extracto")
def ver_extracto(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    uid = str(message.from_user.id)

    movimientos = leer_historial(uid, 10)

    if not movimientos:
        bot.send_message(message.chat.id, "🧾 No hay movimientos registrados.")
        return

    texto = "🧾 <b>Últimos movimientos:</b>\n\n"
    for m in movimientos:
        texto += f"• {m}\n"

    bot.send_message(message.chat.id, texto)


# =========================================================
# MI ID
# =========================================================
@bot.message_handler(func=lambda m: m.text == "🆔 Mi ID")
def ver_id(message):
    cancelar_flujo(message.chat.id)

    bot.send_message(
        message.chat.id,
        f"🆔 Tu ID es:\n\n<code>{message.from_user.id}</code>"
    )


# =========================================================
# TRANSFERENCIAS
# =========================================================
@bot.message_handler(func=lambda m: m.text == "💸 Transferir")
def iniciar_transferencia(message):
    cancelar_flujo(message.chat.id)

    uid = str(message.from_user.id)

    if not cuenta_activa(uid):
        bot.send_message(message.chat.id, "❌ Cuenta suspendida.")
        return

    msg = bot.send_message(
        message.chat.id,
        "💸 Introduce el ID del destinatario:",
        reply_markup=menu_cancelar()
    )

    bot.register_next_step_handler(msg, recibir_destinatario)


def recibir_destinatario(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "❌ Transferencia cancelada.", reply_markup=menu_usuario())
        return

    remitente = str(message.from_user.id)
    destinatario = (message.text or "").strip()

    if not destinatario.isdigit():
        msg = bot.send_message(message.chat.id, "❌ ID inválido. Introduce un ID numérico:")
        bot.register_next_step_handler(msg, recibir_destinatario)
        return

    usuarios = leer_datos()

    if destinatario not in usuarios:
        msg = bot.send_message(message.chat.id, "❌ Ese usuario no existe. Introduce otro ID:")
        bot.register_next_step_handler(msg, recibir_destinatario)
        return

    msg = bot.send_message(message.chat.id, "💵 Introduce el monto:")
    bot.register_next_step_handler(msg, recibir_monto_transferencia, destinatario)


def recibir_monto_transferencia(message, destinatario):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "❌ Transferencia cancelada.", reply_markup=menu_usuario())
        return

    remitente = str(message.from_user.id)

    try:
        monto = float(message.text)
    except:
        msg = bot.send_message(message.chat.id, "❌ Monto inválido.")
        bot.register_next_step_handler(msg, recibir_monto_transferencia, destinatario)
        return

    usuarios = leer_datos()

    if monto > usuarios.get(remitente, 0):
        bot.send_message(message.chat.id, "❌ Fondos insuficientes.", reply_markup=menu_usuario())
        return

    usuarios[remitente] -= monto
    usuarios[destinatario] += monto

    guardar_datos(usuarios)

    agregar_historial(remitente, f"Transferencia enviada a {destinatario}: -${monto}")
    agregar_historial(destinatario, f"Transferencia recibida de {remitente}: +${monto}")

    bot.send_message(
        message.chat.id,
        f"✅ Transferencia enviada.\n💵 Monto: ${monto}",
        reply_markup=menu_usuario()
    )


# =========================================================
# AJUSTES
# =========================================================
@bot.message_handler(func=lambda m: m.text == "⚙️ Ajustes")
def ajustes(message):
    cancelar_flujo(message.chat.id)

    bot.send_message(
        message.chat.id,
        "⚙️ Ajustes:",
        reply_markup=menu_ajustes()
    )


# =========================================================
# CAMBIO DE PIN
# =========================================================
def validar_pin_actual_para_cambio(message):

    uid = str(message.from_user.id)

    pin_real, intentos, estado = leer_seguridad(uid)

    if message.text != pin_real:
        intentos += 1

        if intentos >= 3:
            guardar_seguridad(uid, pin_real, intentos, "SUSPENDIDO")
            bot.send_message(message.chat.id, "🚫 Cuenta suspendida.")
            return

        guardar_seguridad(uid, pin_real, intentos, "ACTIVO")

        msg = bot.send_message(message.chat.id, "❌ PIN incorrecto.")
        bot.register_next_step_handler(msg, validar_pin_actual_para_cambio)
        return

    guardar_seguridad(uid, pin_real, 0, "ACTIVO")

    msg = bot.send_message(message.chat.id, "🔑 Introduce tu nuevo PIN:")
    bot.register_next_step_handler(msg, nuevo_pin)


def nuevo_pin(message):

    uid = str(message.from_user.id)

    pin = message.text.strip()

    if not pin.isdigit():
        msg = bot.send_message(message.chat.id, "❌ El PIN debe ser numérico.")
        bot.register_next_step_handler(msg, nuevo_pin)
        return

    guardar_seguridad(uid, pin, 0, "ACTIVO")

    bot.send_message(message.chat.id, "✅ PIN actualizado.", reply_markup=menu_usuario())


# =========================================================
# ADMIN DESBLOQUEAR
# =========================================================
@bot.message_handler(func=lambda m: m.text == "🔓 Desbloquear ID")
def desbloquear(message):

    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    msg = bot.send_message(message.chat.id, "Envía el ID:")
    bot.register_next_step_handler(msg, procesar_desbloqueo)


def procesar_desbloqueo(message):

    uid = message.text.strip()

    guardar_seguridad(uid, "000010", 0, "ACTIVO")

    bot.send_message(
        message.chat.id,
        f"✅ Usuario {uid} desbloqueado.\nPIN reseteado a 000010"
    )


# =========================================================
# VOLVER AL MENÚ
# =========================================================
@bot.message_handler(func=lambda m: m.text == "🏠 Menú Usuario")
def volver_menu(message):

    bot.send_message(
        message.chat.id,
        "🏦 Menú principal:",
        reply_markup=menu_usuario()
    )


# =========================================================
# INICIAR BOT
# =========================================================
def iniciar():
    while True:
        try:
            print("Bot iniciado...")
            bot.infinity_polling(timeout=40, long_polling_timeout=30)
        except Exception as e:
            print("Error:", e)
            time.sleep(10)


if __name__ == "__main__":
    iniciar()
