import telebot
from telebot import apihelper, types
import os
import time

# ==========================================
# 1. CONFIGURACIÓN (CON PROXY Y REINTENTO)
# ==========================================
apihelper.proxy = {'https': 'http://proxy.server:3128'}

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

# --- [AQUÍ VAN TODAS LAS FUNCIONES DE LEER/GUARDAR DATOS QUE YA TENEMOS] ---
# (Las mantengo igual para no llenar el chat, usa las del código anterior)

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

# --- [AQUÍ VAN LOS HANDLERS DE START, DEPÓSITOS, TRANSFERENCIAS] ---
# (Usa los del código anterior)

@bot.message_handler(commands=['start'])
def start(message):
    uid = str(message.from_user.id)
    _, _, estado = leer_seguridad(uid)
    if estado == "SUSPENDIDO":
        bot.send_message(message.chat.id, "❌ Cuenta bloqueada.")
        return
    bot.send_message(message.chat.id, "🏦 **MBanks**", reply_markup=menu_usuario())

def menu_usuario():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('💰 Mi Saldo', '🧾 Mi Extracto', '💸 Transferir', '⚙️ Ajustes')
    return markup

@bot.message_handler(func=lambda m: True)
def principal(message):
    uid = str(message.from_user.id)
    if message.text == '💰 Mi Saldo':
        s = leer_datos().get(uid, 0.0)
        bot.send_message(message.chat.id, f"Saldo: ${s} USD")
    # ... resto de la lógica ...

# ==========================================
# 2. SISTEMA ANTICAÍDAS (EL CAMBIO IMPORTANTE)
# ==========================================
def iniciar_bot():
    while True:
        try:
            print("MBanks Operativo y Protegido...")
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            print(f"Error de conexión: {e}. Reintentando en 15 segundos...")
            time.sleep(15) # Espera un poco antes de volver a intentar

if __name__ == "__main__":
    iniciar_bot()
