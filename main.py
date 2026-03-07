import telebot
from telebot import apihelper, types
import os
import time

# ==========================================
# 1. CONFIGURACIÓN
# ==========================================
apihelper.proxy = {'https': 'http://proxy.server:3128'}
TOKEN = "8033243001:AAFZMqr1GiHAE0mAF25yRcrfLNPp3H-nnv0"
ADMIN_ID = "5220834019" 
bot = telebot.TeleBot(TOKEN)

# Archivos
DB_FILE = "banco.txt"
PERFILES_FILE = "perfiles.txt"
HISTORIAL_FILE = "historial.txt"
SEGURIDAD_FILE = "seguridad.txt"

# ==========================================
# 2. FUNCIONES DE APOYO
# ==========================================
def leer_datos():
    usuarios = {}
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            for linea in f:
                if ":" in linea:
                    uid, saldo = linea.strip().split(":")
                    usuarios[uid] = float(saldo)
    return usuarios

def guardar_datos(usuarios):
    with open(DB_FILE, "w") as f:
        for uid, s in usuarios.items(): f.write(f"{uid}:{s}\n")

def leer_seguridad(uid):
    if os.path.exists(SEGURIDAD_FILE):
        with open(SEGURIDAD_FILE, "r") as f:
            for linea in f:
                p = linea.strip().split("|")
                if p[0] == str(uid): return p[1], int(p[2]), p[3]
    return "0000", 0, "ACTIVO"

def guardar_seguridad(uid, pin, intentos, estado):
    datos = {}
    if os.path.exists(SEGURIDAD_FILE):
        with open(SEGURIDAD_FILE, "r") as f:
            for linea in f:
                p = linea.strip().split("|")
                datos[p[0]] = p[1:]
    datos[str(uid)] = [str(pin), str(intentos), estado]
    with open(SEGURIDAD_FILE, "w") as f:
        for u, d in datos.items(): f.write(f"{u}|{'|'.join(d)}\n")

# ==========================================
# 3. MENÚS
# ==========================================
def menu_usuario():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('💰 Mi Saldo', '🧾 Mi Extracto', '💸 Transferir', '⚙️ Ajustes')
    return markup

def menu_admin():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('💳 Balance CUP', '🔓 Desbloquear ID', '🏠 Menú Usuario')
    return markup

# ==========================================
# 4. MANEJO DE BOTONES (CALLBACKS)
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    uid = str(call.from_user.id)
    
    if call.data == "cambiar_pin":
        msg = bot.send_message(call.message.chat.id, "🔑 Introduce tu PIN actual para validar:")
        bot.register_next_step_handler(msg, validar_pin_viejo)
    
    elif call.data == "entrar_admin":
        if uid == ADMIN_ID:
            bot.send_message(call.message.chat.id, "🛠 Accediendo al Panel...", reply_markup=menu_admin())
    
    bot.answer_callback_query(call.id)

# ==========================================
# 5. LÓGICA DE CAMBIO DE PIN
# ==========================================
def validar_pin_viejo(message):
    uid = str(message.from_user.id)
    pin_real, _, _ = leer_seguridad(uid)
    if message.text == pin_real:
        msg = bot.send_message(message.chat.id, "✅ Correcto. Introduce tu NUEVO PIN (Solo números):")
        bot.register_next_step_handler(msg, finalizar_cambio_pin)
    else:
        bot.send_message(message.chat.id, "❌ PIN incorrecto. Operación cancelada.")

def finalizar_cambio_pin(message):
    if message.text.isdigit() and len(message.text) >= 4:
        guardar_seguridad(message.from_user.id, message.text, 0, "ACTIVO")
        bot.send_message(message.chat.id, "✅ PIN actualizado con éxito.")
    else:
        bot.send_message(message.chat.id, "❌ El PIN debe ser numérico y de al menos 4 dígitos.")

