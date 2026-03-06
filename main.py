import telebot
import os

# Tu token ya integrado
TOKEN = "8033243001:AAFZMqr1GiHAE0mAF25yRcrfLNPp3H-nnv0"
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "¡Bot activo! Funcionando desde la nube.")

@bot.message_handler(func=lambda m: True)
def echo_all(message):
    bot.reply_to(message, f"Recibido: {message.text}")

if __name__ == "__main__":
    bot.infinity_polling()
