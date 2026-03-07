import os
import time
import json
import telebot
from telebot import apihelper, types

# =========================================================
# CONFIGURACION
# =========================================================
apihelper.proxy = {
    "http": "http://proxy.server:3128",
    "https": "http://proxy.server:3128",
}

# Recomendado: variable de entorno MBANKS_BOT_TOKEN
TOKEN = "8033243001:AAFZMqr1GiHAE0mAF25yRcrfLNPp3H-nnv0"
ADMIN_ID = 5220834019

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

DB_FILE = "banco.txt"
PERFILES_FILE = "perfiles.txt"
HISTORIAL_FILE = "historial.txt"
SEGURIDAD_FILE = "seguridad.txt"
PENDIENTES_FILE = "pendientes_depositos.json"
TASA_FILE = "tasa.txt"

CUENTA_DEPOSITO = "Cuenta destino: 123456789 | Banco: MBanks | Titular: MBanks Admin"
PIN_RESET_ADMIN = "000010"

# =========================================================
# ARCHIVOS BASE
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
asegurar_archivo(TASA_FILE, "250")

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
        return float(str(txt).replace(",", ".").strip())
    except Exception:
        return None

def timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def formato_usd(valor):
    return f"${float(valor):.2f} USD"

def formato_cup(valor):
    return f"{float(valor):.2f} CUP"

# =========================================================
# TASA DE CAMBIO
# =========================================================
def leer_tasa():
    try:
        with open(TASA_FILE, "r", encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        guardar_tasa(250)
        return 250.0

def guardar_tasa(valor):
    with open(TASA_FILE, "w", encoding="utf-8") as f:
        f.write(str(float(valor)))

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
                    return {
                        "pin": partes[1],
                        "intentos": int(partes[2]),
                        "estado": partes[3]
                    }
    return None

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

    sec = leer_seguridad(uid)
    if sec is None:
        guardar_seguridad(uid, "0000", 0, "ACTIVO")

def cuenta_activa(uid):
    sec = leer_seguridad(uid)
    return sec is not None and sec["estado"] == "ACTIVO"

# =========================================================
# MENUS
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
        types.KeyboardButton("💱 Ver Tasa"),
        types.KeyboardButton("✏️ Cambiar Tasa"),
        types.KeyboardButton("📥 Depósitos Pendientes"),
        types.KeyboardButton("🔓 Desbloquear ID"),
        types.KeyboardButton("💰 Balance Sistema"),
        types.KeyboardButton("👤 Buscar Usuario"),
        types.KeyboardButton("💵 Acreditar Saldo"),
        types.KeyboardButton("📊 Estadísticas"),
    )
    mk.add(types.KeyboardButton("🏠 Menú Usuario"))
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

    sec = leer_seguridad(message.from_user.id)
    if sec and sec["estado"] == "SUSPENDIDO":
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

@bot.message_handler(commands=["vertasa"])
def cmd_ver_tasa(message):
    tasa = leer_tasa()
    bot.send_message(message.chat.id, f"💱 Tasa actual\n\n1 USD = {tasa:.2f} CUP")

