from flask import Flask, request, render_template_string, redirect, url_for, session, flash, abort, send_file
from datetime import datetime
from functools import wraps
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import uuid
import io
import secrets

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

app = Flask(__name__)
app.secret_key = "cambia-esta-clave-secreta-por-una-mas-segura"

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "recargas_v2.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

CITIES_CUBA = [
    "Pinar del Río", "Artemisa", "La Habana", "Mayabeque", "Matanzas",
    "Cienfuegos", "Villa Clara", "Sancti Spíritus", "Ciego de Ávila",
    "Camagüey", "Las Tunas", "Holguín", "Granma", "Santiago de Cuba",
    "Guantánamo", "Isla de la Juventud"
]

RECHARGE_OPTIONS = [
    {"label": "Recarga 500 CUP", "price_cup": 14500},
    {"label": "Recarga 750 CUP", "price_cup": 14500},
    {"label": "Recarga 1000 CUP", "price_cup": 14500},
    {"label": "Recarga 1250 CUP", "price_cup": 14500},
]

GIFT_CARD_BRANDS = ["Amazon", "Apple", "Google Play", "Steam"]
GIFT_CARD_VALUES = [5, 10, 15, 20, 25, 50, 100]
CRYPTO_NETWORKS = ["USDT", "TRC20", "Bitcoin"]

