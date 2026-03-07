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
TASA_IN_FILE = "tasa_in.txt"
TASA_OUT_FILE = "tasa_out.txt"
PERFILES_FILE = "perfiles.txt"
HISTORIAL_FILE = "historial.txt"
SEGURIDAD_FILE = "seguridad.txt"

depositos_pendientes = {}

# ==========================================
# 2. FUNCIONES DE DATOS
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
        for u, d in datos.items(): f.write(f"{u}|{'|'.join(d)}\n")

def registrar_movimiento(uid, tipo, monto, detalle):
    fecha = time.strftime("%d/%m %H:%M")
    with open(HISTORIAL_FILE, "a") as f: f.write(f"{uid}|{fecha}|{tipo}|{monto}|{detalle}\n")

# ==========================================
# 3. MENÚS DINÁMICOS
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
# 4. HANDLERS DE COMANDOS
# ==========================================
@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta bloqueada.")
        return
    bot.send_message(message.chat.id, "🏦 **MBanks**", reply_markup=menu_usuario())

@bot.message_handler(commands=['admin'])
def admin_cmd(message):
    if str(message.from_user.id) == ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 **MODO ADMINISTRADOR**", reply_markup=menu_admin())
    else:
        bot.send_message(message.chat.id, "❌ Acceso denegado.")

# ==========================================
# 5. CALLBACKS (BOTONES)
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
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
    
    elif call.data == "entrar_admin":
        bot.send_message(call.message.chat.id, "🛠 Entrando al panel...", reply_markup=menu_admin())

    bot.answer_callback_query(call.id)

# ==========================================
# 6. LÓGICA DE DEPÓSITOS
# ==========================================
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
# 7. MANEJADOR PRINCIPAL (TEXTO)
# ==========================================
@bot.message_handler(func=lambda m: True)
def text_handler(message):
    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)

    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta bloqueada.")
        return

    if message.text == '💰 Mi Saldo':
        s = leer_datos().get(uid, 0.0)
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Depositar", callback_data="iniciar_deposito"))
        bot.send_message(message.chat.id, f"Saldo: ${s} USD", reply_markup=mk)

    elif message.text == '🧾 Mi Extracto':
        txt = "🧾 **EXTRACTO**\n"
        if os.path.exists(HISTORIAL_FILE):
            with open(HISTORIAL_FILE, "r") as f:
                for l in f:
                    if l.startswith(uid): txt += f"• {l.split('|')[1]}: {l.split('|')[2]} ${l.split('|')[3]}\n"
        bot.send_message(message.chat.id, txt)

    elif message.text == '⚙️ Ajustes':
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("Cambiar PIN", callback_data="cambiar_pin"))
        # Si eres tú, te sale el botón secreto de Admin aquí
        if uid == ADMIN_ID:
            mk.add(types.InlineKeyboardButton("🛠 PANEL ADMIN", callback_data="entrar_admin"))
        bot.send_message(message.chat.id, "⚙️ Ajustes de cuenta:", reply_markup=mk)

    elif message.text == '🏠 Menú Usuario':
        bot.send_message(message.chat.id, "Cambiando a vista de usuario...", reply_markup=menu_usuario())

    elif message.text == '💳 Balance CUP' and uid == ADMIN_ID:
        total = 0
        if os.path.exists(HISTORIAL_FILE):
            with open(HISTORIAL_FILE, "r") as f:
                for l in f:
                    if "Depósito" in l:
                        try: total += float(l.split("|")[4].split(":")[1])
                        except: pass
        bot.send_message(ADMIN_ID, f"💰 **Total en Cuba:** {total:.2f} CUP")

    elif message.text == '🔓 Desbloquear ID' and uid == ADMIN_ID:
        msg = bot.send_message(ADMIN_ID, "Escribe el ID a desbloquear:")
        bot.register_next_step_handler(msg, unlock_logic)

def unlock_logic(message):
    guardar_seguridad(message.text, "000010", 0, "ACTIVO")
    bot.send_message(ADMIN_ID, f"✅ ID {message.text} reseteado a 000010.")

# ==========================================
# 8. BUCLE DE RECONEXIÓN
# ==========================================
def run_bot():
    while True:
        try:
            print("Bot Activo...")
            bot.polling(none_stop=True, timeout=30)
        except Exception as e:
            print(f"Error: {e}. Reintentando...")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