# =========================================================
# AJUSTES / CAMBIO DE PIN
# =========================================================
@bot.message_handler(func=lambda m: texto_msg(m) == "⚙️ Ajustes")
def ajustes(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    sec = leer_seguridad(message.from_user.id)
    if sec and sec["estado"] == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    mk = types.InlineKeyboardMarkup()
    mk.add(types.InlineKeyboardButton("🔑 Cambiar PIN", callback_data="cambiar_pin"))
    bot.send_message(message.chat.id, "⚙️ <b>Ajustes</b>", reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data == "cambiar_pin")
def cb_cambiar_pin(call):
    sec = leer_seguridad(call.from_user.id)

    if sec and sec["estado"] == "SUSPENDIDO":
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

    sec = leer_seguridad(message.from_user.id)

    if sec and sec["estado"] == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    if texto_msg(message) != sec["pin"]:
        intentos = sec["intentos"] + 1
        if intentos >= 3:
            guardar_seguridad(message.from_user.id, sec["pin"], intentos, "SUSPENDIDO")
            bot.send_message(message.chat.id, "🚫 Has fallado 3 veces. Tu cuenta fue suspendida.", reply_markup=menu_usuario())
            return

        guardar_seguridad(message.from_user.id, sec["pin"], intentos, "ACTIVO")
        msg = bot.send_message(
            message.chat.id,
            f"❌ PIN incorrecto. Intento {intentos}/3.\nEscribe tu PIN actual o pulsa ❌ Cancelar:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_pin_actual)
        return

    guardar_seguridad(message.from_user.id, sec["pin"], 0, "ACTIVO")
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

    sec = leer_seguridad(message.from_user.id)
    guardar_seguridad(message.from_user.id, nuevo_pin, 0, sec["estado"])
    agregar_historial(message.from_user.id, "PIN cambiado correctamente")
    bot.send_message(message.chat.id, "✅ PIN actualizado correctamente.", reply_markup=menu_usuario())

# =========================================================
# BOTONES USUARIO
# =========================================================
@bot.message_handler(func=lambda m: texto_msg(m) == "💰 Mi Saldo")
def mi_saldo(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    sec = leer_seguridad(message.from_user.id)
    if sec and sec["estado"] == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    uid = str(message.from_user.id)
    saldo = leer_datos().get(uid, 0.0)
    tasa = leer_tasa()
    bot.send_message(
        message.chat.id,
        f"💰 Tu saldo actual es: <b>{formato_usd(saldo)}</b>\n💱 Tasa actual: <b>1 USD = {tasa:.2f} CUP</b>",
        reply_markup=menu_usuario()
    )

@bot.message_handler(func=lambda m: texto_msg(m) == "🧾 Mi Extracto")
def mi_extracto(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    sec = leer_seguridad(message.from_user.id)
    if sec and sec["estado"] == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    uid = str(message.from_user.id)
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

    sec = leer_seguridad(message.from_user.id)
    if sec and sec["estado"] == "SUSPENDIDO":
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
        "💵 Introduce el monto a transferir en USD:",
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
        bot.send_message(message.chat.id, f"❌ Fondos insuficientes. Tu saldo es {formato_usd(saldo)}.", reply_markup=menu_usuario())
        return

    msg = bot.send_message(
        message.chat.id,
        f"🔐 Introduce tu PIN para confirmar la transferencia de <b>{formato_usd(monto)}</b>:",
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
    sec = leer_seguridad(remitente)

    if sec and sec["estado"] == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    if texto_msg(message) != sec["pin"]:
        intentos = sec["intentos"] + 1
        if intentos >= 3:
            guardar_seguridad(remitente, sec["pin"], intentos, "SUSPENDIDO")
            bot.send_message(message.chat.id, "🚫 PIN incorrecto 3 veces. Cuenta suspendida.", reply_markup=menu_usuario())
            return

        guardar_seguridad(remitente, sec["pin"], intentos, "ACTIVO")
        msg = bot.send_message(
            message.chat.id,
            f"❌ PIN incorrecto. Intento {intentos}/3.\nIntroduce tu PIN:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_pin_transferencia, destinatario, monto)
        return

    guardar_seguridad(remitente, sec["pin"], 0, "ACTIVO")

    usuarios = leer_datos()
    usuarios[remitente] = usuarios.get(remitente, 0.0) - monto
    usuarios[destinatario] = usuarios.get(destinatario, 0.0) + monto
    guardar_datos(usuarios)

    agregar_historial(remitente, f"Transferencia enviada a {destinatario}: -{formato_usd(monto)}")
    agregar_historial(destinatario, f"Transferencia recibida de {remitente}: +{formato_usd(monto)}")

    bot.send_message(
        message.chat.id,
        f"✅ Transferencia realizada.\nDestinatario: <code>{destinatario}</code>\nMonto: <b>{formato_usd(monto)}</b>",
        reply_markup=menu_usuario()
    )

    try:
        bot.send_message(
            int(destinatario),
            f"💸 Has recibido una transferencia.\nDe: <code>{remitente}</code>\nMonto: <b>{formato_usd(monto)}</b>",
            reply_markup=menu_usuario()
        )
    except Exception:
        pass

# =========================================================
# DEPOSITOS CON CONVERSION CUP -> USD
# =========================================================
@bot.message_handler(func=lambda m: texto_msg(m) == "➕ Depositar")
def iniciar_deposito(message):
    cancelar_flujo(message.chat.id)
    registrar_usuario_si_no_existe(message)

    sec = leer_seguridad(message.from_user.id)
    if sec and sec["estado"] == "SUSPENDIDO":
        responder_suspendido(message.chat.id)
        return

    tasa = leer_tasa()
    msg = bot.send_message(
        message.chat.id,
        f"➕ ¿Cuántos <b>CUP</b> quieres depositar?\n\n💱 Tasa actual: <b>1 USD = {tasa:.2f} CUP</b>\n\nEjemplo: 1000",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_monto_deposito_cup)

def paso_monto_deposito_cup(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Depósito cancelado.", reply_markup=menu_usuario())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_start(message)
        return

    monto_cup = parse_monto(texto_msg(message))
    if monto_cup is None or monto_cup <= 0:
        msg = bot.send_message(
            message.chat.id,
            "❌ Monto inválido. Intenta de nuevo:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, paso_monto_deposito_cup)
        return

    tasa = leer_tasa()
    monto_usd = monto_cup / tasa

    msg = bot.send_message(
        message.chat.id,
        "💱 <b>Conversión automática</b>\n\n"
        f"Monto enviado: <b>{formato_cup(monto_cup)}</b>\n"
        f"Tasa actual: <b>1 USD = {tasa:.2f} CUP</b>\n"
        f"Se te acreditará: <b>{formato_usd(monto_usd)}</b>\n\n"
        "Deposita en esta cuenta:\n"
        f"<code>{CUENTA_DEPOSITO}</code>\n\n"
        "Ahora envía una <b>foto del comprobante</b>.",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, paso_comprobante_deposito, monto_cup, monto_usd)

def paso_comprobante_deposito(message, monto_cup, monto_usd):
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
        bot.register_next_step_handler(msg, paso_comprobante_deposito, monto_cup, monto_usd)
        return

    uid = str(message.from_user.id)
    perfiles = leer_perfiles()
    nombre = perfiles.get(uid, {}).get("nombre", "Usuario")
    username = perfiles.get(uid, {}).get("username", "")

    deposit_id = f"dep_{int(time.time())}_{uid}"
    photo_file_id = message.photo[-1].file_id
    tasa = leer_tasa()

    pendientes = leer_pendientes()
    pendientes[deposit_id] = {
        "deposit_id": deposit_id,
        "user_id": uid,
        "chat_id": message.chat.id,
        "monto_cup": monto_cup,
        "monto_usd": monto_usd,
        "tasa": tasa,
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
        f"ID depósito: <code>{deposit_id}</code>\n"
        f"Usuario: <code>{uid}</code>\n"
        f"Nombre: <b>{nombre}</b>\n"
        f"Username: @{username if username else 'sin_username'}\n"
        f"Monto CUP: <b>{formato_cup(monto_cup)}</b>\n"
        f"Tasa: <b>1 USD = {tasa:.2f} CUP</b>\n"
        f"A acreditar: <b>{formato_usd(monto_usd)}</b>\n"
        f"Fecha: {timestamp()}"
    )

    bot.send_photo(ADMIN_ID, photo_file_id, caption=caption, reply_markup=mk)
    bot.send_message(
        message.chat.id,
        "📨 Comprobante enviado al administrador.\nTu depósito quedó pendiente de aprobación.",
        reply_markup=menu_usuario()
    )
    agregar_historial(uid, f"Depósito solicitado: {formato_cup(monto_cup)} => {formato_usd(monto_usd)} - pendiente")

# =========================================================
# CALLBACKS DEPOSITOS
# =========================================================
@bot.callback_query_handler(func=lambda c: c.data.startswith("aprobar_dep|") or c.data.startswith("rechazar_dep|"))
def cb_depositos(call):
    if not es_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "No autorizado")
        return

    accion, deposit_id = call.data.split("|", 1)
    pendientes = leer_pendientes()

    if deposit_id not in pendientes:
        bot.answer_callback_query(call.id, "Depósito no encontrado")
        return

    dep = pendientes[deposit_id]

    if dep["estado"] != "PENDIENTE":
        bot.answer_callback_query(call.id, f"Ya fue procesado: {dep['estado']}")
        return

    uid = dep["user_id"]
    monto_usd = float(dep["monto_usd"])
    monto_cup = float(dep["monto_cup"])
    chat_id_usuario = dep["chat_id"]

    if accion == "aprobar_dep":
        usuarios = leer_datos()
        usuarios[uid] = usuarios.get(uid, 0.0) + monto_usd
        guardar_datos(usuarios)

        dep["estado"] = "APROBADO"
        guardar_pendientes(pendientes)

        agregar_historial(uid, f"Depósito aprobado: {formato_cup(monto_cup)} => +{formato_usd(monto_usd)}")

        try:
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                caption=(call.message.caption or "") + "\n\n✅ <b>APROBADO</b>"
            )
        except Exception:
            pass

        bot.send_message(
            chat_id_usuario,
            f"✅ Tu depósito fue aprobado.\nMonto acreditado: <b>{formato_usd(monto_usd)}</b>",
            reply_markup=menu_usuario()
        )
        bot.answer_callback_query(call.id, "Depósito aprobado")
        return

    if accion == "rechazar_dep":
        dep["estado"] = "RECHAZADO"
        guardar_pendientes(pendientes)

        agregar_historial(uid, f"Depósito rechazado: {formato_cup(monto_cup)} => {formato_usd(monto_usd)}")

        try:
            bot.edit_message_caption(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                caption=(call.message.caption or "") + "\n\n❌ <b>RECHAZADO</b>"
            )
        except Exception:
            pass

        bot.send_message(
            chat_id_usuario,
            f"❌ Tu depósito fue rechazado.\nMonto reportado: <b>{formato_cup(monto_cup)}</b>\nEquivalente: <b>{formato_usd(monto_usd)}</b>",
            reply_markup=menu_usuario()
        )
        bot.answer_callback_query(call.id, "Depósito rechazado")
        return

# =========================================================
# PANEL ADMIN
# =========================================================
@bot.message_handler(func=lambda m: texto_msg(m) == "🏠 Menú Usuario")
def admin_ir_menu_usuario(message):
    cancelar_flujo(message.chat.id)
    enviar_menu_principal(message.chat.id, message.from_user.first_name or "Usuario")

@bot.message_handler(func=lambda m: texto_msg(m) == "💱 Ver Tasa")
def admin_ver_tasa(message):
    if not es_admin(message.from_user.id):
        return
    tasa = leer_tasa()
    bot.send_message(message.chat.id, f"💱 Tasa actual\n\n1 USD = {tasa:.2f} CUP", reply_markup=menu_admin())

@bot.message_handler(func=lambda m: texto_msg(m) == "✏️ Cambiar Tasa")
def admin_cambiar_tasa(message):
    cancelar_flujo(message.chat.id)

    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    msg = bot.send_message(
        message.chat.id,
        "✏️ Escribe la nueva tasa.\nEjemplo: 250\n\nEsto significa: 1 USD = 250 CUP",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, admin_guardar_tasa)

def admin_guardar_tasa(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Operación cancelada.", reply_markup=menu_admin())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_admin(message)
        return

    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    nueva_tasa = parse_monto(texto_msg(message))
    if nueva_tasa is None or nueva_tasa <= 0:
        msg = bot.send_message(message.chat.id, "❌ Tasa inválida. Escribe un número mayor que 0:", reply_markup=menu_cancelar())
        bot.register_next_step_handler(msg, admin_guardar_tasa)
        return

    guardar_tasa(nueva_tasa)
    bot.send_message(message.chat.id, f"✅ Nueva tasa guardada.\n\n1 USD = {nueva_tasa:.2f} CUP", reply_markup=menu_admin())

@bot.message_handler(func=lambda m: texto_msg(m) == "🔓 Desbloquear ID")
def admin_pedir_id_desbloqueo(message):
    cancelar_flujo(message.chat.id)

    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    msg = bot.send_message(
        message.chat.id,
        "🔓 Escribe el ID del usuario que quieres desbloquear:",
        reply_markup=menu_cancelar()
    )
    bot.register_next_step_handler(msg, admin_desbloquear_id)

def admin_desbloquear_id(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Operación cancelada.", reply_markup=menu_admin())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_admin(message)
        return

    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    uid_objetivo = texto_msg(message)
    if not uid_objetivo.isdigit():
        msg = bot.send_message(
            message.chat.id,
            "❌ ID inválido. Escribe un ID numérico:",
            reply_markup=menu_cancelar()
        )
        bot.register_next_step_handler(msg, admin_desbloquear_id)
        return

    sec = leer_seguridad(uid_objetivo)
    if sec is None:
        bot.send_message(message.chat.id, "❌ Ese usuario no existe.", reply_markup=menu_admin())
        return

    guardar_seguridad(uid_objetivo, PIN_RESET_ADMIN, 0, "ACTIVO")
    agregar_historial(uid_objetivo, f"Cuenta desbloqueada por admin. PIN reseteado a {PIN_RESET_ADMIN}")

    bot.send_message(
        message.chat.id,
        f"✅ Usuario <code>{uid_objetivo}</code> desbloqueado.\nPIN reseteado a <code>{PIN_RESET_ADMIN}</code>.",
        reply_markup=menu_admin()
    )

    try:
        bot.send_message(
            int(uid_objetivo),
            f"🔓 Tu cuenta fue desbloqueada por el administrador.\nTu nuevo PIN temporal es: <code>{PIN_RESET_ADMIN}</code>",
            reply_markup=menu_usuario()
        )
    except Exception:
        pass

@bot.message_handler(func=lambda m: texto_msg(m) == "📥 Depósitos Pendientes")
def admin_ver_pendientes(message):
    cancelar_flujo(message.chat.id)

    if not es_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ No autorizado.")
        return

    pendientes = leer_pendientes()
    lista = [d for d in pendientes.values() if d.get("estado") == "PENDIENTE"]

    if not lista:
        bot.send_message(message.chat.id, "📭 No hay depósitos pendientes.", reply_markup=menu_admin())
        return

    texto = "📥 <b>Depósitos pendientes:</b>\n\n"
    for dep in lista[-10:]:
        texto += (
            f"• ID: <code>{dep['deposit_id']}</code>\n"
            f"  Usuario: <code>{dep['user_id']}</code>\n"
            f"  Monto CUP: <b>{formato_cup(dep['monto_cup'])}</b>\n"
            f"  A acreditar: <b>{formato_usd(dep['monto_usd'])}</b>\n"
            f"  Fecha: {dep['fecha']}\n\n"
        )

    bot.send_message(message.chat.id, texto, reply_markup=menu_admin())

@bot.message_handler(func=lambda m: texto_msg(m) == "💰 Balance Sistema")
def admin_balance_sistema(message):
    if not es_admin(message.from_user.id):
        return

    usuarios = leer_datos()
    total = sum(usuarios.values())
    bot.send_message(
        message.chat.id,
        f"💰 Balance total del sistema\n\n<b>{formato_usd(total)}</b>",
        reply_markup=menu_admin()
    )

@bot.message_handler(func=lambda m: texto_msg(m) == "👤 Buscar Usuario")
def admin_buscar_usuario(message):
    cancelar_flujo(message.chat.id)

    if not es_admin(message.from_user.id):
        return

    msg = bot.send_message(message.chat.id, "👤 Escribe el ID del usuario:", reply_markup=menu_cancelar())
    bot.register_next_step_handler(msg, admin_procesar_busqueda_usuario)

def admin_procesar_busqueda_usuario(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Operación cancelada.", reply_markup=menu_admin())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_admin(message)
        return

    if not es_admin(message.from_user.id):
        return

    uid = texto_msg(message)

    if not uid.isdigit():
        msg = bot.send_message(message.chat.id, "❌ ID inválido. Escribe un ID numérico:", reply_markup=menu_cancelar())
        bot.register_next_step_handler(msg, admin_procesar_busqueda_usuario)
        return

    usuarios = leer_datos()
    perfiles = leer_perfiles()
    sec = leer_seguridad(uid)

    saldo = usuarios.get(uid, 0.0)
    nombre = perfiles.get(uid, {}).get("nombre", "Desconocido")
    username = perfiles.get(uid, {}).get("username", "")
    estado = sec["estado"] if sec else "SIN_REGISTRO"

    bot.send_message(
        message.chat.id,
        "👤 <b>Datos del usuario</b>\n\n"
        f"ID: <code>{uid}</code>\n"
        f"Nombre: <b>{nombre}</b>\n"
        f"Username: @{username if username else 'sin_username'}\n"
        f"Estado: <b>{estado}</b>\n"
        f"Saldo: <b>{formato_usd(saldo)}</b>",
        reply_markup=menu_admin()
    )

@bot.message_handler(func=lambda m: texto_msg(m) == "💵 Acreditar Saldo")
def admin_acreditar_saldo(message):
    cancelar_flujo(message.chat.id)

    if not es_admin(message.from_user.id):
        return

    msg = bot.send_message(message.chat.id, "💵 Escribe el ID del usuario:", reply_markup=menu_cancelar())
    bot.register_next_step_handler(msg, admin_acreditar_uid)

def admin_acreditar_uid(message):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Operación cancelada.", reply_markup=menu_admin())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_admin(message)
        return

    if not es_admin(message.from_user.id):
        return

    uid = texto_msg(message)
    if not uid.isdigit():
        msg = bot.send_message(message.chat.id, "❌ ID inválido. Escribe un ID numérico:", reply_markup=menu_cancelar())
        bot.register_next_step_handler(msg, admin_acreditar_uid)
        return

    msg = bot.send_message(message.chat.id, "💵 Escribe el monto en USD a acreditar:", reply_markup=menu_cancelar())
    bot.register_next_step_handler(msg, admin_acreditar_monto, uid)

def admin_acreditar_monto(message, uid):
    if es_cancelacion(message):
        cancelar_flujo(message.chat.id)
        bot.send_message(message.chat.id, "✅ Operación cancelada.", reply_markup=menu_admin())
        return

    if texto_msg(message).startswith("/"):
        cancelar_flujo(message.chat.id)
        cmd_admin(message)
        return

    if not es_admin(message.from_user.id):
        return

    monto = parse_monto(texto_msg(message))
    if monto is None or monto <= 0:
        msg = bot.send_message(message.chat.id, "❌ Monto inválido. Escribe un número válido:", reply_markup=menu_cancelar())
        bot.register_next_step_handler(msg, admin_acreditar_monto, uid)
        return

    usuarios = leer_datos()
    usuarios[uid] = usuarios.get(uid, 0.0) + monto
    guardar_datos(usuarios)

    agregar_historial(uid, f"Crédito manual admin: +{formato_usd(monto)}")

    bot.send_message(
        message.chat.id,
        f"✅ Se acreditaron <b>{formato_usd(monto)}</b> al usuario <code>{uid}</code>.",
        reply_markup=menu_admin()
    )

    try:
        bot.send_message(
            int(uid),
            f"💵 El administrador acreditó saldo a tu cuenta.\nMonto: <b>{formato_usd(monto)}</b>",
            reply_markup=menu_usuario()
        )
    except Exception:
        pass

@bot.message_handler(func=lambda m: texto_msg(m) == "📊 Estadísticas")
def admin_estadisticas(message):
    if not es_admin(message.from_user.id):
        return

    usuarios = leer_datos()
    pendientes = leer_pendientes()

    total_usuarios = len(usuarios)
    total_balance = sum(usuarios.values())
    total_pendientes = len([d for d in pendientes.values() if d.get("estado") == "PENDIENTE"])
    total_aprobados = len([d for d in pendientes.values() if d.get("estado") == "APROBADO"])
    total_rechazados = len([d for d in pendientes.values() if d.get("estado") == "RECHAZADO"])

    bot.send_message(
        message.chat.id,
        "📊 <b>Estadísticas del sistema</b>\n\n"
        f"Usuarios registrados: <b>{total_usuarios}</b>\n"
        f"Balance total: <b>{formato_usd(total_balance)}</b>\n"
        f"Depósitos pendientes: <b>{total_pendientes}</b>\n"
        f"Depósitos aprobados: <b>{total_aprobados}</b>\n"
        f"Depósitos rechazados: <b>{total_rechazados}</b>",
        reply_markup=menu_admin()
    )

# =========================================================
# FALLBACKS
# =========================================================
@bot.message_handler(content_types=["photo"])
def foto_fuera_de_flujo(message):
    bot.send_message(
        message.chat.id,
        "📷 Si quieres hacer un depósito, usa el botón <b>➕ Depositar</b> primero.",
        reply_markup=menu_usuario()
    )

@bot.message_handler(content_types=["text"])
def fallback_texto(message):
    txt = texto_msg(message)

    if txt == "❌ Cancelar":
        cancelar_flujo(message.chat.id)
        if es_admin(message.from_user.id):
            bot.send_message(message.chat.id, "✅ Operación cancelada.", reply_markup=menu_admin())
        else:
            bot.send_message(message.chat.id, "✅ Operación cancelada.", reply_markup=menu_usuario())
        return

    if txt.startswith("/"):
        if txt not in ["/start", "/admin", "/cancel", "/vertasa"]:
            bot.send_message(message.chat.id, "❓ Comando no reconocido. Usa /start.", reply_markup=menu_usuario())
        return

    bot.send_message(message.chat.id, "Selecciona una opción del menú.", reply_markup=menu_usuario())

# =========================================================
# INICIO
# =========================================================
def iniciar():
    while True:
        try:
            print("MBanks iniciado...")
            bot.infinity_polling(timeout=40, long_polling_timeout=30)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    iniciar()
