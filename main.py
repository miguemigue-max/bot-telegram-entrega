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
DB_PATH = BASE_DIR / "recargas.db"
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
    {"label": "Recarga 1250 CUP", "price_cup": 14500}
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


def init_db():
    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT '',
            profile_tag TEXT NOT NULL UNIQUE,
            profile_photo TEXT NOT NULL DEFAULT '',
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_locked INTEGER NOT NULL DEFAULT 0,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            last_login_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            service TEXT NOT NULL DEFAULT 'Recargas',
            plan_name TEXT NOT NULL DEFAULT '',
            extra_data TEXT NOT NULL DEFAULT '',
            total_cup REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Pendiente',
            payment_status TEXT NOT NULL DEFAULT 'Pago en revisión',
            proof_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.execute(
        """
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
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Pendiente',
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER PRIMARY KEY,
            cup_balance REAL NOT NULL DEFAULT 0,
            usd_balance REAL NOT NULL DEFAULT 0,
            usdt_balance REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency TEXT NOT NULL,
            amount REAL NOT NULL,
            direction TEXT NOT NULL,
            tx_type TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER NOT NULL DEFAULT 0,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    defaults = {
        "usd_cup": "510",
        "usdt_buy_cup": "580",
        "btc_usd": "85000",
        "giftcard_markup_percent": "10",
        "payment_card_label": "Tarjeta de pago",
        "payment_card_number": "9224 xxxx xxxx xxxx",
        "payment_card_holder": "Nombre de tu mamá",
        "payment_note": "Envía el importe exacto y sube el comprobante.",
    }
    for key, value in defaults.items():
        existing = conn.execute("SELECT key FROM settings WHERE key = ?", (key,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    admin_email = "admin@recargas.local"
    existing_admin = conn.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()
    if not existing_admin:
        conn.execute(
            """
            INSERT INTO users (
                first_name, last_name, email, password, city, profile_tag,
                profile_photo, is_admin, is_locked, failed_attempts, last_login_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', 1, 0, 0, '', ?)
            """,
            (
                "Administrador",
                "General",
                admin_email,
                generate_password_hash("admin123"),
                "La Habana",
                "@admin100",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        admin_user = conn.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO wallets (user_id, cup_balance, usd_balance, usdt_balance, created_at) VALUES (?, 0, 0, 0, ?)",
            (admin_user["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )

    promo_exists = conn.execute("SELECT id FROM promotions LIMIT 1").fetchone()
    if not promo_exists:
        conn.execute(
            """
            INSERT INTO promotions
            (title, price_text, price_cup, description, bonus_1, bonus_2, bonus_3, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PROMOCIÓN DEL 10 de marzo AL 15 de marzo",
                "14 500 CUP",
                14500,
                "Recarga promocional disponible para clientes en Cuba durante las fechas activas.",
                "25GB de navegación válidos para todas las redes.",
                "Datos ilimitados desde las 12:00 a.m. hasta las 7:00 a.m.",
                "Aplica a recargas entre 600 CUP y 1250 CUP.",
                1,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    conn.commit()
    conn.close()


init_db()


def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def get_active_promo():
    conn = get_db()
    promo = conn.execute("SELECT * FROM promotions WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    if not promo:
        promo = conn.execute("SELECT * FROM promotions ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return promo


def get_wallet(user_id):
    conn = get_db()
    wallet = conn.execute("SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return wallet


def ensure_wallet(user_id):
    conn = get_db()
    existing = conn.execute("SELECT user_id FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO wallets (user_id, cup_balance, usd_balance, usdt_balance, created_at) VALUES (?, 0, 0, 0, ?)",
            (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    conn.close()


def adjust_wallet(user_id, currency, amount, description, direction):
    ensure_wallet(user_id)
    conn = get_db()
    field_map = {"CUP": "cup_balance", "USD": "usd_balance", "USDT": "usdt_balance"}
    field = field_map[currency]
    sign = 1 if direction == "credit" else -1
    conn.execute(f"UPDATE wallets SET {field} = {field} + ? WHERE user_id = ?", (sign * amount, user_id))
    conn.execute(
        "INSERT INTO wallet_transactions (user_id, currency, amount, direction, tx_type, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, currency, amount, direction, "admin_adjustment", description, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def log_action(actor_user_id, action, details=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_logs (actor_user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
        (actor_user_id, action, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


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


def parse_float(value, default=0.0):
    try:
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return default


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
    width, height = A4
    y = height - 60
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, y, "Resumen de compra")
    y -= 30
    pdf.setFont("Helvetica", 11)
    lines = [
        f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
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


def make_tag(first_name, last_name):
    base = f"{first_name}.{last_name}".lower().replace(" ", "")
    base = "".join(ch for ch in base if ch.isalnum() or ch == ".")
    if not base:
        base = "usuario"
    return f"@{base}{secrets.randbelow(900)+100}"


BASE_HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root {
      --bg: #0f0618;
      --bg-2: #190826;
      --card: rgba(29, 9, 46, 0.92);
      --text: #f7f3ff;
      --muted: #cbb7ea;
      --border: rgba(255,255,255,0.08);
      --shadow: 0 18px 38px rgba(0,0,0,0.28);
      --accent: #8a05be;
      --accent-2: #b517ff;
      --accent-soft: rgba(181,23,255,0.16);
      --danger: #ff7a8a;
      --success-bg: rgba(75, 211, 125, 0.14);
      --success-border: rgba(75, 211, 125, 0.24);
      --error-bg: rgba(255, 122, 138, 0.14);
      --error-border: rgba(255, 122, 138, 0.22);
      --info-bg: rgba(181, 23, 255, 0.16);
      --info-border: rgba(181, 23, 255, 0.22);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Arial, Helvetica, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, rgba(181,23,255,0.18), transparent 28%),
        radial-gradient(circle at top left, rgba(138,5,190,0.18), transparent 22%),
        linear-gradient(180deg, var(--bg-2) 0%, var(--bg) 100%);
      min-height: 100vh;
    }

    a { color: inherit; text-decoration: none; }
    .container { width: min(1120px, 92%); margin: 0 auto; }

    .nav {
      position: sticky;
      top: 0;
      z-index: 50;
      backdrop-filter: blur(18px);
      background: rgba(16, 6, 24, 0.78);
      border-bottom: 1px solid var(--border);
    }

    .nav-inner {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      padding: 14px 0;
    }

    .brand {
      font-weight: 800;
      font-size: 1.05rem;
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .brand-mark {
      width: 28px;
      height: 28px;
      border-radius: 10px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white;
      font-size: 0.95rem;
      box-shadow: var(--shadow);
    }

    .nav-links { display: flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap; }
    .btn {
      display: inline-flex; align-items: center; justify-content: center; border-radius: 16px; padding: 12px 18px;
      font-weight: 700; border: 1px solid transparent; cursor: pointer; transition: 0.22s ease; text-align: center;
    }
    .btn:hover { transform: translateY(-2px); }
    .btn-primary { background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: white; box-shadow: var(--shadow); }
    .btn-secondary { background: rgba(255,255,255,0.06); border-color: var(--border); color: var(--text); }
    .btn-danger { background: rgba(255,122,138,0.08); border-color: rgba(255,122,138,0.18); color: var(--danger); }
    .btn-buy { margin-top: 14px; width: 100%; background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: white; }

    .icon-btn {
      width: 44px; height: 44px; border-radius: 14px; border: 1px solid var(--border);
      background: rgba(255,255,255,0.06); display: inline-flex; align-items: center; justify-content: center;
      font-size: 1.1rem; cursor: pointer; box-shadow: var(--shadow); color: var(--text);
    }

    .menu-wrap { position: relative; }
    .menu-dropdown {
      position: absolute; top: calc(100% + 10px); right: 0; min-width: 230px;
      background: rgba(26, 9, 41, 0.98); border: 1px solid var(--border); border-radius: 18px;
      box-shadow: 0 20px 40px rgba(0,0,0,0.34); padding: 10px; display: none; z-index: 80;
    }
    .menu-wrap:hover .menu-dropdown, .menu-wrap:focus-within .menu-dropdown { display: block; }
    .menu-item { display: block; padding: 12px 14px; border-radius: 12px; font-weight: 700; color: var(--text); }
    .menu-item:hover { background: rgba(255,255,255,0.06); }
    .menu-item-danger { color: var(--danger); }

    .hero { padding: 66px 0 34px; }
    .hero-grid { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 28px; align-items: center; }
    .badge {
      display: inline-block; padding: 8px 13px; border-radius: 999px; background: var(--accent-soft);
      border: 1px solid rgba(181,23,255,0.20); color: #f0d3ff; font-size: 0.88rem; margin-bottom: 16px; font-weight: 700;
    }
    h1 { margin: 0 0 14px; font-size: clamp(2.4rem, 5vw, 4.7rem); line-height: 1.02; letter-spacing: -0.03em; }
    h2 { margin: 0 0 12px; font-size: 1.9rem; }
    h3 { margin: 0 0 8px; }
    .subtitle { color: var(--muted); font-size: 1.08rem; line-height: 1.75; max-width: 58ch; margin-bottom: 24px; }
    .hero-actions { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; }

    .card {
      background: linear-gradient(180deg, rgba(40,12,62,0.94), rgba(23,8,36,0.96));
      border: 1px solid var(--border);
      border-radius: 28px;
      box-shadow: var(--shadow);
      animation: fadeUp 0.5s ease both;
    }

    .price-card { padding: 28px; }
    .price-kicker { color: #e5b4ff; font-weight: 700; font-size: 0.92rem; margin-bottom: 8px; }
    .price { font-size: clamp(2rem, 4vw, 3.4rem); font-weight: 800; margin: 8px 0 16px; line-height: 1; }
    .promo-box { margin-top: 18px; padding: 18px; border-radius: 18px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06); }
    .promo-box ul { margin: 10px 0 0 20px; padding: 0; color: var(--muted); line-height: 1.7; }

    .section { padding: 16px 0 52px; }
    .services-title { text-align: center; margin-bottom: 16px; }
    .services-title p { color: var(--muted); margin: 0; }
    .services-scroll { display: flex; gap: 14px; overflow-x: auto; padding: 8px 0 12px; margin-top: 12px; scrollbar-width: none; justify-content: center; }
    .services-scroll::-webkit-scrollbar { display: none; }
    .service-item {
      min-width: 126px; background: rgba(255,255,255,0.05); border-radius: 20px; padding: 18px 12px; text-align: center;
      border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 8px 18px rgba(0,0,0,0.18); flex-shrink: 0; transition: 0.22s ease;
    }
    .service-item:hover { transform: translateY(-3px); background: rgba(255,255,255,0.08); }
    .service-item .icon { font-size: 1.65rem; margin-bottom: 8px; line-height: 1; }
    .service-item span { display: block; font-size: 0.95rem; font-weight: 700; color: var(--text); }

    .profile-hero {
      display: grid; grid-template-columns: auto 1fr auto; gap: 18px; align-items: center; margin-bottom: 24px;
      padding: 22px; background: rgba(255,255,255,0.04); border-radius: 24px; border: 1px solid rgba(255,255,255,0.06);
    }
    .avatar {
      width: 84px; height: 84px; border-radius: 50%; background: linear-gradient(135deg, var(--accent), var(--accent-2));
      display: flex; align-items: center; justify-content: center; font-size: 1.6rem; font-weight: 800; color: white; overflow: hidden;
      box-shadow: var(--shadow);
    }
    .avatar img { width: 100%; height: 100%; object-fit: cover; }
    .tag-pill {
      display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 999px;
      background: rgba(181,23,255,0.14); border: 1px solid rgba(181,23,255,0.18); color: #f0d3ff; font-weight: 700; font-size: 0.9rem;
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
      width: 100%; border-radius: 16px; border: 1px solid rgba(255,255,255,0.08); background: rgba(255,255,255,0.06);
      color: var(--text); padding: 14px 15px; font-size: 1rem; outline: none;
    }
    textarea { min-height: 110px; resize: vertical; }
    input:focus, textarea:focus, select:focus { border-color: rgba(181,23,255,0.40); box-shadow: 0 0 0 4px rgba(181,23,255,0.10); }

    .flash-wrap { display: grid; gap: 10px; margin-bottom: 18px; }
    .flash { padding: 13px 15px; border-radius: 14px; font-weight: 700; border: 1px solid transparent; }
    .flash-success { background: var(--success-bg); border-color: var(--success-border); color: #8ff0ab; }
    .flash-error { background: var(--error-bg); border-color: var(--error-border); color: #ffb3bd; }
    .flash-info { background: var(--info-bg); border-color: var(--info-border); color: #f0d3ff; }

    .stats { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 16px; margin-bottom: 22px; }
    .stat { padding: 22px; }
    .stat .label { color: var(--muted); font-size: 0.95rem; margin-bottom: 10px; }
    .stat .value { font-size: 2rem; font-weight: 800; line-height: 1; }
    .top-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }

    .table-card { overflow: hidden; }
    table { width: 100%; border-collapse: collapse; background: transparent; }
    th, td { padding: 14px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.08); vertical-align: top; }
    th { color: var(--text); background: rgba(255,255,255,0.04); font-size: 0.92rem; }
    td { color: var(--muted); font-size: 0.98rem; }
    .status { display: inline-flex; align-items: center; justify-content: center; padding: 7px 11px; border-radius: 999px; font-size: 0.84rem; font-weight: 800; white-space: nowrap; }
    .status-pendiente { background: rgba(245,158,11,0.12); color: #ffd27a; border: 1px solid rgba(245,158,11,0.18); }
    .status-procesando { background: rgba(181,23,255,0.14); color: #f0d3ff; border: 1px solid rgba(181,23,255,0.18); }
    .status-completado { background: rgba(75,211,125,0.14); color: #8ff0ab; border: 1px solid rgba(75,211,125,0.18); }
    .status-cancelado { background: rgba(255,122,138,0.14); color: #ffb3bd; border: 1px solid rgba(255,122,138,0.18); }

    .empty { padding: 28px; text-align: center; color: var(--muted); }
    .footer { padding: 26px 0 46px; color: var(--muted); border-top: 1px solid rgba(255,255,255,0.08); margin-top: 10px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
    .proof-note { font-size: 0.92rem; color: var(--muted); }

    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(16px); }
      to { opacity: 1; transform: translateY(0); }
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
      tr { border-bottom: 1px solid rgba(255,255,255,0.10); padding: 10px 0; }
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
    rendered_content = render_template_string(content, user=user, **context)
    return render_template_string(BASE_HTML, content=rendered_content, title=title, user=user, hide_container=hide_container)


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
          <p class="subtitle">Ofrecemos recargas, cripto y tarjetas de regalo. Los usuarios pueden registrarse, iniciar sesión y gestionar sus pedidos desde la plataforma.</p>
          <div class="hero-actions">
            {% if user %}
              {% if user['is_admin'] %}
                <a class="btn btn-primary" href="{{ url_for('admin_dashboard') }}">Ir al dashboard admin</a>
              {% else %}
                <a class="btn btn-primary" href="{{ url_for('new_order') }}">Hacer pedido</a>
              {% endif %}
            {% endif %}
          </div>
        </div>
        <div class="card price-card">
          <div class="price-kicker">{{ promo['title'] if promo else 'Promoción activa' }}</div>
          <div class="price">{{ promo['price_text'] if promo else '14 500 CUP' }}</div>
          <div class="subtitle" style="margin:0; font-size:1rem;">{{ promo['description'] if promo else 'Recarga promocional disponible.' }}</div>
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
        <div class="services-title"><h2>Productos que ofrecemos</h2><p>Desliza para ver las opciones disponibles.</p></div>
        <div class="services-scroll">
          <a class="service-item" href="{{ url_for('new_order', service='Recargas') }}"><div class="icon">📱</div><span>Recargas</span></a>
          <a class="service-item" href="{{ url_for('new_order', service='Cripto') }}"><div class="icon">💵</div><span>Cripto</span></a>
          <a class="service-item" href="{{ url_for('new_order', service='Gift Cards') }}"><div class="icon">🎁</div><span>Gift Cards</span></a>
        </div>
      </div>
    </section>

    <footer class="footer"><div class="container">Plataforma de servicios · {{ year }}</div></footer>
    """
    return render_page(content, title="Recargas a Cuba", user=user, promo=promo, year=datetime.now().year)


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
        photo = request.files.get("profile_photo")
        photo_path = user["profile_photo"]

        if not first_name or not last_name or city not in CITIES_CUBA:
            flash("Completa los datos del perfil correctamente.", "error")
        else:
            if photo and photo.filename:
                safe_name = secure_filename(photo.filename)
                ext = os.path.splitext(safe_name)[1].lower()
                if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
                    flash("La foto debe ser JPG, PNG o WEBP.", "error")
                    return redirect(url_for("profile"))
                final_name = f"avatar_{uuid.uuid4().hex}{ext}"
                final_path = UPLOAD_DIR / final_name
                photo.save(final_path)
                photo_path = str(final_path)

            conn = get_db()
            conn.execute(
                "UPDATE users SET first_name = ?, last_name = ?, city = ?, profile_photo = ? WHERE id = ?",
                (first_name, last_name, city, photo_path, user["id"]),
            )
            conn.commit()
            conn.close()
            flash("Perfil actualizado correctamente.", "success")
            log_action(user["id"], "profile_updated", "El usuario actualizó su perfil")
            return redirect(url_for("profile"))

    wallet = get_wallet(user["id"])
    profile_photo_url = None
    if user["profile_photo"]:
        profile_photo_url = url_for("uploaded_file", filename=os.path.basename(user["profile_photo"]))

    content = """
    <div class="page-wrap"><div class="container" style="max-width:980px;">
      <div class="profile-hero card">
        <div class="avatar">
          {% if profile_photo_url %}
            <img src="{{ profile_photo_url }}" alt="Foto de perfil">
          {% else %}
            {{ user['first_name'][0] }}{{ user['last_name'][0] }}
          {% endif %}
        </div>
        <div>
          <h2 style="margin-bottom:6px;">{{ user['first_name'] }} {{ user['last_name'] }}</h2>
          <div class="tag-pill">{{ user['profile_tag'] }}</div>
          <p class="subtitle" style="font-size:1rem; margin-top:14px; margin-bottom:0;">{{ user['email'] }} · {{ user['city'] }}</p>
        </div>
        <a class="btn btn-secondary" href="{{ url_for('wallet_page') }}">Ver billetera</a>
      </div>

      <div class="grid-2">
        <div class="card panel">
          <h3>Editar perfil</h3>
          <form method="post" enctype="multipart/form-data">
            <div><label>Nombre</label><input type="text" name="first_name" value="{{ user['first_name'] }}" required></div>
            <div><label>Apellidos</label><input type="text" name="last_name" value="{{ user['last_name'] }}" required></div>
            <div><label>Ciudad</label><select name="city" required>{% for city in cities %}<option value="{{ city }}" {% if user['city'] == city %}selected{% endif %}>{{ city }}</option>{% endfor %}</select></div>
            <div><label>Subir foto</label><input type="file" name="profile_photo"></div>
            <button class="btn btn-primary" type="submit">Guardar cambios</button>
          </form>
        </div>

        <div class="card panel">
          <h3>Resumen rápido</h3>
          <div class="wallet-grid">
            <div class="card wallet-card"><div class="wallet-label">CUP</div><div class="wallet-amount">{{ '%.2f'|format(wallet['cup_balance']) }}</div></div>
            <div class="card wallet-card"><div class="wallet-label">USD</div><div class="wallet-amount">{{ '%.2f'|format(wallet['usd_balance']) }}</div></div>
            <div class="card wallet-card"><div class="wallet-label">USDT</div><div class="wallet-amount">{{ '%.2f'|format(wallet['usdt_balance']) }}</div></div>
          </div>
          <p class="subtitle" style="font-size:1rem; margin-top:18px; margin-bottom:0;">Tu tag sirve para identificar tu cuenta dentro de la plataforma. Luego se puede usar para funciones entre usuarios.</p>
        </div>
      </div>
    </div></div>
    """
    return render_page(content, title="Mi perfil", user=user, cities=CITIES_CUBA, wallet=wallet, profile_photo_url=profile_photo_url)


@app.route("/wallet")
@login_required
def wallet_page():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))
    ensure_wallet(user["id"])
    wallet = get_wallet(user["id"])
    conn = get_db()
    txs = conn.execute("SELECT * FROM wallet_transactions WHERE user_id = ? ORDER BY id DESC LIMIT 30", (user["id"],)).fetchall()
    conn.close()
    content = """
    <div class="page-wrap"><div class="container">
      <div class="top-row"><div><h2>Mi billetera</h2><p class="subtitle" style="font-size:1rem; margin-bottom:0;">Gestiona tus balances y revisa tus movimientos.</p></div><a class="btn btn-primary" href="{{ url_for('new_order') }}">Usar saldo / Comprar</a></div>
      <div class="wallet-grid" style="margin-bottom:22px;">
        <div class="card wallet-card"><div class="wallet-label">Saldo CUP</div><div class="wallet-amount">{{ '%.2f'|format(wallet['cup_balance']) }}</div></div>
        <div class="card wallet-card"><div class="wallet-label">Saldo USD</div><div class="wallet-amount">{{ '%.2f'|format(wallet['usd_balance']) }}</div></div>
        <div class="card wallet-card"><div class="wallet-label">Saldo USDT</div><div class="wallet-amount">{{ '%.2f'|format(wallet['usdt_balance']) }}</div></div>
      </div>
      <div class="card panel">
        <h3 style="margin-bottom:14px;">Movimientos</h3>
        {% if txs %}
        <table>
          <thead><tr><th>ID</th><th>Moneda</th><th>Monto</th><th>Dirección</th><th>Tipo</th><th>Descripción</th><th>Fecha</th></tr></thead>
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
        {% else %}<div class="empty">No hay movimientos todavía.</div>{% endif %}
      </div>
    </div></div>
    """
    return render_page(content, title="Mi billetera", user=user, wallet=wallet, txs=txs)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_file(UPLOAD_DIR / filename)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        user = current_user()
        if user["is_admin"]:
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("home"))
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        city = request.form.get("city", "").strip()
        if not first_name or not last_name or not email or not password or not city:
            flash("Completa todos los campos.", "error")
        elif city not in CITIES_CUBA:
            flash("Selecciona una ciudad válida.", "error")
        elif len(password) < 6:
            flash("La contraseña debe tener al menos 6 caracteres.", "error")
        else:
            conn = get_db()
            existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if existing:
                conn.close()
                flash("Ese correo ya está registrado.", "error")
            else:
                tag = make_tag(first_name, last_name)
                conn.execute(
                    "INSERT INTO users (first_name, last_name, email, password, city, profile_tag, profile_photo, is_admin, is_locked, failed_attempts, last_login_at, created_at) VALUES (?, ?, ?, ?, ?, ?, '', 0, 0, 0, '', ?)",
                    (first_name, last_name, email, generate_password_hash(password), city, tag, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                conn.commit()
                user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
                conn.close()
                ensure_wallet(user["id"])
                session["user_id"] = user["id"]
                flash("Cuenta creada correctamente.", "success")
                log_action(user["id"], "register", f"Nueva cuenta {email}")
                return redirect(url_for("profile"))
    content = """
    <div class="auth-shell"><div class="card auth-card">
      <h2>Crear cuenta</h2>
      <p class="subtitle" style="font-size:1rem; margin-bottom:16px;">Regístrate con nombre y apellidos para usar la plataforma con más seguridad.</p>
      <form method="post">
        <div><label>Nombre</label><input type="text" name="first_name" required></div>
        <div><label>Apellidos</label><input type="text" name="last_name" required></div>
        <div><label>Correo electrónico</label><input type="email" name="email" required></div>
        <div><label>Ciudad</label><select name="city" required><option value="">Selecciona tu ciudad</option>{% for city in cities %}<option value="{{ city }}">{{ city }}</option>{% endfor %}</select></div>
        <div><label>Contraseña</label><input type="password" name="password" required></div>
        <button class="btn btn-primary" type="submit">Crear cuenta</button>
      </form>
      <p class="subtitle" style="font-size:1rem; margin:16px 0 0;">¿Ya tienes cuenta? <a href="{{ url_for('login') }}"><strong>Inicia sesión</strong></a></p>
    </div></div>
    """
    return render_page(content, title="Crear cuenta", user=None, hide_container=True, cities=CITIES_CUBA)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        user = current_user()
        if user["is_admin"]:
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            conn.close()
            flash("Correo o contraseña incorrectos.", "error")
        elif user["is_locked"]:
            conn.close()
            flash("Tu cuenta está bloqueada temporalmente. Contacta soporte.", "error")
        elif not check_password_hash(user["password"], password):
            failed = int(user["failed_attempts"]) + 1
            is_locked = 1 if failed >= 5 else 0
            conn.execute("UPDATE users SET failed_attempts = ?, is_locked = ? WHERE id = ?", (failed, is_locked, user["id"]))
            conn.commit()
            conn.close()
            flash("Correo o contraseña incorrectos.", "error")
        else:
            conn.execute("UPDATE users SET failed_attempts = 0, is_locked = 0, last_login_at = ? WHERE id = ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["id"]))
            conn.commit()
            conn.close()
            session["user_id"] = user["id"]
            flash("Sesión iniciada correctamente.", "success")
            log_action(user["id"], "login", f"Ingreso de {email}")
            if user["is_admin"]:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("home"))
    content = """
    <div class="auth-shell"><div class="card auth-card">
      <h2>Iniciar sesión</h2>
      <p class="subtitle" style="font-size:1rem; margin-bottom:16px;">Entra a tu cuenta para revisar pedidos, perfil y billetera.</p>
      <form method="post">
        <div><label>Correo electrónico</label><input type="email" name="email" required></div>
        <div><label>Contraseña</label><input type="password" name="password" required></div>
        <button class="btn btn-primary" type="submit">Entrar</button>
      </form>
      <p class="subtitle" style="font-size:1rem; margin:16px 0 0;">¿No tienes cuenta? <a href="{{ url_for('register') }}"><strong>Créala aquí</strong></a><br><a href="{{ url_for('forgot_password') }}"><strong>¿Olvidaste tu contraseña?</strong></a></p>
    </div></div>
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
            conn.execute("INSERT INTO password_resets (email, note, status, created_at) VALUES (?, ?, 'Pendiente', ?)", (email, note, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()
            flash("Solicitud enviada. Revisaremos tu caso.", "success")
            return redirect(url_for("login"))
    content = """
    <div class="auth-shell"><div class="card auth-card">
      <h2>Recuperar contraseña</h2>
      <p class="subtitle" style="font-size:1rem; margin-bottom:16px;">Escribe tu correo y una nota opcional para que podamos ayudarte.</p>
      <form method="post">
        <div><label>Correo electrónico</label><input type="email" name="email" required></div>
        <div><label>Nota opcional</label><textarea name="note" placeholder="Ej: no recuerdo mi contraseña"></textarea></div>
        <button class="btn btn-primary" type="submit">Enviar solicitud</button>
      </form>
    </div></div>
    """
    return render_page(content, title="Recuperar contraseña", user=None, hide_container=True)


@app.route("/logout")
def logout():
    session.clear()
    flash("Has cerrado sesión.", "info")
    return redirect(url_for("home"))


@app.route("/orders/new", methods=["GET", "POST"])
@login_required
def new_order():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))
    settings = get_settings()
    selected_service = request.args.get("service", "").strip() or request.form.get("service", "").strip() or "Recargas"
    if selected_service not in PRODUCTS:
        selected_service = "Recargas"

    if request.method == "POST":
        service = request.form.get("service", "").strip()
        if service not in PRODUCTS:
            service = "Recargas"
        customer_name = f"{user['first_name']} {user['last_name']}"
        plan_name = request.form.get("plan_name", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        wallet_network = request.form.get("wallet_network", "").strip()
        wallet_address = request.form.get("wallet_address", "").strip()
        coin_amount_cup = request.form.get("coin_amount_cup", "").strip()
        gift_card_value = request.form.get("gift_card_value", "").strip()
        checkout = None

        if service == "Recargas":
            if plan_name not in PRODUCTS[service] or not phone_number:
                flash("Completa todos los campos obligatorios.", "error")
            else:
                promo = get_active_promo()
                total_cup = recharge_price(plan_name, promo)
                checkout = {"service": service, "plan_name": plan_name, "customer_name": customer_name, "reference": phone_number, "extra_data": "", "total_cup": total_cup, "total_cup_text": f"{total_cup:.0f} CUP", "summary_text": f"Número a recargar: {phone_number}"}
        elif service == "Cripto":
            if wallet_network not in CRYPTO_NETWORKS or not wallet_address or not coin_amount_cup:
                flash("Completa todos los campos obligatorios.", "error")
            else:
                cup_amount = parse_float(coin_amount_cup, 0)
                if cup_amount <= 0:
                    flash("El monto en CUP debe ser válido.", "error")
                else:
                    receive_text = crypto_receive_text(wallet_network, cup_amount, settings)
                    checkout = {"service": service, "plan_name": "Compra de cripto", "customer_name": customer_name, "reference": f"Wallet: {wallet_address}", "extra_data": f"Red: {wallet_network} | Wallet: {wallet_address} | Monto CUP: {cup_amount:.2f} | {receive_text}", "total_cup": cup_amount, "total_cup_text": f"{cup_amount:.2f} CUP", "summary_text": receive_text}
        elif service == "Gift Cards":
            value_num = parse_float(gift_card_value, 0)
            if plan_name not in PRODUCTS[service] or value_num not in [float(v) for v in GIFT_CARD_VALUES]:
                flash("Completa todos los campos obligatorios.", "error")
            else:
                total_cup = gift_card_price_cup(value_num, settings)
                checkout = {"service": service, "plan_name": plan_name, "customer_name": customer_name, "reference": f"Gift Card {plan_name}", "extra_data": f"Valor: {int(value_num)} USD", "total_cup": total_cup, "total_cup_text": f"{total_cup:.2f} CUP", "summary_text": f"Gift Card {plan_name} de {int(value_num)} USD"}
        if checkout:
            session["checkout_data"] = checkout
            return redirect(url_for("checkout"))

    current_recharge_options = PRODUCTS["Recargas"]
    current_gift_brands = PRODUCTS["Gift Cards"]
    usdt_buy_cup = parse_float(settings.get("usdt_buy_cup", "580"), 580)
    usd_cup = parse_float(settings.get("usd_cup", "510"), 510)
    btc_usd = parse_float(settings.get("btc_usd", "85000"), 85000)
    content = """
    <div class="page-wrap"><div class="container" style="max-width:760px;">
      <div class="card form-card">
        <div class="top-row" style="margin-bottom:10px;"><div><h2>Nuevo pedido</h2><p class="subtitle" style="font-size:1rem; margin-bottom:0;">Selecciona el producto y completa los datos necesarios.</p></div><a class="btn btn-secondary" href="{{ url_for('my_orders') }}">Mis pedidos</a></div>
        <form method="post">
          <div><label>Producto</label><select name="service" onchange="window.location='{{ url_for('new_order') }}?service=' + encodeURIComponent(this.value)">{% for product in products.keys() %}<option value="{{ product }}" {% if selected_service == product %}selected{% endif %}>{{ product }}</option>{% endfor %}</select></div>
          {% if selected_service == 'Recargas' %}
          <div><label>Número a recargar</label><input type="text" name="phone_number" placeholder="Ej: 53XXXXXXXX" required></div>
          <div><label>Seleccionar recarga</label><select name="plan_name" required><option value="">Selecciona una opción</option>{% for item in current_recharge_options %}<option value="{{ item }}">{{ item }}</option>{% endfor %}</select></div>
          {% elif selected_service == 'Cripto' %}
          <div><label>Cripto seleccionada</label><select name="wallet_network" id="wallet_network" required><option value="">Selecciona una red</option>{% for item in crypto_networks %}<option value="{{ item }}">{{ item }}</option>{% endfor %}</select></div>
          <div><label>Billetera virtual</label><input type="text" name="wallet_address" placeholder="Pega aquí tu wallet" required></div>
          <div><label>Monto en CUP a comprar</label><input type="text" name="coin_amount_cup" id="coin_amount_cup" placeholder="Ej: 5000 CUP" required></div>
          <div class="promo-box" style="margin-top:0;"><strong>Estimación</strong><ul><li id="crypto_preview">Escribe el monto y selecciona la red para ver cuánto caerá en tu cuenta.</li></ul></div>
          <div class="promo-box" style="margin-top:0;"><strong>Importante</strong><ul><li>Verifica muy bien tu billetera virtual antes de confirmar.</li><li>Si la wallet está incorrecta, la responsabilidad será del cliente.</li><li>Revisa la red seleccionada para evitar pérdidas.</li></ul></div>
          {% elif selected_service == 'Gift Cards' %}
          <div><label>Tienda / plataforma</label><select name="plan_name" required><option value="">Selecciona una opción</option>{% for item in current_gift_brands %}<option value="{{ item }}">{{ item }}</option>{% endfor %}</select></div>
          <div><label>Valor de la gift card</label><select name="gift_card_value" required><option value="">Selecciona un valor</option>{% for item in gift_card_values %}<option value="{{ item }}">{{ item }} USD</option>{% endfor %}</select></div>
          {% endif %}
          <button class="btn btn-primary" type="submit">Ir para pagos</button>
        </form>
      </div>
    </div></div>
    <script>
      const usdtRate = {{ usdt_buy_cup }}; const usdCup = {{ usd_cup }}; const btcUsd = {{ btc_usd }};
      const networkEl = document.getElementById('wallet_network'); const amountEl = document.getElementById('coin_amount_cup'); const previewEl = document.getElementById('crypto_preview');
      function updateCryptoPreview() {
        if (!networkEl || !amountEl || !previewEl) return;
        const network = networkEl.value; const amount = parseFloat((amountEl.value || '').replace(',', '.'));
        if (!network || !amount || amount <= 0) { previewEl.textContent = 'Escribe el monto y selecciona la red para ver cuánto caerá en tu cuenta.'; return; }
        if (network === 'USDT' || network === 'TRC20') { const val = amount / usdtRate; previewEl.textContent = 'Recibirás aprox. ' + val.toFixed(2) + ' ' + network; return; }
        if (network === 'Bitcoin') { const val = amount / (btcUsd * usdCup); previewEl.textContent = 'Recibirás aprox. ' + val.toFixed(8) + ' BTC'; }
      }
      if (networkEl && amountEl) { networkEl.addEventListener('change', updateCryptoPreview); amountEl.addEventListener('input', updateCryptoPreview); }
    </script>
    """
    return render_page(content, title="Nuevo pedido", user=user, selected_service=selected_service, products=PRODUCTS, current_recharge_options=current_recharge_options, current_gift_brands=current_gift_brands, crypto_networks=CRYPTO_NETWORKS, gift_card_values=GIFT_CARD_VALUES, usdt_buy_cup=usdt_buy_cup, usd_cup=usd_cup, btc_usd=btc_usd)


@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    user = current_user()
    checkout_data = session.get("checkout_data")
    if not checkout_data:
        flash("No hay un pedido pendiente de pago.", "info")
        return redirect(url_for("new_order"))
    settings = get_settings()
    if request.method == "POST":
        proof = request.files.get("proof_file")
        if not proof or not proof.filename:
            flash("Adjunta una imagen del comprobante.", "error")
        else:
            safe_name = secure_filename(proof.filename)
            ext = os.path.splitext(safe_name)[1].lower()
            if ext not in [".jpg", ".jpeg", ".png", ".webp", ".pdf"]:
                flash("Formato inválido. Usa JPG, PNG, WEBP o PDF.", "error")
            else:
                final_name = f"proof_{uuid.uuid4().hex}{ext}"
                proof_path = UPLOAD_DIR / final_name
                proof.save(proof_path)
                conn = get_db()
                conn.execute(
                    "INSERT INTO orders (user_id, customer_name, phone_number, service, plan_name, extra_data, total_cup, status, payment_status, proof_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'Pendiente', 'Pago en revisión', ?, ?)",
                    (user["id"], checkout_data.get("customer_name", "Pedido web"), checkout_data.get("reference", ""), checkout_data.get("service", ""), checkout_data.get("plan_name", ""), checkout_data.get("extra_data", ""), float(checkout_data.get("total_cup", 0)), str(proof_path), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                conn.commit()
                conn.close()
                session.pop("checkout_data", None)
                flash("Pedido guardado y pago enviado para revisión.", "success")
                log_action(user["id"], "checkout_paid", f"Pedido {checkout_data.get('service', '')}")
                return redirect(url_for("my_orders"))
    content = """
    <div class="page-wrap"><div class="container" style="max-width:860px;"><div class="grid-2">
      <div class="card panel">
        <h2>Pago</h2>
        <p class="subtitle" style="font-size:1rem;">Envía el importe indicado y luego adjunta el comprobante.</p>
        <div class="promo-box" style="margin-top:0;"><strong>{{ settings['payment_card_label'] }}</strong><ul><li>Enviar a: {{ settings['payment_card_number'] }}</li><li>Titular: {{ settings['payment_card_holder'] }}</li><li>{{ settings['payment_note'] }}</li></ul></div>
        <form method="post" enctype="multipart/form-data">
          <div><label>Adjuntar comprobante</label><input type="file" name="proof_file" required><div class="proof-note">Sube una foto o PDF del pago.</div></div>
          <button class="btn btn-primary" type="submit">Confirmar pago y guardar pedido</button>
          {% if reportlab_available %}<a class="btn btn-secondary" href="{{ url_for('checkout_receipt_pdf') }}">Descargar resumen PDF</a>{% endif %}
        </form>
      </div>
      <div class="card panel">
        <h2>Resumen de compra</h2>
        <table><tbody>
          <tr><td data-label="Producto"><strong>Producto</strong></td><td>{{ checkout.service }}</td></tr>
          <tr><td data-label="Opción"><strong>Opción</strong></td><td>{{ checkout.plan_name }}</td></tr>
          <tr><td data-label="Referencia"><strong>Referencia</strong></td><td>{{ checkout.reference }}</td></tr>
          <tr><td data-label="Detalle"><strong>Detalle</strong></td><td>{{ checkout.extra_data or checkout.summary_text }}</td></tr>
          <tr><td data-label="Total"><strong>Total a pagar</strong></td><td>{{ checkout.total_cup_text }}</td></tr>
        </tbody></table>
      </div>
    </div></div></div>
    """
    return render_page(content, title="Pago", user=user, checkout=checkout_data, settings=settings, reportlab_available=REPORTLAB_AVAILABLE)


@app.route("/checkout/receipt.pdf")
@login_required
def checkout_receipt_pdf():
    checkout_data = session.get("checkout_data")
    if not checkout_data:
        flash("No hay resumen disponible.", "error")
        return redirect(url_for("new_order"))
    pdf_buffer = generate_receipt_pdf(checkout_data)
    if not pdf_buffer:
        flash("La generación de PDF no está disponible en este entorno.", "error")
        return redirect(url_for("checkout"))
    return send_file(pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name="resumen_compra.pdf")


@app.route("/mis-pedidos")
@login_required
def my_orders():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))
    conn = get_db()
    orders = conn.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()
    total_orders = len(orders)
    pending = sum(1 for o in orders if o["status"] == "Pendiente")
    processing = sum(1 for o in orders if o["status"] == "Procesando")
    completed = sum(1 for o in orders if o["status"] == "Completado")
    content = """
    <div class="page-wrap"><div class="container">
      <div class="top-row"><div><h2>Mis pedidos</h2><p class="subtitle" style="font-size:1rem; margin-bottom:0;">Bienvenido, {{ user['first_name'] }}. Aquí puedes revisar todos tus pedidos.</p></div><div style="display:flex; gap:12px; flex-wrap:wrap;"><a class="btn btn-secondary" href="{{ url_for('home') }}">Volver al inicio</a><a class="btn btn-primary" href="{{ url_for('new_order') }}">Crear nuevo pedido</a></div></div>
      <div class="stats">
        <div class="card stat"><div class="label">Pedidos totales</div><div class="value">{{ total_orders }}</div></div>
        <div class="card stat"><div class="label">Pendientes</div><div class="value">{{ pending }}</div></div>
        <div class="card stat"><div class="label">Procesando</div><div class="value">{{ processing }}</div></div>
        <div class="card stat"><div class="label">Completados</div><div class="value">{{ completed }}</div></div>
      </div>
      <div class="card table-card">
        {% if orders %}
        <table><thead><tr><th>ID</th><th>Producto</th><th>Opción</th><th>Referencia</th><th>Pago</th><th>Estado</th><th>Fecha</th></tr></thead><tbody>
          {% for order in orders %}
          <tr>
            <td data-label="ID">#{{ order['id'] }}</td>
            <td data-label="Producto">{{ order['service'] }}</td>
            <td data-label="Opción">{{ order['plan_name'] }}</td>
            <td data-label="Referencia">{{ order['phone_number'] }}</td>
            <td data-label="Pago">{{ order['payment_status'] }}</td>
            <td data-label="Estado"><span class="status status-{{ order['status'].lower()|replace('á','a')|replace('é','e')|replace('í','i')|replace('ó','o')|replace('ú','u') }}">{{ order['status'] }}</span></td>
            <td data-label="Fecha">{{ order['created_at'] }}</td>
          </tr>
          {% endfor %}
        </tbody></table>
        {% else %}<div class="empty">Todavía no tienes pedidos creados.</div>{% endif %}
      </div>
    </div></div>
    """
    return render_page(content, title="Mis pedidos", user=user, orders=orders, total_orders=total_orders, pending=pending, processing=processing, completed=completed)


@app.route("/admin")
@admin_required
def admin_dashboard():
    user = current_user()
    conn = get_db()
    orders = conn.execute("SELECT orders.*, users.first_name AS user_first_name, users.last_name AS user_last_name, users.email AS user_email FROM orders JOIN users ON orders.user_id = users.id ORDER BY orders.id DESC").fetchall()
    users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    promotions = conn.execute("SELECT * FROM promotions ORDER BY id DESC").fetchall()
    resets = conn.execute("SELECT * FROM password_resets ORDER BY id DESC").fetchall()
    audits = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    settings = get_settings()
    total_orders = len(orders)
    total_users = sum(1 for u in users if not u["is_admin"])
    pending = sum(1 for o in orders if o["status"] == "Pendiente")
    completed = sum(1 for o in orders if o["status"] == "Completado")
    content = """
    <div class="page-wrap"><div class="container">
      <div class="top-row"><div><h2>Dashboard del administrador</h2><p class="subtitle" style="font-size:1rem; margin-bottom:0;">Gestiona usuarios, promociones, tasas, pagos, billeteras y pedidos.</p></div></div>
      <div class="stats">
        <div class="card stat"><div class="label">Usuarios</div><div class="value">{{ total_users }}</div></div>
        <div class="card stat"><div class="label">Pedidos</div><div class="value">{{ total_orders }}</div></div>
        <div class="card stat"><div class="label">Pendientes</div><div class="value">{{ pending }}</div></div>
        <div class="card stat"><div class="label">Completados</div><div class="value">{{ completed }}</div></div>
      </div>

      <div class="grid-2" style="margin-bottom:22px;">
        <div class="card panel">
          <h3>Nueva promoción</h3>
          <form method="post" action="{{ url_for('create_promo') }}">
            <div><label>Título</label><input type="text" name="title" required></div>
            <div><label>Precio visible</label><input type="text" name="price_text" placeholder="Ej: 14 500 CUP" required></div>
            <div><label>Precio numérico CUP</label><input type="text" name="price_cup" placeholder="14500" required></div>
            <div><label>Descripción</label><input type="text" name="description" required></div>
            <div><label>Bonificación 1</label><input type="text" name="bonus_1" required></div>
            <div><label>Bonificación 2</label><input type="text" name="bonus_2" required></div>
            <div><label>Bonificación 3</label><input type="text" name="bonus_3" required></div>
            <button class="btn btn-primary" type="submit">Crear promoción</button>
          </form>
        </div>
        <div class="card panel">
          <h3>Tasas y datos de pago</h3>
          <form method="post" action="{{ url_for('update_settings') }}">
            <div><label>USD en CUP</label><input type="text" name="usd_cup" value="{{ settings['usd_cup'] }}" required></div>
            <div><label>USDT compra en CUP</label><input type="text" name="usdt_buy_cup" value="{{ settings['usdt_buy_cup'] }}" required></div>
            <div><label>BTC en USD</label><input type="text" name="btc_usd" value="{{ settings['btc_usd'] }}" required></div>
            <div><label>Markup Gift Cards (%)</label><input type="text" name="giftcard_markup_percent" value="{{ settings['giftcard_markup_percent'] }}" required></div>
            <div><label>Etiqueta del pago</label><input type="text" name="payment_card_label" value="{{ settings['payment_card_label'] }}" required></div>
            <div><label>Número de tarjeta</label><input type="text" name="payment_card_number" value="{{ settings['payment_card_number'] }}" required></div>
            <div><label>Titular</label><input type="text" name="payment_card_holder" value="{{ settings['payment_card_holder'] }}" required></div>
            <div><label>Nota de pago</label><input type="text" name="payment_note" value="{{ settings['payment_note'] }}" required></div>
            <button class="btn btn-primary" type="submit">Guardar tasas y pagos</button>
          </form>
        </div>
      </div>

      <div class="grid-2" style="margin-bottom:22px;">
        <div class="card panel">
          <h3>Solicitudes de recuperación</h3>
          {% if resets %}
          <table><thead><tr><th>ID</th><th>Correo</th><th>Nota</th><th>Estado</th></tr></thead><tbody>{% for item in resets %}<tr><td data-label="ID">#{{ item['id'] }}</td><td data-label="Correo">{{ item['email'] }}</td><td data-label="Nota">{{ item['note'] or 'Sin nota' }}</td><td data-label="Estado">{{ item['status'] }}</td></tr>{% endfor %}</tbody></table>
          {% else %}<div class="empty">No hay solicitudes por ahora.</div>{% endif %}
        </div>
        <div class="card panel">
          <h3>Restablecer contraseña</h3>
          <form method="post" action="{{ url_for('admin_reset_password') }}">
            <div><label>Correo del usuario</label><input type="email" name="email" required></div>
            <div><label>Nueva contraseña</label><input type="text" name="new_password" required></div>
            <button class="btn btn-primary" type="submit">Cambiar contraseña</button>
          </form>
        </div>
      </div>

      <div class="grid-2" style="margin-bottom:22px;">
        <div class="card panel">
          <h3>Ajustar billetera</h3>
          <form method="post" action="{{ url_for('admin_adjust_wallet') }}">
            <div><label>Correo del usuario</label><input type="email" name="email" required></div>
            <div><label>Moneda</label><select name="currency" required><option value="CUP">CUP</option><option value="USD">USD</option><option value="USDT">USDT</option></select></div>
            <div><label>Monto</label><input type="text" name="amount" required></div>
            <div><label>Acción</label><select name="direction" required><option value="credit">Acreditar</option><option value="debit">Descontar</option></select></div>
            <div><label>Descripción</label><input type="text" name="description" required></div>
            <button class="btn btn-primary" type="submit">Aplicar movimiento</button>
          </form>
        </div>
        <div class="card panel">
          <h3>Auditoría reciente</h3>
          {% if audits %}
          <table><thead><tr><th>ID</th><th>Acción</th><th>Detalle</th><th>Fecha</th></tr></thead><tbody>{% for item in audits %}<tr><td data-label="ID">#{{ item['id'] }}</td><td data-label="Acción">{{ item['action'] }}</td><td data-label="Detalle">{{ item['details'] }}</td><td data-label="Fecha">{{ item['created_at'] }}</td></tr>{% endfor %}</tbody></table>
          {% else %}<div class="empty">No hay registros todavía.</div>{% endif %}
        </div>
      </div>

      <div class="card panel" style="margin-bottom:22px;">
        <h3 style="margin-bottom:14px;">Promociones</h3>
        {% if promotions %}
        <table><thead><tr><th>ID</th><th>Título</th><th>Precio</th><th>Estado</th><th>Acciones</th></tr></thead><tbody>{% for promo in promotions %}<tr><td data-label="ID">#{{ promo['id'] }}</td><td data-label="Título">{{ promo['title'] }}</td><td data-label="Precio">{{ promo['price_text'] }}</td><td data-label="Estado">{{ 'Activa' if promo['is_active'] else 'Inactiva' }}</td><td data-label="Acciones"><form method="post" action="{{ url_for('activate_promo', promo_id=promo['id']) }}" style="margin-bottom:8px;"><button class="btn btn-secondary" type="submit">Activar</button></form><form method="post" action="{{ url_for('delete_promo', promo_id=promo['id']) }}"><button class="btn btn-danger" type="submit">Eliminar</button></form></td></tr>{% endfor %}</tbody></table>
        {% else %}<div class="empty">No hay promociones creadas.</div>{% endif %}
      </div>

      <div class="card panel" style="margin-bottom:22px;">
        <h3 style="margin-bottom:14px;">Pedidos</h3>
        {% if orders %}
        <table><thead><tr><th>ID</th><th>Usuario</th><th>Producto</th><th>Opción</th><th>Referencia</th><th>Total CUP</th><th>Pago</th><th>Estado</th><th>Fecha</th><th>Acción</th></tr></thead><tbody>{% for order in orders %}<tr><td data-label="ID">#{{ order['id'] }}</td><td data-label="Usuario">{{ order['user_first_name'] }} {{ order['user_last_name'] }}<br><small>{{ order['user_email'] }}</small></td><td data-label="Producto">{{ order['service'] }}</td><td data-label="Opción">{{ order['plan_name'] }}</td><td data-label="Referencia">{{ order['phone_number'] }}<br><small>{{ order['extra_data'] }}</small></td><td data-label="Total CUP">{{ order['total_cup'] }}</td><td data-label="Pago">{{ order['payment_status'] }}</td><td data-label="Estado"><span class="status status-{{ order['status'].lower()|replace('á','a')|replace('é','e')|replace('í','i')|replace('ó','o')|replace('ú','u') }}">{{ order['status'] }}</span></td><td data-label="Fecha">{{ order['created_at'] }}</td><td data-label="Acción"><form method="post" action="{{ url_for('update_order_status', order_id=order['id']) }}"><select name="status"><option value="Pendiente" {% if order['status'] == 'Pendiente' %}selected{% endif %}>Pendiente</option><option value="Procesando" {% if order['status'] == 'Procesando' %}selected{% endif %}>Procesando</option><option value="Completado" {% if order['status'] == 'Completado' %}selected{% endif %}>Completado</option><option value="Cancelado" {% if order['status'] == 'Cancelado' %}selected{% endif %}>Cancelado</option></select><button class="btn btn-secondary" style="margin-top:8px; width:100%;" type="submit">Actualizar</button></form></td></tr>{% endfor %}</tbody></table>
        {% else %}<div class="empty">No hay pedidos todavía.</div>{% endif %}
      </div>

      <div class="card panel">
        <h3 style="margin-bottom:14px;">Usuarios registrados</h3>
        {% if users %}
        <table><thead><tr><th>ID</th><th>Nombre</th><th>Correo</th><th>Tag</th><th>Ciudad</th><th>Tipo</th><th>Bloqueo</th><th>Fecha de registro</th></tr></thead><tbody>{% for item in users %}<tr><td data-label="ID">#{{ item['id'] }}</td><td data-label="Nombre">{{ item['first_name'] }} {{ item['last_name'] }}</td><td data-label="Correo">{{ item['email'] }}</td><td data-label="Tag">{{ item['profile_tag'] }}</td><td data-label="Ciudad">{{ item['city'] }}</td><td data-label="Tipo">{{ 'Admin' if item['is_admin'] else 'Cliente' }}</td><td data-label="Bloqueo">{{ 'Sí' if item['is_locked'] else 'No' }}</td><td data-label="Fecha">{{ item['created_at'] }}</td></tr>{% endfor %}</tbody></table>
        {% else %}<div class="empty">No hay usuarios registrados.</div>{% endif %}
      </div>
    </div></div>
    """
    return render_page(content, title="Dashboard admin", user=user, orders=orders, users=users, promotions=promotions, resets=resets, audits=audits, total_orders=total_orders, total_users=total_users, pending=pending, completed=completed, settings=settings)


@app.route("/admin/promotions/create", methods=["POST"])
@admin_required
def create_promo():
    actor = current_user()
    title = request.form.get("title", "").strip()
    price_text = request.form.get("price_text", "").strip()
    price_cup = parse_float(request.form.get("price_cup", "0"), 0)
    description = request.form.get("description", "").strip()
    bonus_1 = request.form.get("bonus_1", "").strip()
    bonus_2 = request.form.get("bonus_2", "").strip()
    bonus_3 = request.form.get("bonus_3", "").strip()
    if not all([title, price_text, description, bonus_1, bonus_2, bonus_3]) or price_cup <= 0:
        flash("Completa todos los campos de la promoción.", "error")
    else:
        conn = get_db()
        conn.execute("INSERT INTO promotions (title, price_text, price_cup, description, bonus_1, bonus_2, bonus_3, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)", (title, price_text, price_cup, description, bonus_1, bonus_2, bonus_3, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        flash("Promoción creada correctamente.", "success")
        log_action(actor["id"], "promo_created", title)
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/settings/update", methods=["POST"])
@admin_required
def update_settings():
    actor = current_user()
    fields = ["usd_cup", "usdt_buy_cup", "btc_usd", "giftcard_markup_percent", "payment_card_label", "payment_card_number", "payment_card_holder", "payment_note"]
    conn = get_db()
    for field in fields:
        value = request.form.get(field, "").strip()
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", (value, field))
    conn.commit()
    conn.close()
    flash("Tasas y datos de pago actualizados.", "success")
    log_action(actor["id"], "settings_updated", "Actualizó tasas y pagos")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/promotions/<int:promo_id>/activate", methods=["POST"])
@admin_required
def activate_promo(promo_id):
    actor = current_user()
    conn = get_db()
    conn.execute("UPDATE promotions SET is_active = 0")
    conn.execute("UPDATE promotions SET is_active = 1 WHERE id = ?", (promo_id,))
    conn.commit()
    conn.close()
    flash("Promoción activada.", "success")
    log_action(actor["id"], "promo_activated", f"Promo #{promo_id}")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/promotions/<int:promo_id>/delete", methods=["POST"])
@admin_required
def delete_promo(promo_id):
    actor = current_user()
    conn = get_db()
    conn.execute("DELETE FROM promotions WHERE id = ?", (promo_id,))
    remaining_active = conn.execute("SELECT id FROM promotions WHERE is_active = 1 LIMIT 1").fetchone()
    if not remaining_active:
        latest = conn.execute("SELECT id FROM promotions ORDER BY id DESC LIMIT 1").fetchone()
        if latest:
            conn.execute("UPDATE promotions SET is_active = 1 WHERE id = ?", (latest["id"],))
    conn.commit()
    conn.close()
    flash("Promoción eliminada.", "info")
    log_action(actor["id"], "promo_deleted", f"Promo #{promo_id}")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reset-password", methods=["POST"])
@admin_required
def admin_reset_password():
    actor = current_user()
    email = request.form.get("email", "").strip().lower()
    new_password = request.form.get("new_password", "").strip()
    if not email or not new_password:
        flash("Completa correo y nueva contraseña.", "error")
        return redirect(url_for("admin_dashboard"))
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not user:
        conn.close()
        flash("No encontramos ese usuario.", "error")
        return redirect(url_for("admin_dashboard"))
    conn.execute("UPDATE users SET password = ?, failed_attempts = 0, is_locked = 0 WHERE email = ?", (generate_password_hash(new_password), email))
    conn.execute("UPDATE password_resets SET status = 'Completado' WHERE email = ? AND status = 'Pendiente'", (email,))
    conn.commit()
    conn.close()
    flash("Contraseña cambiada correctamente.", "success")
    log_action(actor["id"], "password_reset", email)
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/wallet-adjust", methods=["POST"])
@admin_required
def admin_adjust_wallet():
    actor = current_user()
    email = request.form.get("email", "").strip().lower()
    currency = request.form.get("currency", "").strip()
    amount = parse_float(request.form.get("amount", "0"), 0)
    direction = request.form.get("direction", "").strip()
    description = request.form.get("description", "").strip()
    if currency not in ["CUP", "USD", "USDT"] or amount <= 0 or direction not in ["credit", "debit"] or not description:
        flash("Completa correctamente el ajuste de billetera.", "error")
        return redirect(url_for("admin_dashboard"))
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not user:
        flash("No encontramos ese usuario.", "error")
        return redirect(url_for("admin_dashboard"))
    adjust_wallet(user["id"], currency, amount, description, direction)
    flash("Movimiento aplicado en la billetera.", "success")
    log_action(actor["id"], "wallet_adjust", f"{email} {direction} {amount} {currency}")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def update_order_status(order_id):
    actor = current_user()
    status = request.form.get("status", "Pendiente").strip()
    valid_statuses = {"Pendiente", "Procesando", "Completado", "Cancelado"}
    if status not in valid_statuses:
        flash("Estado inválido.", "error")
        return redirect(url_for("admin_dashboard"))
    conn = get_db()
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()
    flash("Estado del pedido actualizado.", "success")
    log_action(actor["id"], "order_status_updated", f"Pedido #{order_id} -> {status}")
    return redirect(url_for("admin_dashboard"))


@app.errorhandler(403)
def forbidden(_error):
    content = """
    <div class="page-wrap"><div class="container" style="max-width:720px;"><div class="card panel"><h2>Acceso denegado</h2><p class="subtitle" style="font-size:1rem; margin-bottom:18px;">No tienes permisos para entrar a esta página.</p><a class="btn btn-primary" href="{{ url_for('home') }}">Volver al inicio</a></div></div></div>
    """
    return render_page(content, title="Acceso denegado", user=current_user()), 403


if __name__ == "__main__":
    app.run(debug=True)
    
