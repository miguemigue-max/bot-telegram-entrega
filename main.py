import telebot
from telebot import apihelper, types
import os
import time

# ==========================================
# 1. CONFIGURACIÓN (CONEXIÓN SEGURA)
# ==========================================
apihelper.proxy = {'https': 'http://proxy.server:3128'}

# REVISA QUE ESTE ID SEA EL TUYO (Puedes verlo al escribirle al bot)
TOKEN = "8033243001:AAFZMqr1GiHAE0mAF25yRcrfLNPp3H-nnv0"
ADMIN_ID = "5220834019" 
bot = telebot.TeleBot(TOKEN)

# Archivos de Base de Datos
DB_FILE = "banco.txt"
TASA_IN_FILE = "tasa_in.txt"
TASA_OUT_FILE = "tasa_out.txt"
PERFILES_FILE = "perfiles.txt"
HISTORIAL_FILE = "historial.txt"
SEGURIDAD_FILE = "seguridad.txt"

depositos_pendientes = {}

# ==========================================
# 2. FUNCIONES DE BASE DE DATOS
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
        for uid, s in usuarios.items():
            f.write(f"{uid}:{s}\n")

def leer_tasas():
    t_in, t_out = 510.0, 490.0
    if os.path.exists(TASA_IN_FILE):
        with open(TASA_IN_FILE, "r") as f: t_in = float(f.read().strip())
    if os.path.exists(TASA_OUT_FILE):
        with open(TASA_OUT_FILE, "r") as f: t_out = float(f.read().strip())
    return t_in, t_out

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
        for u, d in datos.items():
            f.write(f"{u}|{'|'.join(d)}\n")

def registrar_movimiento(uid, tipo, monto, detalle):
    fecha = time.strftime("%d/%m %H:%M")
    with open(HISTORIAL_FILE, "a") as f:
        f.write(f"{uid}|{fecha}|{tipo}|{monto}|{detalle}\n")

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
# 4. HANDLERS (START Y ADMIN)
# ==========================================
@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.from_user.id)
    # Esto te servirá para confirmar tu ID en la consola de PythonAnywhere
    print(f"DEBUG: El usuario {message.from_user.first_name} tiene ID: {uid}")
    
    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta suspendida.")
        return

    bot.send_message(message.chat.id, "🏦 **MBanks**", reply_markup=menu_usuario())

@bot.message_handler(commands=['admin'])
def admin_command(message):
    uid = str(message.from_user.id)
    if uid == ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 **Panel de Administrador**", reply_markup=menu_admin())
    else:
        bot.send_message(message.chat.id, "❌ No tienes permisos de administrador.")

# ==========================================
# 5. DEPÓSITOS Y CALLBACKS
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = str(call.from_user.id)
    
    if call.data == "iniciar_deposito":
        msg = bot.send_message(call.message.chat.id, "💰 ¿Cuánto CUP vas a depositar?")
        bot.register_next_step_handler(msg, dep_paso2)
    
    elif call.data.startswith("apr_"):
        if uid != ADMIN_ID: return
        _, target, m_usd, m_cup = call.data.split("_")
        u = leer_datos(); u[target] = u.get(target, 0) + float(m_usd); guardar_datos(u)
        registrar_movimiento(target, "Depósito", m_usd, f"CUP:{m_cup}")
        bot.send_message(target, f"✅ Depósito aprobado: +${m_usd} USD.")
        bot.edit_message_caption("✅ APROBADO", call.message.chat.id, call.message.message_id)
    
    bot.answer_callback_query(call.id)

def dep_paso2(message):
    try:
        cup = float(message.text)
        t_in, _ = leer_tasas()
        usd = round(cup / t_in, 2)
        depositos_pendientes[str(message.from_user.id)] = {'c': cup, 'u': usd}
        bot.send_message(message.chat.id, f"Recibirás: ${usd} USD.\nEnvía FOTO del comprobante:")
        bot.register_next_step_handler(message, dep_paso3)
    except: bot.send_message(message.chat.id, "❌ Usa solo números.")

def dep_paso3(message):
    uid = str(message.from_user.id)
    if message.content_type == 'photo':
        d = depositos_pendientes[uid]
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("Aprobar", callback_data=f"apr_{uid}_{d['u']}_{d['c']}"))
        bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"ID: {uid} | {d['c']} CUP", reply_markup=mk)
        bot.send_message(message.chat.id, "✅ Enviado.")

# ==========================================
# 6. LÓGICA PRINCIPAL
# ==========================================
@bot.message_handler(func=lambda m: True)
def principal(message):
    uid = str(message.from_user.id)
    pin_real, intentos, estado = leer_seguridad(uid)

    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta bloqueada.")
        return

    if message.text == '💰 Mi Saldo':
        s = leer_datos().get(uid, 0.0)
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Depositar", callback_data="iniciar_deposito"))
        bot.send_message(message.chat.id, f"Saldo: ${s} USD", reply_markup=mk)

    elif message.text == '🏠 Menú Usuario':
        bot.send_message(message.chat.id, "Menú principal", reply_markup=menu_usuario())

    elif message.text == '💳 Balance CUP' and uid == ADMIN_ID:
        total = 0
        if os.path.exists(HISTORIAL_FILE):
            with open(HISTORIAL_FILE, "r") as f:
                for l in f:
                    if "Depósito" in l:
                        try: total += float(l.split("|")[4].split(":")[1])