# ==========================================
# 6. MANEJADOR PRINCIPAL
# ==========================================
@bot.message_handler(func=lambda m: True)
def principal(message):
    uid = str(message.from_user.id)
    pin_real, intentos, estado = leer_seguridad(uid)

    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Tu cuenta está suspendida. Contacta al administrador.")
        return

    if message.text == '💰 Mi Saldo':
        s = leer_datos().get(uid, 0.0)
        bot.send_message(message.chat.id, f"💵 Saldo actual: **${s:.2f} USD**", parse_mode="Markdown")

    elif message.text == '🧾 Mi Extracto':
        txt = "🧾 **TUS ÚLTIMOS MOVIMIENTOS**\n\n"
        encontrado = False
        if os.path.exists(HISTORIAL_FILE):
            with open(HISTORIAL_FILE, "r") as f:
                for linea in f:
                    if linea.startswith(uid):
                        p = linea.strip().split("|")
                        txt += f"📅 {p[1]} | {p[2]} | **${p[3]}**\n"
                        encontrado = True
        bot.send_message(message.chat.id, txt if encontrado else "No tienes movimientos registrados.", parse_mode="Markdown")

    elif message.text == '💸 Transferir':
        msg = bot.send_message(message.chat.id, "👤 Indica el ID del destinatario:")
        bot.register_next_step_handler(msg, trans_paso1)

    elif message.text == '⚙️ Ajustes':
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("🔑 Cambiar PIN", callback_data="cambiar_pin"))
        # El botón de Admin SOLO aparece si tu ID coincide con ADMIN_ID
        if uid == ADMIN_ID:
            mk.add(types.InlineKeyboardButton("🛠 PANEL ADMIN", callback_data="entrar_admin"))
        bot.send_message(message.chat.id, "⚙️ **AJUSTES DE CUENTA**", reply_markup=mk, parse_mode="Markdown")

    elif message.text == '🏠 Menú Usuario':
        bot.send_message(message.chat.id, "Volviendo...", reply_markup=menu_usuario())

    # Funciones de Admin
    elif message.text == '🔓 Desbloquear ID' and uid == ADMIN_ID:
        msg = bot.send_message(message.chat.id, "Escribe el ID a desbloquear:")
        bot.register_next_step_handler(msg, admin_desbloqueo)

# ==========================================
# 7. FUNCIONES DE TRANSFERENCIA
# ==========================================
def trans_paso1(message):
    dest = message.text
    msg = bot.send_message(message.chat.id, "¿Cuánto quieres transferir (USD)?")
    bot.register_next_step_handler(msg, trans_paso2, dest)

def trans_paso2(message, dest):
    try:
        monto = float(message.text)
        msg = bot.send_message(message.chat.id, "🔒 Introduce tu PIN para confirmar:")
        bot.register_next_step_handler(msg, trans_final, dest, monto)
    except: bot.send_message(message.chat.id, "❌ Cantidad no válida.")

def trans_final(message, dest, monto):
    uid = str(message.from_user.id)
    pin_real, intentos, _ = leer_seguridad(uid)
    if message.text == pin_real:
        u = leer_datos()
        if u.get(uid, 0) >= monto:
            u[uid] -= monto; u[dest] = u.get(dest, 0) + monto; guardar_datos(u)
            guardar_seguridad(uid, pin_real, 0, "ACTIVO")
            bot.send_message(message.chat.id, "✅ Transferencia enviada con éxito.")
            bot.send_message(dest, f"💰 Has recibido ${monto} USD de ID: {uid}")
        else: bot.send_message(message.chat.id, "❌ Saldo insuficiente.")
    else:
        intentos += 1
        est = "SUSPENDIDO" if intentos >= 3 else "ACTIVO"
        guardar_seguridad(uid, pin_real, intentos, est)
        bot.send_message(message.chat.id, f"❌ PIN incorrecto. Intentos: {intentos}/3")

def admin_desbloqueo(message):
    target = message.text
    guardar_seguridad(target, "000010", 0, "ACTIVO")
    bot.send_message(ADMIN_ID, f"✅ ID {target} activado. PIN: 000010")

# ==========================================
# INICIO
# ==========================================
if __name__ == "__main__":
    while True:
        try:
            print("MBanks Operativo...")
            bot.polling(none_stop=True, timeout=40)
        except Exception as e:
            time.sleep(10)