PRODUCTS = {
    "Recargas": [item["label"] for item in RECHARGE_OPTIONS],
    "Cripto": ["Compra de cripto"],
    "Gift Cards": GIFT_CARD_BRANDS,
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def q(conn, sql: str, params=()):
    return conn.execute(sql, params)


def parse_float(value, default=0.0):
    try:
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return default


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_tag(first_name: str, last_name: str):
    base = f"{first_name}.{last_name}".lower().replace(" ", "")
    base = "".join(ch for ch in base if ch.isalnum() or ch in "._")
    if not base:
        base = "usuario"
    return f"@{base}{secrets.randbelow(900)+100}"


def get_unique_tag(first_name: str, last_name: str):
    while True:
        candidate = make_tag(first_name, last_name)
        conn = get_db()
        existing = q(conn, "SELECT id FROM users WHERE profile_tag = ?", (candidate,)).fetchone()
        conn.close()
        if not existing:
            return candidate


def init_db():
    conn = get_db()

    q(conn, """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT '',
            profile_tag TEXT NOT NULL UNIQUE,
            profile_photo TEXT NOT NULL DEFAULT '',
            referral_code TEXT NOT NULL UNIQUE,
            referred_by_user_id INTEGER,
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_locked INTEGER NOT NULL DEFAULT 0,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            last_login_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(referred_by_user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER PRIMARY KEY,
            cup_balance REAL NOT NULL DEFAULT 0,
            usd_balance REAL NOT NULL DEFAULT 0,
            usdt_balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS wallet_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency TEXT NOT NULL,
            amount REAL NOT NULL,
            direction TEXT NOT NULL,
            tx_type TEXT NOT NULL,
            description TEXT NOT NULL,
            reference TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_user_id INTEGER NOT NULL,
            receiver_user_id INTEGER NOT NULL,
            currency TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'Completado',
            created_at TEXT NOT NULL,
            FOREIGN KEY(sender_user_id) REFERENCES users(id),
            FOREIGN KEY(receiver_user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            service TEXT NOT NULL DEFAULT 'Recargas',
            plan_name TEXT NOT NULL DEFAULT '',
            extra_data TEXT NOT NULL DEFAULT '',
            total_cup REAL NOT NULL DEFAULT 0,
            payment_method TEXT NOT NULL DEFAULT 'externo',
            status TEXT NOT NULL DEFAULT 'Pendiente',
            payment_status TEXT NOT NULL DEFAULT 'Pago en revisión',
            proof_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            price_text TEXT NOT NULL,
            price_cup REAL NOT NULL DEFAULT 14500,
            description TEXT NOT NULL,
            bonus_1 TEXT NOT NULL,
            bonus_2 TEXT NOT NULL,
            bonus_3 TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Pendiente',
            created_at TEXT NOT NULL
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER NOT NULL DEFAULT 0,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    defaults = {
        "usd_cup": "510",
        "usdt_buy_cup": "580",
        "btc_usd": "85000",
        "giftcard_markup_percent": "10",
        "payment_card_label": "Tarjeta de pago",
        "payment_card_number": "9224 xxxx xxxx xxxx",
        "payment_card_holder": "Nombre de tu mamá",
        "payment_note": "Envía el importe exacto y sube el comprobante.",
        "referral_reward_usdt": "0.50",
    }
    for key, value in defaults.items():
        if not q(conn, "SELECT key FROM settings WHERE key = ?", (key,)).fetchone():
            q(conn, "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    admin_email = "admin@recargas.local"
    if not q(conn, "SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone():
        q(conn, """
            INSERT INTO users (
                first_name, last_name, email, password, city, profile_tag, profile_photo,
                referral_code, referred_by_user_id, is_admin, is_locked, failed_attempts,
                last_login_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', ?, NULL, 1, 0, 0, '', ?)
        """, (
            "Administrador",
            "General",
            admin_email,
            generate_password_hash("admin123"),
            "La Habana",
            "@admin999",
            "ADMIN999",
            now_str(),
        ))
        admin_id = q(conn, "SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()["id"]
        q(conn, """
            INSERT OR IGNORE INTO wallets (user_id, cup_balance, usd_balance, usdt_balance, created_at)
            VALUES (?, 0, 0, 0, ?)
        """, (admin_id, now_str()))

    if not q(conn, "SELECT id FROM promotions LIMIT 1").fetchone():
        q(conn, """
            INSERT INTO promotions
            (title, price_text, price_cup, description, bonus_1, bonus_2, bonus_3, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "PROMOCIÓN DEL 10 de marzo AL 15 de marzo",
            "14 500 CUP",
            14500,
            "Recarga promocional disponible para clientes en Cuba durante las fechas activas.",
            "25GB de navegación válidos para todas las redes.",
            "Datos ilimitados desde las 12:00 a.m. hasta las 7:00 a.m.",
            "Aplica a recargas entre 600 CUP y 1250 CUP.",
            1,
            now_str(),
        ))

    conn.commit()
    conn.close()


init_db()


def get_settings():
    conn = get_db()
    rows = q(conn, "SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    user = q(conn, "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def ensure_wallet(user_id):
    conn = get_db()
    exists = q(conn, "SELECT user_id FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    if not exists:
        q(conn, """
            INSERT INTO wallets (user_id, cup_balance, usd_balance, usdt_balance, created_at)
            VALUES (?, 0, 0, 0, ?)
        """, (user_id, now_str()))
        conn.commit()
    conn.close()


def get_wallet(user_id):
    ensure_wallet(user_id)
    conn = get_db()
    wallet = q(conn, "SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return wallet


def log_action(actor_user_id, action, details=""):
    conn = get_db()
    q(conn, """
        INSERT INTO audit_logs (actor_user_id, action, details, created_at)
        VALUES (?, ?, ?, ?)
    """, (actor_user_id, action, details, now_str()))
    conn.commit()
    conn.close()


def add_wallet_tx(user_id, currency, amount, direction, tx_type, description, reference=""):
    conn = get_db()
    q(conn, """
        INSERT INTO wallet_transactions
        (user_id, currency, amount, direction, tx_type, description, reference, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, currency, amount, direction, tx_type, description, reference, now_str()))
    conn.commit()
    conn.close()


def adjust_wallet(user_id, currency, amount, description, direction, tx_type="admin_adjustment", reference=""):
    ensure_wallet(user_id)
    field = {"CUP": "cup_balance", "USD": "usd_balance", "USDT": "usdt_balance"}[currency]
    sign = 1 if direction == "credit" else -1
    conn = get_db()
    q(conn, f"UPDATE wallets SET {field} = {field} + ? WHERE user_id = ?", (sign * amount, user_id))
    conn.commit()
    conn.close()
    add_wallet_tx(user_id, currency, amount, direction, tx_type, description, reference)


def get_active_promo():
    conn = get_db()
    promo = q(conn, "SELECT * FROM promotions WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    if not promo:
        promo = q(conn, "SELECT * FROM promotions ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return promo


def recharge_price(plan_name, promo):
    if promo:
        return float(promo["price_cup"])
    match = next((item for item in RECHARGE_OPTIONS if item["label"] == plan_name), None)
    return float(match["price_cup"] if match else 14500)


def gift_card_price_cup(value_usd, settings):
    usd_cup = parse_float(settings.get("usd_cup", "510"), 510)
    markup = parse_float(settings.get("giftcard_markup_percent", "10"), 10)
    return round(value_usd * usd_cup * (1 + markup / 100), 2)


def crypto_receive_text(network, cup_amount, settings):
    usd_cup = parse_float(settings.get("usd_cup", "510"), 510)
    usdt_buy_cup = parse_float(settings.get("usdt_buy_cup", "580"), 580)
    btc_usd = parse_float(settings.get("btc_usd", "85000"), 85000)
    if network in ("USDT", "TRC20"):
        amount = cup_amount / usdt_buy_cup if usdt_buy_cup else 0
        return f"Recibirás aprox. {amount:.2f} {network}"
    if network == "Bitcoin":
        btc_amount = cup_amount / (btc_usd * usd_cup) if btc_usd and usd_cup else 0
        return f"Recibirás aprox. {btc_amount:.8f} BTC"
    return ""


def generate_receipt_pdf(order_data):
    if not REPORTLAB_AVAILABLE:
        return None
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    y = height - 60
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, y, "Resumen de compra")
    y -= 30
    pdf.setFont("Helvetica", 11)
    lines = [
        f"Fecha: {now_str()}",
        f"Producto: {order_data.get('service', '')}",
        f"Opción: {order_data.get('plan_name', '')}",
        f"Referencia: {order_data.get('reference', '')}",
        f"Detalle: {order_data.get('extra_data', '')}",
        f"Total a pagar: {order_data.get('total_cup_text', '')}",
        f"Cliente: {order_data.get('customer_name', '')}",
    ]
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 22
    pdf.save()
    buffer.seek(0)
    return buffer


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if not user["is_admin"]:
            abort(403)
        return view_func(*args, **kwargs)
    return wrapped


BASE_HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root {
      --bg: #ffffff;
      --bg-2: #f8f4ff;
      --card: rgba(255,255,255,0.96);
      --text: #171717;
      --muted: #6b5f80;
      --border: rgba(138,5,190,0.10);
      --shadow: 0 18px 38px rgba(138,5,190,0.10);
      --accent: #8a05be;
      --accent-2: #b517ff;
      --accent-soft: rgba(181,23,255,0.10);
      --danger: #e54864;
      --success-bg: rgba(75, 211, 125, 0.12);
      --success-border: rgba(75, 211, 125, 0.24);
      --error-bg: rgba(255, 122, 138, 0.12);
      --error-border: rgba(255, 122, 138, 0.20);
      --info-bg: rgba(181, 23, 255, 0.10);
      --info-border: rgba(181, 23, 255, 0.18);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Arial, Helvetica, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, rgba(181,23,255,0.12), transparent 28%),
        radial-gradient(circle at top left, rgba(138,5,190,0.10), transparent 22%),
        linear-gradient(180deg, var(--bg-2) 0%, var(--bg) 100%);
      min-height: 100vh;
    }
    a { color: inherit; text-decoration: none; }
    .container { width: min(1120px, 92%); margin: 0 auto; }
    .nav {
      position: sticky; top: 0; z-index: 50;
      backdrop-filter: blur(18px);
      background: rgba(255,255,255,0.80);
      border-bottom: 1px solid var(--border);
    }
    .nav-inner { display: flex; justify-content: space-between; align-items: center; gap: 14px; padding: 14px 0; }
    .brand { font-weight: 800; font-size: 1.05rem; display: flex; align-items: center; gap: 10px; }
    .brand-mark {
      width: 28px; height: 28px; border-radius: 10px;
      display: inline-flex; align-items: center; justify-content: center;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white; font-size: 0.95rem; box-shadow: var(--shadow);
    }
    .nav-links { display: flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap; }
    .btn {
      display: inline-flex; align-items: center; justify-content: center;
      border-radius: 16px; padding: 12px 18px; font-weight: 700;
      border: 1px solid transparent; cursor: pointer; transition: 0.22s ease;
      text-align: center;
    }
    .btn:hover { transform: translateY(-2px); }
    .btn-primary {
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white; box-shadow: var(--shadow);
    }
    .btn-secondary {
      background: rgba(138,5,190,0.04);
      border-color: var(--border);
      color: var(--text);
    }
    .btn-danger {
      background: rgba(229,72,100,0.08);
      border-color: rgba(229,72,100,0.14);
      color: var(--danger);
    }
    .btn-buy {
      margin-top: 14px; width: 100%;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white;
    }
    .icon-btn {
      width: 44px; height: 44px; border-radius: 14px;
      border: 1px solid var(--border);
      background: rgba(138,5,190,0.05);
      display: inline-flex; align-items: center; justify-content: center;
      font-size: 1.1rem; cursor: pointer; box-shadow: var(--shadow); color: var(--text);
    }
    .menu-wrap { position: relative; }
    .menu-dropdown {
      position: absolute; top: calc(100% + 10px); right: 0;
      min-width: 230px; background: rgba(255,255,255,0.98);
      border: 1px solid var(--border); border-radius: 18px;
      box-shadow: 0 20px 40px rgba(138,5,190,0.16);
      padding: 10px; display: none; z-index: 80;
    }
    .menu-wrap:hover .menu-dropdown,
    .menu-wrap:focus-within .menu-dropdown { display: block; }
    .menu-item { display: block; padding: 12px 14px; border-radius: 12px; font-weight: 700; color: var(--text); }
    .menu-item:hover { background: rgba(138,5,190,0.06); }
    .menu-item-danger { color: var(--danger); }
    .hero { padding: 66px 0 34px; }
    .hero-grid { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 28px; align-items: center; }
    .badge {
      display: inline-block; padding: 8px 13px; border-radius: 999px;
      background: var(--accent-soft); border: 1px solid rgba(181,23,255,0.16);
      color: var(--accent); font-size: 0.88rem; margin-bottom: 16px; font-weight: 700;
    }
    h1 { margin: 0 0 14px; font-size: clamp(2.4rem, 5vw, 4.7rem); line-height: 1.02; letter-spacing: -0.03em; }
    h2 { margin: 0 0 12px; font-size: 1.9rem; }
    h3 { margin: 0 0 8px; }
    .subtitle { color: var(--muted); font-size: 1.08rem; line-height: 1.75; max-width: 58ch; margin-bottom: 24px; }
    .hero-actions { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; }
    .card {
      background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,244,255,0.96));
      border: 1px solid var(--border);
      border-radius: 28px;
      box-shadow: var(--shadow);
      animation: fadeUp 0.5s ease both, floatCard 5s ease-in-out infinite;
    }
    .price-card { padding: 28px; }
    .price-kicker { color: var(--accent); font-weight: 700; font-size: 0.92rem; margin-bottom: 8px; }
    .price { font-size: clamp(2rem, 4vw, 3.4rem); font-weight: 800; margin: 8px 0 16px; line-height: 1; }
    .promo-box {
      margin-top: 18px; padding: 18px; border-radius: 18px;
      background: rgba(138,5,190,0.04); border: 1px solid rgba(138,5,190,0.08);
    }
    .promo-box ul { margin: 10px 0 0 20px; padding: 0; color: var(--muted); line-height: 1.7; }
    .section { padding: 16px 0 52px; }
    .services-title { text-align: center; margin-bottom: 16px; }
    .services-title p { color: var(--muted); margin: 0; }
    .services-scroll {
      display: flex; gap: 14px; overflow-x: auto; padding: 8px 0 12px; margin-top: 12px;
      scrollbar-width: none; justify-content: center;
    }
    .services-scroll::-webkit-scrollbar { display: none; }
    .service-item {
      min-width: 126px; background: rgba(255,255,255,0.9); border-radius: 20px;
      padding: 18px 12px; text-align: center; border: 1px solid rgba(138,5,190,0.08);
      box-shadow: 0 8px 18px rgba(138,5,190,0.10); flex-shrink: 0; transition: 0.22s ease;
      animation: fadeUp 0.6s ease both;
    }
    .service-item:hover { transform: translateY(-4px); background: rgba(255,255,255,1); }
    .service-item .icon { font-size: 1.65rem; margin-bottom: 8px; line-height: 1; }
    .service-item span { display: block; font-size: 0.95rem; font-weight: 700; color: var(--text); }
    .profile-hero {
      display: grid; grid-template-columns: auto 1fr auto; gap: 18px; align-items: center;
      margin-bottom: 24px; padding: 22px; background: rgba(138,5,190,0.03);
      border-radius: 24px; border: 1px solid rgba(138,5,190,0.08);
    }
    .avatar {
      width: 84px; height: 84px; border-radius: 50%;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      display: flex; align-items: center; justify-content: center;
      font-size: 1.6rem; font-weight: 800; color: white; overflow: hidden; box-shadow: var(--shadow);
    }
    .avatar img { width: 100%; height: 100%; object-fit: cover; }
    .tag-pill {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 8px 12px; border-radius: 999px;
      background: rgba(181,23,255,0.10); border: 1px solid rgba(181,23,255,0.16);
      color: var(--accent); font-weight: 700; font-size: 0.9rem;
    }
    .wallet-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 16px; }
    .wallet-card { padding: 22px; }
    .wallet-label { color: var(--muted); font-size: 0.9rem; margin-bottom: 10px; }
    .wallet-amount { font-size: 1.9rem; font-weight: 800; }
    .page-wrap { padding: 36px 0 56px; }
    .auth-shell { min-height: calc(100vh - 90px); display: flex; align-items: center; justify-content: center; padding: 36px 0; }
    .auth-card, .panel, .form-card { padding: 28px; }
    .auth-card { width: min(560px, 92vw); }
    form { display: grid; gap: 14px; }
    label { display: block; font-size: 0.92rem; margin-bottom: 6px; font-weight: 700; color: var(--text); }
    input, textarea, select {
      width: 100%; border-radius: 16px; border: 1px solid rgba(138,5,190,0.10);
      background: rgba(255,255,255,0.90); color: var(--text); padding: 14px 15px; font-size: 1rem; outline: none;
    }
    textarea { min-height: 110px; resize: vertical; }
    input:focus, textarea:focus, select:focus { border-color: rgba(181,23,255,0.40); box-shadow: 0 0 0 4px rgba(181,23,255,0.10); }
    .flash-wrap { display: grid; gap: 10px; margin-bottom: 18px; }
    .flash { padding: 13px 15px; border-radius: 14px; font-weight: 700; border: 1px solid transparent; animation: fadeUp 0.35s ease both; }
    .flash-success { background: var(--success-bg); border-color: var(--success-border); color: #198754; }
    .flash-error { background: var(--error-bg); border-color: var(--error-border); color: #b4233d; }
    .flash-info { background: var(--info-bg); border-color: var(--info-border); color: var(--accent); }
    .stats { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 16px; margin-bottom: 22px; }
    .stat { padding: 22px; }
    .stat .label { color: var(--muted); font-size: 0.95rem; margin-bottom: 10px; }
    .stat .value { font-size: 2rem; font-weight: 800; line-height: 1; }
    .top-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
    .table-card { overflow: hidden; }
    table { width: 100%; border-collapse: collapse; background: transparent; }
    th, td { padding: 14px; text-align: left; border-bottom: 1px solid rgba(138,5,190,0.08); vertical-align: top; }
    th { color: var(--text); background: rgba(138,5,190,0.04); font-size: 0.92rem; }
    td { color: var(--muted); font-size: 0.98rem; }
    .status { display: inline-flex; align-items: center; justify-content: center; padding: 7px 11px; border-radius: 999px; font-size: 0.84rem; font-weight: 800; white-space: nowrap; }
    .status-pendiente { background: rgba(245,158,11,0.12); color: #a25f00; border: 1px solid rgba(245,158,11,0.18); }
    .status-procesando { background: rgba(181,23,255,0.14); color: var(--accent); border: 1px solid rgba(181,23,255,0.18); }
    .status-completado { background: rgba(75,211,125,0.14); color: #198754; border: 1px solid rgba(75,211,125,0.18); }
    .status-cancelado { background: rgba(255,122,138,0.14); color: #b4233d; border: 1px solid rgba(255,122,138,0.18); }
    .empty { padding: 28px; text-align: center; color: var(--muted); }
    .footer { padding: 26px 0 46px; color: var(--muted); border-top: 1px solid rgba(138,5,190,0.08); margin-top: 10px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
    .proof-note { font-size: 0.92rem; color: var(--muted); }
    .loader {
      width: 20px; height: 20px; border-radius: 50%;
      border: 3px solid rgba(181,23,255,0.18);
      border-top-color: var(--accent);
      animation: spin 1s linear infinite;
      display: inline-block;
      vertical-align: middle;
      margin-right: 8px;
    }
    @keyframes fadeUp { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }
    @keyframes floatCard {
      0%, 100% { transform: translateY(0); }
      50% { transform: translateY(-4px); }
    }
    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    @media (max-width: 980px) {
      .hero-grid, .stats, .grid-2, .wallet-grid { grid-template-columns: 1fr; }
      .nav-inner { flex-direction: row; align-items: center; }
      h1 { font-size: clamp(2rem, 10vw, 3.3rem); }
      .subtitle { max-width: none; }
      .profile-hero { grid-template-columns: 1fr; text-align: center; }
      .profile-hero .avatar { margin: 0 auto; }
    }
    @media (max-width: 740px) {
      .container { width: min(94%, 100%); }
      table, thead, tbody, th, td, tr { display: block; }
      thead { display: none; }
      tr { border-bottom: 1px solid rgba(138,5,190,0.10); padding: 10px 0; }
      td { border-bottom: none; padding: 8px 14px; }
      td::before { content: attr(data-label); display: block; font-size: 0.82rem; color: var(--text); font-weight: 800; margin-bottom: 4px; }
    }
    @media (max-width: 640px) {
      .auth-shell { padding: 24px 0 40px; min-height: auto; }
      .auth-card { width: min(94vw, 100%); padding: 22px; border-radius: 22px; }
      .auth-card h2 { font-size: 2.2rem; line-height: 1.05; }
      .auth-card .btn, .hero-actions .btn { width: 100%; }
      .hero-actions { flex-direction: column; }
      .nav-links { width: auto; flex-direction: row; align-items: center; }
      .nav-links .btn { width: auto; padding: 8px 14px; font-size: 0.9rem; }
      .brand span:last-child { font-size: 0.95rem; }
      .services-scroll { justify-content: flex-start; }
      .menu-dropdown { right: 0; min-width: 190px; }
    }
  </style>
</head>
<body>
  <nav class="nav">
    <div class="container nav-inner">
      <div class="brand">
        <a href="{{ url_for('home') }}" style="display:flex; align-items:center; gap:10px;">
          <span class="brand-mark">◉</span>
          <span>Recargas a Cuba</span>
        </a>
      </div>
      <div class="nav-links">
        {% if user %}
          {% if user['is_admin'] %}
            <a class="icon-btn" href="{{ url_for('admin_dashboard') }}" title="Dashboard">📊</a>
          {% endif %}
          <div class="menu-wrap">
            <button class="icon-btn" type="button" aria-label="Menú">☰</button>
            <div class="menu-dropdown">
              {% if not user['is_admin'] %}
                <a class="menu-item" href="{{ url_for('profile') }}">Mi perfil</a>
                <a class="menu-item" href="{{ url_for('wallet_page') }}">Mi billetera</a>
                <a class="menu-item" href="{{ url_for('transfer_money') }}">Enviar dinero</a>
                <a class="menu-item" href="{{ url_for('my_orders') }}">Mis pedidos</a>
                <a class="menu-item" href="{{ url_for('new_order') }}">Nuevo pedido</a>
                <a class="menu-item" href="{{ url_for('forgot_password') }}">Cambiar contraseña</a>
              {% else %}
                <a class="menu-item" href="{{ url_for('admin_dashboard') }}">Dashboard admin</a>
              {% endif %}
              <a class="menu-item menu-item-danger" href="{{ url_for('logout') }}">Cerrar sesión</a>
            </div>
          </div>
        {% else %}
          <a class="btn btn-secondary" href="{{ url_for('login') }}">Entrar</a>
          <a class="btn btn-primary" href="{{ url_for('register') }}">Crear cuenta</a>
        {% endif %}
      </div>
    </div>
  </nav>
  {% if not hide_container %}<div class="container">{% endif %}
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        <div class="flash-wrap" style="padding-top: 18px;">
          {% for category, message in messages %}
            <div class="flash flash-{{ category }}">{{ message }}</div>
          {% endfor %}
        </div>
      {% endif %}
    {% endwith %}
  {% if not hide_container %}</div>{% endif %}
  {{ content|safe }}
</body>
</html>
"""


def render_page(content, title="Recargas a Cuba", user=None, hide_container=False, **context):
    rendered = render_template_string(content, user=user, **context)
    return render_template_string(BASE_HTML, content=rendered, title=title, user=user, hide_container=hide_container)

@app.route("/")
def home():
    user = current_user()
    promo = get_active_promo()

    content = """
    <header class="hero">
      <div class="container hero-grid">
        <div>
          <div class="badge">Productos que ofrecemos</div>
          <h1>Recargas y activos digitales en un solo lugar.</h1>
          <p class="subtitle">
            Ofrecemos recargas, cripto y tarjetas de regalo. También puedes usar tu saldo interno,
            enviar dinero entre usuarios y gestionar tu cuenta con tu propio @tag.
          </p>
          <div class="hero-actions">
            {% if user %}
              {% if user['is_admin'] %}
                <a class="btn btn-primary" href="{{ url_for('admin_dashboard') }}">Ir al dashboard admin</a>
              {% else %}
                <a class="btn btn-primary" href="{{ url_for('new_order') }}">Hacer pedido</a>
                <a class="btn btn-secondary" href="{{ url_for('wallet_page') }}">Mi billetera</a>
              {% endif %}
            {% endif %}
          </div>
        </div>

        <div class="card price-card">
          <div class="price-kicker">{{ promo['title'] if promo else 'Promoción activa' }}</div>
          <div class="price">{{ promo['price_text'] if promo else '14 500 CUP' }}</div>
          <div class="subtitle" style="margin:0; font-size:1rem;">
            {{ promo['description'] if promo else 'Recarga promocional disponible.' }}
          </div>

          <div class="promo-box">
            <strong>Bonificación actual</strong>
            <ul>
              <li>{{ promo['bonus_1'] if promo else '25GB de navegación válidos para todas las redes.' }}</li>
              <li>{{ promo['bonus_2'] if promo else 'Datos ilimitados desde las 12:00 a.m. hasta las 7:00 a.m.' }}</li>
              <li>{{ promo['bonus_3'] if promo else 'Aplica a recargas entre 600 CUP y 1250 CUP.' }}</li>
            </ul>
          </div>

          <a class="btn btn-buy" href="{{ url_for('new_order', service='Recargas') }}">Comprar</a>
        </div>
      </div>
    </header>

    <section class="section">
      <div class="container">
        <div class="services-title">
          <h2>Productos que ofrecemos</h2>
          <p>Desliza para ver las opciones disponibles.</p>
        </div>

        <div class="services-scroll">
          <a class="service-item" href="{{ url_for('new_order', service='Recargas') if user else url_for('login') }}">
            <div class="icon">📱</div>
            <span>Recargas</span>
          </a>
          <a class="service-item" href="{{ url_for('new_order', service='Cripto') if user else url_for('login') }}">
            <div class="icon">💵</div>
            <span>Cripto</span>
          </a>
          <a class="service-item" href="{{ url_for('new_order', service='Gift Cards') if user else url_for('login') }}">
            <div class="icon">🎁</div>
            <span>Gift Cards</span>
          </a>
          <a class="service-item" href="{{ url_for('wallet_page') if user else url_for('login') }}">
            <div class="icon">👛</div>
            <span>Billetera</span>
          </a>
        </div>
      </div>
    </section>

    <footer class="footer">
      <div class="container">Plataforma de servicios · {{ year }}</div>
    </footer>
    """
    return render_page(
        content,
        title="Recargas a Cuba",
        user=user,
        promo=promo,
        year=datetime.now().year,
    )


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_file(UPLOAD_DIR / filename)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("home"))

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        city = request.form.get("city", "").strip()
        desired_tag = request.form.get("profile_tag", "").strip()
        referral_code = request.form.get("referral_code", "").strip().upper()

        if desired_tag and not desired_tag.startswith("@"):
            desired_tag = "@" + desired_tag

        if not first_name or not last_name or not email or not password or not city or not desired_tag:
            flash("Completa todos los campos.", "error")
        elif city not in CITIES_CUBA:
            flash("Selecciona una ciudad válida.", "error")
        elif len(password) < 6:
            flash("La contraseña debe tener al menos 6 caracteres.", "error")
        else:
            clean = desired_tag.lower().strip()
            clean = "@" + "".join(ch for ch in clean.replace("@", "") if ch.isalnum() or ch in "._")

            if clean == "@":
                flash("El @tag no es válido.", "error")
            else:
                conn = get_db()

                email_exists = q(conn, "SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                tag_exists = q(conn, "SELECT id FROM users WHERE profile_tag = ?", (clean,)).fetchone()

                if email_exists:
                    conn.close()
                    flash("Ese correo ya está registrado.", "error")
                elif tag_exists:
                    conn.close()
                    flash("Ese @tag ya está en uso.", "error")
                else:
                    referred_by_user_id = None
                    if referral_code:
                        inviter = q(conn, "SELECT id FROM users WHERE referral_code = ?", (referral_code,)).fetchone()
                        if inviter:
                            referred_by_user_id = inviter["id"]

                    user_ref_code = "REF" + secrets.token_hex(4).upper()
                    while q(conn, "SELECT id FROM users WHERE referral_code = ?", (user_ref_code,)).fetchone():
                        user_ref_code = "REF" + secrets.token_hex(4).upper()

                    q(conn, """
                        INSERT INTO users (
                            first_name, last_name, email, password, city, profile_tag, profile_photo,
                            referral_code, referred_by_user_id, is_admin, is_locked, failed_attempts,
                            last_login_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 0, 0, 0, '', ?)
                    """, (
                        first_name,
                        last_name,
                        email,
                        generate_password_hash(password),
                        city,
                        clean,
                        user_ref_code,
                        referred_by_user_id,
                        now_str(),
                    ))

                    new_user = q(conn, "SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                    user_id = new_user["id"]

                    q(conn, """
                        INSERT INTO wallets (user_id, cup_balance, usd_balance, usdt_balance, created_at)
                        VALUES (?, 0, 0, 0, ?)
                    """, (user_id, now_str()))

                    if referred_by_user_id:
                        settings = get_settings()
                        reward = parse_float(settings.get("referral_reward_usdt", "0.50"), 0.50)
                        q(conn, "UPDATE wallets SET usdt_balance = usdt_balance + ? WHERE user_id = ?", (reward, referred_by_user_id))
                        q(conn, """
                            INSERT INTO wallet_transactions
                            (user_id, currency, amount, direction, tx_type, description, reference, created_at)
                            VALUES (?, 'USDT', ?, 'credit', 'referral_reward', ?, ?, ?)
                        """, (
                            referred_by_user_id,
                            reward,
                            f"Bono por referido: {email}",
                            clean,
                            now_str(),
                        ))

                    conn.commit()
                    conn.close()

                    session["user_id"] = user_id
                    log_action(user_id, "user_registered", "Registro de nuevo usuario")
                    flash("Cuenta creada correctamente.", "success")
                    return redirect(url_for("home"))

    content = """
    <div class="auth-shell">
      <div class="card auth-card">
        <h2>Crear cuenta</h2>
        <p class="subtitle" style="font-size:1rem; margin-bottom:16px;">
          Regístrate para hacer pedidos, usar tu billetera, transferir saldo y ganar referidos.
        </p>

        <form method="post">
          <div><label>Nombre</label><input type="text" name="first_name" required></div>
          <div><label>Apellidos</label><input type="text" name="last_name" required></div>
          <div><label>Correo electrónico</label><input type="email" name="email" required></div>
          <div><label>Tu @tag único</label><input type="text" name="profile_tag" placeholder="@miguel" required></div>

          <div>
            <label>Ciudad</label>
            <select name="city" required>
              <option value="">Selecciona tu ciudad</option>
              {% for city in cities %}
                <option value="{{ city }}">{{ city }}</option>
              {% endfor %}
            </select>
          </div>

          <div><label>Contraseña</label><input type="password" name="password" required></div>
          <div><label>Código de referido (opcional)</label><input type="text" name="referral_code" placeholder="REFXXXXXXX"></div>

          <button class="btn btn-primary" type="submit">Crear cuenta</button>
        </form>

        <p class="subtitle" style="font-size:1rem; margin:16px 0 0;">
          ¿Ya tienes cuenta? <a href="{{ url_for('login') }}"><strong>Inicia sesión</strong></a>
        </p>
      </div>
    </div>
    """
    return render_page(content, title="Crear cuenta", user=None, hide_container=True, cities=CITIES_CUBA)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        conn = get_db()
        user = q(conn, "SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if not user:
            conn.close()
            flash("Correo o contraseña incorrectos.", "error")
        elif user["is_locked"]:
            conn.close()
            flash("Tu cuenta está bloqueada. Solicita recuperación o contacta al administrador.", "error")
        elif not check_password_hash(user["password"], password):
            failed = int(user["failed_attempts"]) + 1
            is_locked = 1 if failed >= 5 else 0
            q(conn, "UPDATE users SET failed_attempts = ?, is_locked = ? WHERE id = ?", (failed, is_locked, user["id"]))
            conn.commit()
            conn.close()
            flash("Correo o contraseña incorrectos.", "error")
        else:
            q(conn, "UPDATE users SET failed_attempts = 0, is_locked = 0, last_login_at = ? WHERE id = ?", (now_str(), user["id"]))
            conn.commit()
            conn.close()

            session["user_id"] = user["id"]
            log_action(user["id"], "user_login", "Inicio de sesión correcto")
            flash("Sesión iniciada correctamente.", "success")

            if user["is_admin"]:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("home"))

    content = """
    <div class="auth-shell">
      <div class="card auth-card">
        <h2>Iniciar sesión</h2>
        <p class="subtitle" style="font-size:1rem; margin-bottom:16px;">
          Entra a tu cuenta para revisar pedidos, billetera y perfil.
        </p>

        <form method="post">
          <div><label>Correo electrónico</label><input type="email" name="email" required></div>
          <div><label>Contraseña</label><input type="password" name="password" required></div>
          <button class="btn btn-primary" type="submit"><span class="loader"></span>Entrar</button>
        </form>

        <p class="subtitle" style="font-size:1rem; margin:16px 0 0;">
          ¿No tienes cuenta? <a href="{{ url_for('register') }}"><strong>Créala aquí</strong></a><br>
          <a href="{{ url_for('forgot_password') }}"><strong>¿Olvidaste tu contraseña?</strong></a>
        </p>
      </div>
    </div>
    """
    return render_page(content, title="Iniciar sesión", user=None, hide_container=True)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        note = request.form.get("note", "").strip()

        if not email:
            flash("Escribe tu correo.", "error")
        else:
            conn = get_db()
            q(conn, """
                INSERT INTO password_resets (email, note, status, created_at)
                VALUES (?, ?, 'Pendiente', ?)
            """, (email, note, now_str()))
            conn.commit()
            conn.close()
            flash("Solicitud enviada. Revisaremos tu caso.", "success")
            return redirect(url_for("login"))

    content = """
    <div class="auth-shell">
      <div class="card auth-card">
        <h2>Recuperar contraseña</h2>
        <p class="subtitle" style="font-size:1rem; margin-bottom:16px;">
          Escribe tu correo y una nota opcional para que podamos ayudarte.
        </p>

        <form method="post">
          <div><label>Correo electrónico</label><input type="email" name="email" required></div>
          <div><label>Nota opcional</label><textarea name="note" placeholder="Ej: olvidé mi contraseña"></textarea></div>
          <button class="btn btn-primary" type="submit">Enviar solicitud</button>
        </form>
      </div>
    </div>
    """
    return render_page(content, title="Recuperar contraseña", user=None, hide_container=True)


@app.route("/logout")
def logout():
    user = current_user()
    if user:
        log_action(user["id"], "user_logout", "Cierre de sesión")
    session.clear()
    flash("Has cerrado sesión.", "info")
    return redirect(url_for("home"))

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        city = request.form.get("city", "").strip()
        new_tag = request.form.get("profile_tag", "").strip()
        photo = request.files.get("profile_photo")
        photo_path = user["profile_photo"]

        if new_tag and not new_tag.startswith("@"):
            new_tag = "@" + new_tag

        new_tag = new_tag.lower().strip()
        new_tag = "@" + "".join(ch for ch in new_tag.replace("@", "") if ch.isalnum() or ch in "._")

        if not first_name or not last_name or city not in CITIES_CUBA or not new_tag or new_tag == "@":
            flash("Completa los datos del perfil correctamente.", "error")
        else:
            conn = get_db()
            tag_exists = q(conn, "SELECT id FROM users WHERE profile_tag = ? AND id != ?", (new_tag, user["id"])).fetchone()

            if tag_exists:
                conn.close()
                flash("Ese @tag ya está en uso.", "error")
                return redirect(url_for("profile"))

            if photo and photo.filename:
                safe_name = secure_filename(photo.filename)
                ext = os.path.splitext(safe_name)[1].lower()

                if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
                    conn.close()
                    flash("La foto debe ser JPG, PNG o WEBP.", "error")
                    return redirect(url_for("profile"))

                final_name = f"avatar_{uuid.uuid4().hex}{ext}"
                final_path = UPLOAD_DIR / final_name
                photo.save(final_path)
                photo_path = str(final_path)

            q(conn, """
                UPDATE users
                SET first_name = ?, last_name = ?, city = ?, profile_tag = ?, profile_photo = ?
                WHERE id = ?
            """, (first_name, last_name, city, new_tag, photo_path, user["id"]))
            conn.commit()
            conn.close()

            log_action(user["id"], "profile_updated", "Perfil actualizado")
            flash("Perfil actualizado correctamente.", "success")
            return redirect(url_for("profile"))

    wallet = get_wallet(user["id"])
    profile_photo_url = url_for("uploaded_file", filename=os.path.basename(user["profile_photo"])) if user["profile_photo"] else None
    settings = get_settings()
    referral_reward = parse_float(settings.get("referral_reward_usdt", "0.50"), 0.50)

    content = """
    <div class="page-wrap">
      <div class="container" style="max-width:980px;">
        <div class="profile-hero card">
          <div class="avatar">
            {% if profile_photo_url %}
              <img src="{{ profile_photo_url }}" alt="Foto">
            {% else %}
              {{ user['first_name'][0] }}{{ user['last_name'][0] }}
            {% endif %}
          </div>

          <div>
            <h2 style="margin-bottom:6px;">{{ user['first_name'] }} {{ user['last_name'] }}</h2>
            <div class="tag-pill">{{ user['profile_tag'] }}</div>
            <p class="subtitle" style="font-size:1rem; margin-top:14px; margin-bottom:0;">
              {{ user['email'] }} · {{ user['city'] }}
            </p>
            <p class="subtitle" style="font-size:0.95rem; margin-top:10px; margin-bottom:0;">
              Tu código de referido: <strong>{{ user['referral_code'] }}</strong>
            </p>
          </div>

          <a class="btn btn-secondary" href="{{ url_for('wallet_page') }}">Ver billetera</a>
        </div>

        <div class="grid-2">
          <div class="card panel">
            <h3>Editar perfil</h3>
            <form method="post" enctype="multipart/form-data">
              <div><label>Nombre</label><input type="text" name="first_name" value="{{ user['first_name'] }}" required></div>
              <div><label>Apellidos</label><input type="text" name="last_name" value="{{ user['last_name'] }}" required></div>
              <div><label>@tag</label><input type="text" name="profile_tag" value="{{ user['profile_tag'] }}" required></div>

              <div>
                <label>Ciudad</label>
                <select name="city" required>
                  {% for city in cities %}
                    <option value="{{ city }}" {% if user['city'] == city %}selected{% endif %}>{{ city }}</option>
                  {% endfor %}
                </select>
              </div>

              <div><label>Subir foto</label><input type="file" name="profile_photo"></div>
              <button class="btn btn-primary" type="submit">Guardar cambios</button>
            </form>
          </div>

          <div class="card panel">
            <h3>Resumen rápido</h3>
            <div class="wallet-grid">
              <div class="card wallet-card">
                <div class="wallet-label">CUP</div>
                <div class="wallet-amount">{{ '%.2f'|format(wallet['cup_balance']) }}</div>
              </div>
              <div class="card wallet-card">
                <div class="wallet-label">USD</div>
                <div class="wallet-amount">{{ '%.2f'|format(wallet['usd_balance']) }}</div>
              </div>
              <div class="card wallet-card">
                <div class="wallet-label">USDT</div>
                <div class="wallet-amount">{{ '%.2f'|format(wallet['usdt_balance']) }}</div>
              </div>
            </div>

            <p class="subtitle" style="font-size:1rem; margin-top:18px; margin-bottom:0;">
              Gana {{ '%.2f'|format(referral_reward) }} USDT por cada referido válido que se registre con tu código.
            </p>
          </div>
        </div>
      </div>
    </div>
    """
    return render_page(
        content,
        title="Mi perfil",
        user=user,
        cities=CITIES_CUBA,
        wallet=wallet,
        profile_photo_url=profile_photo_url,
        referral_reward=referral_reward,
    )


@app.route("/wallet")
@login_required
def wallet_page():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    wallet = get_wallet(user["id"])

    conn = get_db()
    txs = q(conn, """
        SELECT * FROM wallet_transactions
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 30
    """, (user["id"],)).fetchall()

    transfers = q(conn, """
        SELECT
            t.*,
            u1.profile_tag AS sender_tag,
            u2.profile_tag AS receiver_tag
        FROM transfers t
        JOIN users u1 ON t.sender_user_id = u1.id
        JOIN users u2 ON t.receiver_user_id = u2.id
        WHERE t.sender_user_id = ? OR t.receiver_user_id = ?
        ORDER BY t.id DESC
        LIMIT 20
    """, (user["id"], user["id"])).fetchall()
    conn.close()

    content = """
    <div class="page-wrap">
      <div class="container">
        <div class="top-row">
          <div>
            <h2>Mi billetera</h2>
            <p class="subtitle" style="font-size:1rem; margin-bottom:0;">
              Gestiona tus balances y revisa tus movimientos.
            </p>
          </div>
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <a class="btn btn-secondary" href="{{ url_for('transfer_money') }}">Enviar dinero</a>
            <a class="btn btn-primary" href="{{ url_for('new_order') }}">Comprar</a>
          </div>
        </div>

        <div class="wallet-grid" style="margin-bottom:22px;">
          <div class="card wallet-card">
            <div class="wallet-label">Saldo CUP</div>
            <div class="wallet-amount">{{ '%.2f'|format(wallet['cup_balance']) }}</div>
          </div>
          <div class="card wallet-card">
            <div class="wallet-label">Saldo USD</div>
            <div class="wallet-amount">{{ '%.2f'|format(wallet['usd_balance']) }}</div>
          </div>
          <div class="card wallet-card">
            <div class="wallet-label">Saldo USDT</div>
            <div class="wallet-amount">{{ '%.2f'|format(wallet['usdt_balance']) }}</div>
          </div>
        </div>

        <div class="grid-2">
          <div class="card panel">
            <h3 style="margin-bottom:14px;">Movimientos</h3>
            {% if txs %}
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Moneda</th>
                  <th>Monto</th>
                  <th>Dirección</th>
                  <th>Tipo</th>
                  <th>Descripción</th>
                  <th>Fecha</th>
                </tr>
              </thead>
              <tbody>
                {% for tx in txs %}
                <tr>
                  <td data-label="ID">#{{ tx['id'] }}</td>
                  <td data-label="Moneda">{{ tx['currency'] }}</td>
                  <td data-label="Monto">{{ tx['amount'] }}</td>
                  <td data-label="Dirección">{{ tx['direction'] }}</td>
                  <td data-label="Tipo">{{ tx['tx_type'] }}</td>
                  <td data-label="Descripción">{{ tx['description'] }}</td>
                  <td data-label="Fecha">{{ tx['created_at'] }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
            {% else %}
              <div class="empty">No hay movimientos todavía.</div>
            {% endif %}
          </div>

          <div class="card panel">
            <h3 style="margin-bottom:14px;">Transferencias</h3>
            {% if transfers %}
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Desde</th>
                  <th>Hacia</th>
                  <th>Moneda</th>
                  <th>Monto</th>
                  <th>Fecha</th>
                </tr>
              </thead>
              <tbody>
                {% for tx in transfers %}
                <tr>
                  <td data-label="ID">#{{ tx['id'] }}</td>
                  <td data-label="Desde">{{ tx['sender_tag'] }}</td>
                  <td data-label="Hacia">{{ tx['receiver_tag'] }}</td>
                  <td data-label="Moneda">{{ tx['currency'] }}</td>
                  <td data-label="Monto">{{ tx['amount'] }}</td>
                  <td data-label="Fecha">{{ tx['created_at'] }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
            {% else %}
              <div class="empty">No hay transferencias todavía.</div>
            {% endif %}
          </div>
        </div>
      </div>
    </div>
    """
    return render_page(
        content,
        title="Mi billetera",
        user=user,
        wallet=wallet,
        txs=txs,
        transfers=transfers,
    )


@app.route("/transfer", methods=["GET", "POST"])
@login_required
def transfer_money():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    wallet = get_wallet(user["id"])

    if request.method == "POST":
        receiver_tag = request.form.get("receiver_tag", "").strip().lower()
        currency = request.form.get("currency", "").strip().upper()
        amount = parse_float(request.form.get("amount", "0"), 0)
        description = request.form.get("description", "").strip()

        if receiver_tag and not receiver_tag.startswith("@"):
            receiver_tag = "@" + receiver_tag

        if currency not in {"CUP", "USD", "USDT"} or amount <= 0 or not receiver_tag:
            flash("Completa correctamente los datos de la transferencia.", "error")
        elif receiver_tag == user["profile_tag"]:
            flash("No puedes enviarte dinero a ti mismo.", "error")
        else:
            conn = get_db()
            receiver = q(conn, "SELECT id, profile_tag FROM users WHERE profile_tag = ?", (receiver_tag,)).fetchone()

            if not receiver:
                conn.close()
                flash("No encontramos ese @tag.", "error")
            else:
                field = {"CUP": "cup_balance", "USD": "usd_balance", "USDT": "usdt_balance"}[currency]
                sender_wallet = q(conn, "SELECT * FROM wallets WHERE user_id = ?", (user["id"],)).fetchone()

                if float(sender_wallet[field]) < float(amount):
                    conn.close()
                    flash("Saldo insuficiente.", "error")
                else:
                    q(conn, f"UPDATE wallets SET {field} = {field} - ? WHERE user_id = ?", (amount, user["id"]))
                    q(conn, f"UPDATE wallets SET {field} = {field} + ? WHERE user_id = ?", (amount, receiver["id"]))

                    q(conn, """
                        INSERT INTO wallet_transactions
                        (user_id, currency, amount, direction, tx_type, description, reference, created_at)
                        VALUES (?, ?, ?, 'debit', 'transfer_out', ?, ?, ?)
                    """, (
                        user["id"],
                        currency,
                        amount,
                        description or f"Transferencia a {receiver_tag}",
                        receiver_tag,
                        now_str(),
                    ))

                    q(conn, """
                        INSERT INTO wallet_transactions
                        (user_id, currency, amount, direction, tx_type, description, reference, created_at)
                        VALUES (?, ?, ?, 'credit', 'transfer_in', ?, ?, ?)
                    """, (
                        receiver["id"],
                        currency,
                        amount,
                        description or f"Transferencia recibida de {user['profile_tag']}",
                        user["profile_tag"],
                        now_str(),
                    ))

                    q(conn, """
                        INSERT INTO transfers
                        (sender_user_id, receiver_user_id, currency, amount, status, created_at)
                        VALUES (?, ?, ?, ?, 'Completado', ?)
                    """, (
                        user["id"],
                        receiver["id"],
                        currency,
                        amount,
                        now_str(),
                    ))

                    conn.commit()
                    conn.close()

                    log_action(user["id"], "transfer_sent", f"{amount} {currency} a {receiver_tag}")
                    flash("Transferencia realizada correctamente.", "success")
                    return redirect(url_for("wallet_page"))

    content = """
    <div class="page-wrap">
      <div class="container" style="max-width:760px;">
        <div class="card form-card">
          <div class="top-row" style="margin-bottom:10px;">
            <div>
              <h2>Enviar dinero</h2>
              <p class="subtitle" style="font-size:1rem; margin-bottom:0;">
                Envía CUP, USD o USDT a otro usuario usando su @tag.
              </p>
            </div>
            <a class="btn btn-secondary" href="{{ url_for('wallet_page') }}">Volver a billetera</a>
          </div>

          <div class="wallet-grid" style="margin-bottom:20px;">
            <div class="card wallet-card">
              <div class="wallet-label">CUP</div>
              <div class="wallet-amount">{{ '%.2f'|format(wallet['cup_balance']) }}</div>
            </div>
            <div class="card wallet-card">
              <div class="wallet-label">USD</div>
              <div class="wallet-amount">{{ '%.2f'|format(wallet['usd_balance']) }}</div>
            </div>
            <div class="card wallet-card">
              <div class="wallet-label">USDT</div>
              <div class="wallet-amount">{{ '%.2f'|format(wallet['usdt_balance']) }}</div>
            </div>
          </div>

          <form method="post">
            <div><label>@tag destino</label><input type="text" name="receiver_tag" placeholder="@usuario" required></div>

            <div>
              <label>Moneda</label>
              <select name="currency" required>
                <option value="CUP">CUP</option>
                <option value="USD">USD</option>
                <option value="USDT">USDT</option>
              </select>
            </div>

            <div><label>Monto</label><input type="text" name="amount" placeholder="Ej: 10" required></div>
            <div><label>Descripción opcional</label><input type="text" name="description" placeholder="Ej: pago, ayuda, recarga"></div>

            <button class="btn btn-primary" type="submit">Enviar dinero</button>
          </form>
        </div>
      </div>
    </div>
    """
    return render_page(content, title="Enviar dinero", user=user, wallet=wallet)

@app.route("/new-order", methods=["GET","POST"])
@login_required
def new_order():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    service = request.args.get("service","Recargas")
    promo = get_active_promo()
    settings = get_settings()

    if request.method == "POST":

        service = request.form.get("service")
        plan = request.form.get("plan_name","")
        phone = request.form.get("phone_number","")
        wallet_address = request.form.get("wallet_address","")
        gift_brand = request.form.get("gift_brand","")
        gift_value = request.form.get("gift_value","")
        payment_method = request.form.get("payment_method","externo")

        total_cup = 0
        extra_data = ""

        if service == "Recargas":

            total_cup = recharge_price(plan,promo)
            extra_data = phone

        elif service == "Gift Cards":

            total_cup = gift_card_price_cup(float(gift_value),settings)
            extra_data = f"{gift_brand} {gift_value}$"

        elif service == "Cripto":

            cup_amount = parse_float(request.form.get("cup_amount","0"),0)
            total_cup = cup_amount
            extra_data = f"{request.form.get('network')} -> {wallet_address}"

        conn = get_db()

        if payment_method == "wallet":

            wallet = get_wallet(user["id"])

            if wallet["cup_balance"] < total_cup:
                conn.close()
                flash("Saldo insuficiente en CUP.","error")
                return redirect(url_for("wallet_page"))

            q(conn,"UPDATE wallets SET cup_balance = cup_balance - ? WHERE user_id = ?",(total_cup,user["id"]))

            add_wallet_tx(
                user["id"],
                "CUP",
                total_cup,
                "debit",
                "purchase",
                f"Compra {service}"
            )

            payment_status = "Pagado"

        else:
            payment_status = "Pago en revisión"

        q(conn,"""
        INSERT INTO orders
        (user_id,customer_name,phone_number,service,plan_name,extra_data,total_cup,payment_method,status,payment_status,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,(
            user["id"],
            f"{user['first_name']} {user['last_name']}",
            phone,
            service,
            plan,
            extra_data,
            total_cup,
            payment_method,
            "Pendiente",
            payment_status,
            now_str()
        ))

        conn.commit()
        order_id = q(conn,"SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        log_action(user["id"],"order_created",f"Order {order_id}")

        if payment_method == "wallet":

            flash("Pedido pagado correctamente con saldo.","success")
            return redirect(url_for("my_orders"))

        return redirect(url_for("checkout",order_id=order_id))

    content = """
<div class="page-wrap">
<div class="container" style="max-width:800px;">
<div class="card form-card">

<h2>Nuevo pedido</h2>

<form method="post">

<input type="hidden" name="service" value="{{service}}">

<div>
<label>Producto</label>
<select name="service">

<option value="Recargas">Recargas</option>
<option value="Cripto">Cripto</option>
<option value="Gift Cards">Gift Cards</option>

</select>
</div>

<div>
<label>Plan / opción</label>
<select name="plan_name">
{% for p in plans %}
<option value="{{p}}">{{p}}</option>
{% endfor %}
</select>
</div>

<div>
<label>Número Cubacel</label>
<input name="phone_number">
</div>

<div>
<label>Método de pago</label>
<select name="payment_method">
<option value="externo">Transferencia</option>
<option value="wallet">Saldo billetera</option>
</select>
</div>

<button class="btn btn-primary">Continuar</button>

</form>

</div>
</div>
</div>
"""

    plans = [p["label"] for p in RECHARGE_OPTIONS]

    return render_page(
        content,
        title="Nuevo pedido",
        user=user,
        service=service,
        plans=plans
    )


@app.route("/checkout/<int:order_id>",methods=["GET","POST"])
@login_required
def checkout(order_id):

    user = current_user()

    conn = get_db()
    order = q(conn,"SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    settings = get_settings()

    if not order:
        conn.close()
        abort(404)

    if request.method == "POST":

        proof = request.files.get("proof")

        if proof and proof.filename:

            safe = secure_filename(proof.filename)
            name = f"proof_{uuid.uuid4().hex}_{safe}"
            path = UPLOAD_DIR / name

            proof.save(path)

            q(conn,"UPDATE orders SET proof_path=?,payment_status='Pago enviado' WHERE id=?",(str(path),order_id))
            conn.commit()

        conn.close()

        flash("Comprobante enviado. Revisaremos el pago.","success")

        return redirect(url_for("my_orders"))

    card_label = settings.get("payment_card_label")
    card_number = settings.get("payment_card_number")
    card_holder = settings.get("payment_card_holder")

    content = """

<div class="page-wrap">
<div class="container" style="max-width:760px;">

<div class="card form-card">

<h2>Pago del pedido</h2>

<p><strong>Total:</strong> {{order['total_cup']}} CUP</p>

<div class="promo-box">

<p>Envía el pago a:</p>

<strong>{{card_label}}</strong><br>
{{card_number}}<br>
{{card_holder}}

</div>

<form method="post" enctype="multipart/form-data">

<label>Subir comprobante</label>
<input type="file" name="proof" required>

<button class="btn btn-primary">Enviar comprobante</button>

</form>

</div>
</div>
</div>

"""

    return render_page(content,title="Pago",user=user,order=order)


@app.route("/my-orders")
@login_required
def my_orders():

    user = current_user()

    conn = get_db()

    orders = q(conn,"SELECT * FROM orders WHERE user_id=? ORDER BY id DESC",(user["id"],)).fetchall()

    conn.close()

    content = """

<div class="page-wrap">
<div class="container">

<h2>Mis pedidos</h2>

{% if orders %}

<table>

<thead>
<tr>
<th>ID</th>
<th>Producto</th>
<th>Total</th>
<th>Estado</th>
<th>Pago</th>
<th>Fecha</th>
</tr>
</thead>

<tbody>

{% for o in orders %}

<tr>

<td>#{{o['id']}}</td>

<td>{{o['service']}}</td>

<td>{{o['total_cup']}} CUP</td>

<td>{{o['status']}}</td>

<td>{{o['payment_status']}}</td>

<td>{{o['created_at']}}</td>

</tr>

{% endfor %}

</tbody>
</table>

{% else %}

<div class="empty">Aún no tienes pedidos.</div>

{% endif %}

</div>
</div>

"""

    return render_page(content,title="Mis pedidos",user=user,orders=orders)

@app.route("/admin")
@admin_required
def admin_dashboard():

    user = current_user()
    conn = get_db()

    orders_today = q(conn,"SELECT COUNT(*) as c FROM orders WHERE date(created_at)=date('now')").fetchone()["c"]
    users_total = q(conn,"SELECT COUNT(*) as c FROM users").fetchone()["c"]
    pending_orders = q(conn,"SELECT COUNT(*) as c FROM orders WHERE status='Pendiente'").fetchone()["c"]
    pending_payments = q(conn,"SELECT COUNT(*) as c FROM orders WHERE payment_status!='Pagado'").fetchone()["c"]

    conn.close()

    content = """

<div class="page-wrap">
<div class="container">

<h2>Dashboard Admin</h2>

<div class="stats">

<div class="card stat">
<div class="label">Pedidos hoy</div>
<div class="value">{{orders_today}}</div>
</div>

<div class="card stat">
<div class="label">Usuarios</div>
<div class="value">{{users_total}}</div>
</div>

<div class="card stat">
<div class="label">Pedidos pendientes</div>
<div class="value">{{pending_orders}}</div>
</div>

<div class="card stat">
<div class="label">Pagos por revisar</div>
<div class="value">{{pending_payments}}</div>
</div>

</div>

<div class="services-scroll">

<a class="service-item" href="{{url_for('admin_orders')}}">
<div class="icon">📦</div>
<span>Pedidos</span>
</a>

<a class="service-item" href="{{url_for('admin_users')}}">
<div class="icon">👥</div>
<span>Usuarios</span>
</a>

<a class="service-item" href="{{url_for('admin_promos')}}">
<div class="icon">🔥</div>
<span>Promociones</span>
</a>

<a class="service-item" href="{{url_for('admin_resets')}}">
<div class="icon">🔑</div>
<span>Recuperaciones</span>
</a>

</div>

</div>
</div>

"""

    return render_page(
        content,
        title="Admin",
        user=user,
        orders_today=orders_today,
        users_total=users_total,
        pending_orders=pending_orders,
        pending_payments=pending_payments
    )


@app.route("/admin/orders")
@admin_required
def admin_orders():

    conn = get_db()

    orders = q(conn,"SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()

    conn.close()

    content = """

<div class="page-wrap">
<div class="container">

<h2>Pedidos</h2>

<table>

<thead>
<tr>
<th>ID</th>
<th>Usuario</th>
<th>Producto</th>
<th>Total</th>
<th>Estado</th>
<th>Pago</th>
<th>Acciones</th>
</tr>
</thead>

<tbody>

{% for o in orders %}

<tr>

<td>#{{o['id']}}</td>

<td>{{o['customer_name']}}</td>

<td>{{o['service']}}</td>

<td>{{o['total_cup']}}</td>

<td>{{o['status']}}</td>

<td>{{o['payment_status']}}</td>

<td>

<a class="btn btn-secondary" href="{{url_for('admin_update_order',order_id=o['id'],status='Procesando')}}">Procesar</a>

<a class="btn btn-primary" href="{{url_for('admin_update_order',order_id=o['id'],status='Completado')}}">Completar</a>

</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>
</div>

"""

    return render_page(content,title="Pedidos",user=current_user(),orders=orders)


@app.route("/admin/order/<int:order_id>/<status>")
@admin_required
def admin_update_order(order_id,status):

    conn = get_db()

    q(conn,"UPDATE orders SET status=? WHERE id=?",(status,order_id))

    conn.commit()
    conn.close()

    flash("Estado actualizado.","success")

    return redirect(url_for("admin_orders"))


@app.route("/admin/users")
@admin_required
def admin_users():

    conn = get_db()

    users = q(conn,"SELECT * FROM users ORDER BY id DESC").fetchall()

    conn.close()

    content = """

<div class="page-wrap">
<div class="container">

<h2>Usuarios</h2>

<table>

<thead>
<tr>
<th>ID</th>
<th>Nombre</th>
<th>Email</th>
<th>Tag</th>
<th>Acción</th>
</tr>
</thead>

<tbody>

{% for u in users %}

<tr>

<td>{{u['id']}}</td>

<td>{{u['first_name']}} {{u['last_name']}}</td>

<td>{{u['email']}}</td>

<td>{{u['profile_tag']}}</td>

<td>

<a class="btn btn-secondary" href="{{url_for('admin_wallet',user_id=u['id'])}}">Billetera</a>

</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>
</div>

"""

    return render_page(content,title="Usuarios",user=current_user(),users=users)


@app.route("/admin/wallet/<int:user_id>",methods=["GET","POST"])
@admin_required
def admin_wallet(user_id):

    wallet = get_wallet(user_id)

    if request.method == "POST":

        currency = request.form.get("currency")
        amount = parse_float(request.form.get("amount"),0)
        direction = request.form.get("direction")

        adjust_wallet(
            user_id,
            currency,
            amount,
            "Ajuste admin",
            direction
        )

        flash("Saldo actualizado.","success")
        return redirect(url_for("admin_wallet",user_id=user_id))

    content = """

<div class="page-wrap">
<div class="container" style="max-width:700px;">

<div class="card panel">

<h2>Editar billetera</h2>

<div class="wallet-grid">

<div class="card wallet-card">
<div class="wallet-label">CUP</div>
<div class="wallet-amount">{{wallet['cup_balance']}}</div>
</div>

<div class="card wallet-card">
<div class="wallet-label">USD</div>
<div class="wallet-amount">{{wallet['usd_balance']}}</div>
</div>

<div class="card wallet-card">
<div class="wallet-label">USDT</div>
<div class="wallet-amount">{{wallet['usdt_balance']}}</div>
</div>

</div>

<form method="post">

<div>
<label>Moneda</label>
<select name="currency">
<option>CUP</option>
<option>USD</option>
<option>USDT</option>
</select>
</div>

<div>
<label>Monto</label>
<input name="amount">
</div>

<div>
<label>Tipo</label>
<select name="direction">
<option value="credit">Agregar</option>
<option value="debit">Restar</option>
</select>
</div>

<button class="btn btn-primary">Actualizar saldo</button>

</form>

</div>
</div>
</div>

"""

    return render_page(content,title="Wallet admin",user=current_user(),wallet=wallet)


@app.route("/admin/promos",methods=["GET","POST"])
@admin_required
def admin_promos():

    conn = get_db()

    if request.method == "POST":

        title = request.form.get("title")
        price = request.form.get("price_text")
        price_cup = parse_float(request.form.get("price_cup"),0)
        desc = request.form.get("description")
        b1 = request.form.get("b1")
        b2 = request.form.get("b2")
        b3 = request.form.get("b3")

        q(conn,"UPDATE promotions SET is_active=0")

        q(conn,"""
        INSERT INTO promotions
        (title,price_text,price_cup,description,bonus_1,bonus_2,bonus_3,is_active,created_at)
        VALUES (?,?,?,?,?,?,?,1,?)
        """,(
            title,
            price,
            price_cup,
            desc,
            b1,
            b2,
            b3,
            now_str()
        ))

        conn.commit()

    promos = q(conn,"SELECT * FROM promotions ORDER BY id DESC").fetchall()

    conn.close()

    content = """

<div class="page-wrap">
<div class="container">

<h2>Promociones</h2>

<form method="post" class="card panel">

<input name="title" placeholder="Título">
<input name="price_text" placeholder="Precio visible">
<input name="price_cup" placeholder="Precio CUP">
<input name="description" placeholder="Descripción">
<input name="b1" placeholder="Bonus 1">
<input name="b2" placeholder="Bonus 2">
<input name="b3" placeholder="Bonus 3">

<button class="btn btn-primary">Crear promoción</button>

</form>

</div>
</div>

"""

    return render_page(content,title="Promociones",user=current_user(),promos=promos)


@app.route("/admin/resets")
@admin_required
def admin_resets():

    conn = get_db()

    resets = q(conn,"SELECT * FROM password_resets ORDER BY id DESC").fetchall()

    conn.close()

    content = """

<div class="page-wrap">
<div class="container">

<h2>Solicitudes recuperación</h2>

<table>

<thead>
<tr>
<th>Email</th>
<th>Nota</th>
<th>Estado</th>
</tr>
</thead>

<tbody>

{% for r in resets %}

<tr>

<td>{{r['email']}}</td>
<td>{{r['note']}}</td>
<td>{{r['status']}}</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>
</div>

"""

    return render_page(content,title="Recuperaciones",user=current_user(),resets=resets)

