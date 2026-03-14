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
DB_PATH = BASE_DIR / "banco_cuba.db"
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

GIFT_CARD_BRANDS = ["Amazon", "Apple", "Google Play", "Steam", "Netflix"]
GIFT_CARD_VALUES = [5, 10, 15, 20, 25, 50, 100]

DEPOSIT_METHODS = ["Cripto", "Paypal", "Tarjeta CUP", "PIX Brasil"]
WITHDRAW_METHODS = ["Cripto", "Paypal", "Tarjeta CUP", "PIX Brasil"]

PRODUCTS = {
    "Recargas": [item["label"] for item in RECHARGE_OPTIONS],
    "Gift Cards": GIFT_CARD_BRANDS,
}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def clean_tag(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if raw.startswith("@"):
        raw = raw[1:]
    raw = "".join(ch for ch in raw if ch.isalnum() or ch in "._")
    return "@" + raw if raw else ""


def generate_referral_code():
    return "REF" + secrets.token_hex(4).upper()


def make_default_tag(first_name: str, last_name: str):
    base = f"{first_name}.{last_name}".lower().replace(" ", "")
    base = "".join(ch for ch in base if ch.isalnum() or ch in "._")
    if not base:
        base = "usuario"
    return f"@{base}{secrets.randbelow(900) + 100}"


def card_mask(carnet: str):
    carnet = (carnet or "").strip()
    if len(carnet) <= 4:
        return "*" * len(carnet)
    return "*" * (len(carnet) - 4) + carnet[-4:]


def log_action(actor_user_id, action, details=""):
    conn = get_db()
    q(
        conn,
        """
        INSERT INTO audit_logs (actor_user_id, action, details, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (actor_user_id, action, details, now_str()),
    )
    conn.commit()
    conn.close()


def get_settings():
    conn = get_db()
    rows = q(conn, "SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def get_setting(key: str, default=None):
    settings = get_settings()
    return settings.get(key, default)


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
        q(
            conn,
            """
            INSERT INTO wallets (
                user_id, cup_balance, usd_balance, usdt_balance,
                bonus_usdt_balance, created_at
            ) VALUES (?, 0, 0, 0, 0, ?)
            """,
            (user_id, now_str()),
        )
        conn.commit()
    conn.close()


def get_wallet(user_id):
    ensure_wallet(user_id)
    conn = get_db()
    wallet = q(conn, "SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return wallet


def wallet_field(currency):
    return {
        "CUP": "cup_balance",
        "USD": "usd_balance",
        "USDT": "usdt_balance",
        "BONUS_USDT": "bonus_usdt_balance",
    }[currency]


def add_wallet_tx(user_id, currency, amount, direction, tx_type, description, reference=""):
    conn = get_db()
    q(
        conn,
        """
        INSERT INTO wallet_transactions
        (user_id, currency, amount, direction, tx_type, description, reference, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, currency, amount, direction, tx_type, description, reference, now_str()),
    )
    conn.commit()
    conn.close()


def adjust_wallet(user_id, currency, amount, description, direction, tx_type="admin_adjustment", reference=""):
    ensure_wallet(user_id)
    field = wallet_field(currency)
    sign = 1 if direction == "credit" else -1
    conn = get_db()
    q(conn, f"UPDATE wallets SET {field} = {field} + ? WHERE user_id = ?", (sign * amount, user_id))
    conn.commit()
    conn.close()
    add_wallet_tx(user_id, currency, amount, direction, tx_type, description, reference)


def can_debit_wallet(user_id, currency, amount):
    wallet = get_wallet(user_id)
    field = wallet_field(currency)
    return float(wallet[field]) >= float(amount)


def get_active_promo():
    conn = get_db()
    promo = q(conn, "SELECT * FROM promotions WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    if not promo:
        promo = q(conn, "SELECT * FROM promotions ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return promo


def generate_receipt_pdf(order_data):
    if not REPORTLAB_AVAILABLE:
        return None

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    y = height - 60

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, y, "Resumen de operación")
    y -= 30

    pdf.setFont("Helvetica", 11)
    lines = [
        f"Fecha: {now_str()}",
        f"Tipo: {order_data.get('type', '')}",
        f"Producto: {order_data.get('service', '')}",
        f"Detalle: {order_data.get('detail', '')}",
        f"Monto: {order_data.get('amount_text', '')}",
        f"Estado: {order_data.get('status', '')}",
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


def init_db():
    conn = get_db()

    q(conn, """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            carnet TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            city TEXT NOT NULL,
            profile_tag TEXT NOT NULL UNIQUE,
            profile_photo TEXT NOT NULL DEFAULT '',
            referral_code TEXT NOT NULL UNIQUE,
            referred_by_user_id INTEGER,
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_locked INTEGER NOT NULL DEFAULT 0,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_login_at TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(referred_by_user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER PRIMARY KEY,
            cup_balance REAL NOT NULL DEFAULT 0,
            usd_balance REAL NOT NULL DEFAULT 0,
            usdt_balance REAL NOT NULL DEFAULT 0,
            bonus_usdt_balance REAL NOT NULL DEFAULT 0,
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
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_user_id INTEGER NOT NULL,
            invited_user_id INTEGER NOT NULL,
            reward_usdt REAL NOT NULL DEFAULT 0.25,
            required_deposit_usd REAL NOT NULL DEFAULT 5,
            status TEXT NOT NULL DEFAULT 'pendiente',
            activated_at TEXT NOT NULL DEFAULT '',
            paid_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(inviter_user_id) REFERENCES users(id),
            FOREIGN KEY(invited_user_id) REFERENCES users(id)
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
        CREATE TABLE IF NOT EXISTS conversions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            from_currency TEXT NOT NULL,
            to_currency TEXT NOT NULL,
            from_amount REAL NOT NULL,
            to_amount REAL NOT NULL,
            rate_used REAL NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount REAL NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            proof_path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Pendiente',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount REAL NOT NULL,
            target_detail TEXT NOT NULL DEFAULT '',
            payout_amount REAL NOT NULL DEFAULT 0,
            payout_currency TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Pendiente',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            plan_name TEXT NOT NULL DEFAULT '',
            customer_name TEXT NOT NULL DEFAULT '',
            phone_number TEXT NOT NULL DEFAULT '',
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
        "usd_buy_cup": "510",
        "usd_sell_cup": "490",
        "usdt_buy_cup": "585",
        "usdt_sell_cup": "575",
        "usdt_to_usd": "1.00",
        "usd_to_usdt": "1.00",
        "giftcard_markup_percent": "10",
        "referral_reward_usdt": "0.25",
        "referral_required_deposit_usd": "5",
        "bonus_withdraw_min_usdt": "1",
        "payment_card_label": "Tarjeta de pago",
        "payment_card_number": "9224 xxxx xxxx xxxx",
        "payment_card_holder": "Nombre de tu mamá",
        "payment_note": "Envía el importe exacto y sube el comprobante.",
    }

    for key, value in defaults.items():
        if not q(conn, "SELECT key FROM settings WHERE key = ?", (key,)).fetchone():
            q(conn, "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    admin_email = "admin@recargas.local"
    if not q(conn, "SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone():
        q(conn, """
            INSERT INTO users (
                first_name, last_name, carnet, email, password, city,
                profile_tag, profile_photo, referral_code, referred_by_user_id,
                is_admin, is_locked, failed_attempts, created_at, last_login_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, NULL, 1, 0, 0, ?, '')
        """, (
            "Administrador",
            "General",
            "ADMIN0001",
            admin_email,
            generate_password_hash("admin123"),
            "La Habana",
            "@admin999",
            "ADMIN999",
            now_str(),
        ))
        admin_id = q(conn, "SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone()["id"]
        q(conn, """
            INSERT OR IGNORE INTO wallets (
                user_id, cup_balance, usd_balance, usdt_balance, bonus_usdt_balance, created_at
            ) VALUES (?, 0, 0, 0, 0, ?)
        """, (admin_id, now_str()))

    if not q(conn, "SELECT id FROM promotions LIMIT 1").fetchone():
        q(conn, """
            INSERT INTO promotions
            (title, price_text, price_cup, description, bonus_1, bonus_2, bonus_3, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "PROMOCIÓN ACTIVA",
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
      --bg-soft: #f7f7fb;
      --card: #ffffff;
      --text: #161616;
      --muted: #6b6b78;
      --border: rgba(138,5,190,0.10);
      --shadow: 0 18px 38px rgba(138,5,190,0.10);
      --accent: #8A05BE;
      --accent-2: #9f2bce;
      --accent-soft: rgba(138,5,190,0.08);
      --danger: #d63b59;
      --success: #1f9d57;
      --success-bg: rgba(31,157,87,0.10);
      --error-bg: rgba(214,59,89,0.10);
      --info-bg: rgba(138,5,190,0.10);
      --radius-xl: 28px;
      --radius-lg: 20px;
      --radius-md: 16px;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: Inter, Arial, Helvetica, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, rgba(138,5,190,0.10), transparent 24%),
        radial-gradient(circle at top left, rgba(138,5,190,0.06), transparent 20%),
        linear-gradient(180deg, #fcfbff 0%, var(--bg) 100%);
      min-height: 100vh;
    }

    a { color: inherit; text-decoration: none; }

    .container {
      width: min(1120px, 92%);
      margin: 0 auto;
    }

    .topbar {
      position: sticky;
      top: 0;
      z-index: 50;
      backdrop-filter: blur(14px);
      background: rgba(255,255,255,0.82);
      border-bottom: 1px solid var(--border);
    }

    .topbar-inner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 14px 0;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 800;
      font-size: 1.02rem;
    }

    .brand-badge {
      width: 30px;
      height: 30px;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      box-shadow: var(--shadow);
      font-size: 0.95rem;
    }

    .nav-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }

    .btn {
      border: 0;
      border-radius: 16px;
      padding: 12px 18px;
      font-weight: 700;
      cursor: pointer;
      transition: transform .18s ease, box-shadow .18s ease, opacity .18s ease;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-align: center;
    }

    .btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 14px 24px rgba(138,5,190,0.12);
    }

    .btn-primary {
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white;
    }

    .btn-secondary {
      background: rgba(138,5,190,0.05);
      color: var(--text);
      border: 1px solid var(--border);
    }

    .btn-danger {
      background: rgba(214,59,89,0.08);
      color: var(--danger);
      border: 1px solid rgba(214,59,89,0.12);
    }

    .icon-btn {
      width: 44px;
      height: 44px;
      border-radius: 16px;
      background: rgba(138,5,190,0.06);
      border: 1px solid var(--border);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
    }

    .menu-wrap {
      position: relative;
    }

    .menu-dropdown {
      position: absolute;
      right: 0;
      top: calc(100% + 10px);
      width: 230px;
      border-radius: 20px;
      background: white;
      border: 1px solid var(--border);
      box-shadow: 0 20px 40px rgba(138,5,190,0.12);
      padding: 10px;
      display: none;
      z-index: 80;
      animation: fadeUp .22s ease both;
    }

    .menu-wrap:hover .menu-dropdown,
    .menu-wrap:focus-within .menu-dropdown {
      display: block;
    }

    .menu-item {
      display: block;
      padding: 12px 14px;
      border-radius: 12px;
      font-weight: 700;
    }

    .menu-item:hover {
      background: rgba(138,5,190,0.06);
    }

    .hero {
      padding: 60px 0 28px;
    }

    .hero-grid {
      display: grid;
      grid-template-columns: 1.08fr 0.92fr;
      gap: 26px;
      align-items: center;
    }

    .badge {
      display: inline-block;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
      font-size: .9rem;
      margin-bottom: 14px;
    }

    h1 {
      margin: 0 0 14px;
      font-size: clamp(2.3rem, 5vw, 4.5rem);
      line-height: 1.02;
      letter-spacing: -0.03em;
    }

    h2 {
      margin: 0 0 12px;
      font-size: 1.85rem;
    }

    h3 {
      margin: 0 0 8px;
      font-size: 1.15rem;
    }

    .subtitle {
      color: var(--muted);
      line-height: 1.7;
      font-size: 1.04rem;
      margin-bottom: 22px;
      max-width: 58ch;
    }

    .hero-actions {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }

    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius-xl);
      box-shadow: var(--shadow);
      animation: fadeUp .40s ease both;
    }

    .price-card,
    .panel,
    .form-card,
    .auth-card,
    .stat,
    .wallet-card {
      padding: 26px;
    }

    .price-kicker {
      color: var(--accent);
      font-weight: 800;
      font-size: .92rem;
      margin-bottom: 8px;
    }

    .price {
      font-size: clamp(2rem, 4vw, 3.2rem);
      font-weight: 800;
      margin: 4px 0 14px;
      line-height: 1;
    }

    .promo-box {
      margin-top: 16px;
      padding: 16px;
      border-radius: 18px;
      background: rgba(138,5,190,0.04);
      border: 1px solid rgba(138,5,190,0.08);
    }

    .promo-box ul {
      margin: 10px 0 0 18px;
      padding: 0;
      color: var(--muted);
      line-height: 1.7;
    }

    .page-wrap {
      padding: 34px 0 56px;
    }

    .services-title {
      text-align: center;
      margin-bottom: 16px;
    }

    .services-title p {
      color: var(--muted);
      margin: 0;
    }

    .services-scroll {
      display: flex;
      gap: 14px;
      overflow-x: auto;
      padding: 8px 0 12px;
      scrollbar-width: none;
    }

    .services-scroll::-webkit-scrollbar {
      display: none;
    }

    .service-item {
      min-width: 130px;
      padding: 18px 14px;
      border-radius: 22px;
      background: white;
      border: 1px solid var(--border);
      box-shadow: 0 10px 22px rgba(138,5,190,0.08);
      text-align: center;
      transition: transform .18s ease;
      animation: fadeUp .45s ease both;
    }

    .service-item:hover {
      transform: translateY(-3px);
    }

    .service-item .icon {
      font-size: 1.7rem;
      margin-bottom: 8px;
      line-height: 1;
    }

    .service-item span {
      font-weight: 700;
      font-size: .95rem;
    }

    .wallet-grid,
    .stats,
    .grid-2 {
      display: grid;
      gap: 16px;
    }

    .wallet-grid {
      grid-template-columns: repeat(4, minmax(0,1fr));
    }

    .stats {
      grid-template-columns: repeat(4, minmax(0,1fr));
      margin-bottom: 20px;
    }

    .grid-2 {
      grid-template-columns: 1fr 1fr;
    }

    .wallet-label,
    .stat .label {
      color: var(--muted);
      font-size: .92rem;
      margin-bottom: 10px;
    }

    .wallet-amount,
    .stat .value {
      font-size: 1.95rem;
      font-weight: 800;
      line-height: 1;
    }

    .top-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      margin-bottom: 20px;
      flex-wrap: wrap;
    }

    .auth-shell {
      min-height: calc(100vh - 90px);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 26px 0 40px;
    }

    .auth-card {
      width: min(560px, 94vw);
    }

    form {
      display: grid;
      gap: 14px;
    }

    label {
      display: block;
      margin-bottom: 6px;
      font-size: .92rem;
      font-weight: 700;
    }

    input, select, textarea {
      width: 100%;
      padding: 14px 15px;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: white;
      color: var(--text);
      outline: none;
      font-size: 1rem;
    }

    input:focus, select:focus, textarea:focus {
      box-shadow: 0 0 0 4px rgba(138,5,190,0.10);
      border-color: rgba(138,5,190,0.18);
    }

    textarea {
      min-height: 110px;
      resize: vertical;
    }

    .flash-wrap {
      display: grid;
      gap: 10px;
      margin-bottom: 16px;
    }

    .flash {
      padding: 13px 15px;
      border-radius: 16px;
      font-weight: 700;
      border: 1px solid transparent;
      animation: fadeUp .28s ease both;
    }

    .flash-success {
      background: var(--success-bg);
      color: var(--success);
      border-color: rgba(31,157,87,0.16);
    }

    .flash-error {
      background: var(--error-bg);
      color: var(--danger);
      border-color: rgba(214,59,89,0.16);
    }

    .flash-info {
      background: var(--info-bg);
      color: var(--accent);
      border-color: rgba(138,5,190,0.14);
    }

    table {
      width: 100%;
      border-collapse: collapse;
    }

    th, td {
      padding: 14px;
      text-align: left;
      border-bottom: 1px solid rgba(138,5,190,0.08);
      vertical-align: top;
    }

    th {
      font-size: .92rem;
      background: rgba(138,5,190,0.04);
    }

    td {
      color: var(--muted);
      font-size: .98rem;
    }

    .status {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 7px 11px;
      border-radius: 999px;
      font-size: .84rem;
      font-weight: 800;
      white-space: nowrap;
    }

    .status-pendiente {
      background: rgba(245,158,11,0.12);
      color: #9c6100;
      border: 1px solid rgba(245,158,11,0.18);
    }

    .status-procesando {
      background: rgba(138,5,190,0.10);
      color: var(--accent);
      border: 1px solid rgba(138,5,190,0.14);
    }

    .status-completado {
      background: rgba(31,157,87,0.10);
      color: var(--success);
      border: 1px solid rgba(31,157,87,0.16);
    }

    .status-cancelado,
    .status-rechazado {
      background: rgba(214,59,89,0.10);
      color: var(--danger);
      border: 1px solid rgba(214,59,89,0.16);
    }

    .empty {
      padding: 28px;
      text-align: center;
      color: var(--muted);
    }

    .avatar {
      width: 84px;
      height: 84px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.5rem;
      font-weight: 800;
      overflow: hidden;
      box-shadow: var(--shadow);
    }

    .avatar img {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .profile-hero {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 18px;
      align-items: center;
      margin-bottom: 22px;
      padding: 22px;
      border-radius: 24px;
      background: rgba(138,5,190,0.03);
      border: 1px solid rgba(138,5,190,0.08);
    }

    .tag-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(138,5,190,0.08);
      color: var(--accent);
      font-weight: 700;
      font-size: .9rem;
      border: 1px solid rgba(138,5,190,0.10);
    }

    .onboarding-shell {
      min-height: calc(100vh - 90px);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 30px 0 50px;
    }

    .step-card {
      width: min(560px, 94vw);
      padding: 34px 30px;
      border-radius: 30px;
      background: white;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      animation: fadeUp .35s ease both;
    }

    .step-progress {
      width: 100%;
      height: 8px;
      border-radius: 999px;
      background: rgba(138,5,190,0.08);
      overflow: hidden;
      margin-bottom: 26px;
    }

    .step-progress-fill {
      height: 100%;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      border-radius: 999px;
      transition: width .25s ease;
    }

    .step-question {
      font-size: clamp(1.8rem, 4vw, 2.6rem);
      font-weight: 800;
      line-height: 1.08;
      margin-bottom: 12px;
      letter-spacing: -0.03em;
    }

    .step-helper {
      color: var(--muted);
      margin-bottom: 22px;
      line-height: 1.7;
    }

    .step-actions {
      display: flex;
      gap: 12px;
      margin-top: 8px;
      flex-wrap: wrap;
    }

    .footer {
      padding: 26px 0 40px;
      color: var(--muted);
      border-top: 1px solid rgba(138,5,190,0.08);
      margin-top: 10px;
    }

    .loader {
      width: 20px;
      height: 20px;
      border-radius: 50%;
      border: 3px solid rgba(138,5,190,0.16);
      border-top-color: var(--accent);
      animation: spin 1s linear infinite;
      display: inline-block;
      vertical-align: middle;
      margin-right: 8px;
    }

    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(16px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }

    @media (max-width: 980px) {
      .hero-grid, .stats, .wallet-grid, .grid-2 { grid-template-columns: 1fr; }
      .profile-hero { grid-template-columns: 1fr; text-align: center; }
      .profile-hero .avatar { margin: 0 auto; }
    }

    @media (max-width: 740px) {
      .container { width: min(94%, 100%); }
      table, thead, tbody, th, td, tr { display: block; }
      thead { display: none; }
      tr { border-bottom: 1px solid rgba(138,5,190,0.10); padding: 10px 0; }
      td { border-bottom: none; padding: 8px 14px; }
      td::before {
        content: attr(data-label);
        display: block;
        font-size: .82rem;
        color: var(--text);
        font-weight: 800;
        margin-bottom: 4px;
      }
    }

    @media (max-width: 640px) {
      .topbar-inner { align-items: center; }
      .hero-actions .btn { width: 100%; }
      .step-card { padding: 28px 22px; }
      .step-actions { flex-direction: column; }
      .step-actions .btn { width: 100%; }
    }
  </style>
</head>
<body>
  <nav class="topbar">
    <div class="container topbar-inner">
      <div class="brand">
        <a href="{{ url_for('home') }}" style="display:flex; align-items:center; gap:10px;">
          <span class="brand-badge">◉</span>
          <span>Banco Cuba</span>
        </a>
      </div>

      <div class="nav-actions">
        {% if user %}
          {% if user['is_admin'] %}
            <a class="icon-btn" href="{{ url_for('admin_dashboard') }}" title="Dashboard">📊</a>
          {% endif %}
          <div class="menu-wrap">
            <button class="icon-btn" type="button">☰</button>
            <div class="menu-dropdown">
              {% if not user['is_admin'] %}
                <a class="menu-item" href="{{ url_for('profile') }}">Mi perfil</a>
                <a class="menu-item" href="{{ url_for('wallet_page') }}">Mi billetera</a>
                <a class="menu-item" href="{{ url_for('transfer_money') }}">Enviar dinero</a>
                <a class="menu-item" href="{{ url_for('my_orders') }}">Servicios</a>
                <a class="menu-item" href="{{ url_for('referrals_page') }}">Referidos</a>
              {% else %}
                <a class="menu-item" href="{{ url_for('admin_dashboard') }}">Panel admin</a>
              {% endif %}
              <a class="menu-item" href="{{ url_for('forgot_password') }}">Seguridad</a>
              <a class="menu-item" href="{{ url_for('logout') }}">Cerrar sesión</a>
            </div>
          </div>
        {% else %}
          <a class="btn btn-secondary" href="{{ url_for('login') }}">Entrar</a>
          <a class="btn btn-primary" href="{{ url_for('register_step', step=1) }}">Crear cuenta</a>
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


def render_page(content, title="Banco Cuba", user=None, hide_container=False, **context):
    rendered = render_template_string(content, user=user, **context)
    return render_template_string(
        BASE_HTML,
        content=rendered,
        title=title,
        user=user,
        hide_container=hide_container,
    )


@app.route("/")
def home():
    user = current_user()
    promo = get_active_promo()

    content = """
    <header class="hero">
      <div class="container hero-grid">
        <div>
          <div class="badge">Tu dinero, tus servicios, tu control</div>
          <h1>Una billetera digital pensada para Cuba.</h1>
          <p class="subtitle">
            Guarda saldo en CUP, USD y USDT, convierte entre monedas, transfiere por @tag,
            deposita, retira y usa tus fondos para recargas y servicios.
          </p>
          <div class="hero-actions">
            {% if user %}
              {% if user['is_admin'] %}
                <a class="btn btn-primary" href="{{ url_for('admin_dashboard') }}">Abrir panel admin</a>
              {% else %}
                <a class="btn btn-primary" href="{{ url_for('wallet_page') }}">Entrar a mi billetera</a>
                <a class="btn btn-secondary" href="{{ url_for('transfer_money') }}">Enviar dinero</a>
              {% endif %}
            {% else %}
              <a class="btn btn-primary" href="{{ url_for('register_step', step=1) }}">Crear cuenta</a>
              <a class="btn btn-secondary" href="{{ url_for('login') }}">Ya tengo cuenta</a>
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
        </div>
      </div>
    </header>

    <section class="page-wrap" style="padding-top:0;">
      <div class="container">
        <div class="services-title">
          <h2>Todo desde una sola app</h2>
          <p>Funciones principales del banco digital.</p>
        </div>

        <div class="services-scroll">
          <a class="service-item" href="{{ url_for('wallet_page') if user else url_for('login') }}">
            <div class="icon">👛</div>
            <span>Billetera</span>
          </a>
          <a class="service-item" href="{{ url_for('transfer_money') if user else url_for('login') }}">
            <div class="icon">💸</div>
            <span>Transferir</span>
          </a>
          <a class="service-item" href="{{ url_for('new_order') if user else url_for('login') }}">
            <div class="icon">📱</div>
            <span>Recargas</span>
          </a>
          <a class="service-item" href="{{ url_for('new_order') if user else url_for('login') }}">
            <div class="icon">🎁</div>
            <span>Gift Cards</span>
          </a>
        </div>
      </div>
    </section>

    <footer class="footer">
      <div class="container">Banco Cuba · Plataforma digital</div>
    </footer>
    """
    return render_page(content, title="Banco Cuba", user=user, promo=promo)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_file(UPLOAD_DIR / filename)


@app.route("/register")
def register_redirect():
    return redirect(url_for("register_step", step=1))


@app.route("/register/step/<int:step>", methods=["GET", "POST"])
def register_step(step):
    if current_user():
        return redirect(url_for("home"))

    if "register_data" not in session:
        session["register_data"] = {}

    data = session["register_data"]

    field_map = {
        1: "first_name",
        2: "last_name",
        3: "email",
        4: "password",
        5: "carnet",
        6: "city",
        7: "profile_tag",
        8: "referral_code",
    }

    question_map = {
        1: "¿Cuál es tu nombre?",
        2: "¿Cuáles son tus apellidos?",
        3: "¿Cuál es tu correo?",
        4: "Crea tu contraseña",
        5: "¿Cuál es tu número de carnet?",
        6: "¿En qué ciudad vives?",
        7: "Crea tu @tag",
        8: "¿Tienes un código de referido?",
        9: "Confirma tus datos",
    }

    helper_map = {
        1: "Escribe tu nombre real, como aparece en tu documento.",
        2: "Escribe tus apellidos completos.",
        3: "Usaremos tu correo para acceso y seguridad.",
        4: "Debe tener al menos 6 caracteres.",
        5: "Este dato quedará bloqueado después del registro.",
        6: "Selecciona tu ciudad en Cuba.",
        7: "Tu @tag será único dentro de la plataforma.",
        8: "Este paso es opcional. Si no tienes, puedes continuar.",
        9: "Revisa todo antes de crear tu cuenta.",
    }

    if step < 1 or step > 9:
        return redirect(url_for("register_step", step=1))

    if request.method == "POST":
        if step in field_map:
            field = field_map[step]
            value = request.form.get(field, "").strip()

            if field == "profile_tag":
                value = clean_tag(value)
            elif field == "email":
                value = value.lower()
            elif field == "referral_code":
                value = value.upper()

            if step != 8 and not value:
                flash("Completa este campo para continuar.", "error")
                return redirect(url_for("register_step", step=step))

            data[field] = value
            session["register_data"] = data

            return redirect(url_for("register_step", step=step + 1))

        if step == 9:
            first_name = data.get("first_name", "").strip()
            last_name = data.get("last_name", "").strip()
            email = data.get("email", "").strip().lower()
            password = data.get("password", "").strip()
            carnet = data.get("carnet", "").strip()
            city = data.get("city", "").strip()
            profile_tag = clean_tag(data.get("profile_tag", ""))
            referral_code = data.get("referral_code", "").strip().upper()

            if not all([first_name, last_name, email, password, carnet, city, profile_tag]):
                flash("Faltan datos del registro. Vuelve a completar los pasos.", "error")
                return redirect(url_for("register_step", step=1))

            if city not in CITIES_CUBA:
                flash("Selecciona una ciudad válida.", "error")
                return redirect(url_for("register_step", step=6))

            if len(password) < 6:
                flash("La contraseña debe tener al menos 6 caracteres.", "error")
                return redirect(url_for("register_step", step=4))

            conn = get_db()

            email_exists = q(conn, "SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            carnet_exists = q(conn, "SELECT id FROM users WHERE carnet = ?", (carnet,)).fetchone()
            tag_exists = q(conn, "SELECT id FROM users WHERE profile_tag = ?", (profile_tag,)).fetchone()

            if email_exists:
                conn.close()
                flash("Ese correo ya está registrado.", "error")
                return redirect(url_for("register_step", step=3))
            if carnet_exists:
                conn.close()
                flash("Ese carnet ya está registrado.", "error")
                return redirect(url_for("register_step", step=5))
            if tag_exists:
                conn.close()
                flash("Ese @tag ya está en uso.", "error")
                return redirect(url_for("register_step", step=7))

            referred_by_user_id = None
            if referral_code:
                inviter = q(conn, "SELECT id FROM users WHERE referral_code = ?", (referral_code,)).fetchone()
                if inviter:
                    referred_by_user_id = inviter["id"]

            my_ref_code = generate_referral_code()
            while q(conn, "SELECT id FROM users WHERE referral_code = ?", (my_ref_code,)).fetchone():
                my_ref_code = generate_referral_code()

            q(conn, """
                INSERT INTO users (
                    first_name, last_name, carnet, email, password, city,
                    profile_tag, profile_photo, referral_code, referred_by_user_id,
                    is_admin, is_locked, failed_attempts, created_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, 0, 0, 0, ?, '')
            """, (
                first_name,
                last_name,
                carnet,
                email,
                generate_password_hash(password),
                city,
                profile_tag,
                my_ref_code,
                referred_by_user_id,
                now_str(),
            ))

            user_id = q(conn, "SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]

            q(conn, """
                INSERT INTO wallets (user_id, cup_balance, usd_balance, usdt_balance, bonus_usdt_balance, created_at)
                VALUES (?, 0, 0, 0, 0, ?)
            """, (user_id, now_str()))

            if referred_by_user_id:
                reward = parse_float(get_setting("referral_reward_usdt", "0.25"), 0.25)
                required_deposit = parse_float(get_setting("referral_required_deposit_usd", "5"), 5)
                q(conn, """
                    INSERT INTO referrals (
                        inviter_user_id, invited_user_id, reward_usdt, required_deposit_usd,
                        status, activated_at, paid_at, created_at
                    ) VALUES (?, ?, ?, ?, 'pendiente', '', '', ?)
                """, (
                    referred_by_user_id,
                    user_id,
                    reward,
                    required_deposit,
                    now_str(),
                ))

            conn.commit()
            conn.close()

            session.pop("register_data", None)
            session["user_id"] = user_id
            log_action(user_id, "user_registered", "Registro por pasos completado")
            flash("Cuenta creada correctamente.", "success")
            return redirect(url_for("home"))

    progress = int((step / 9) * 100)

    if step == 6:
        input_html = """
        <select name="city" required>
          <option value="">Selecciona tu ciudad</option>
          {% for city in cities %}
            <option value="{{ city }}" {% if data.get('city') == city %}selected{% endif %}>{{ city }}</option>
          {% endfor %}
        </select>
        """
    elif step == 4:
        input_html = '<input type="password" name="password" placeholder="Tu contraseña" required>'
    elif step == 8:
        input_html = '<input type="text" name="referral_code" placeholder="Código opcional">'
    elif step == 3:
        input_html = '<input type="email" name="email" placeholder="tucorreo@email.com" value="{{ data.get(\'email\', \'\') }}" required>'
    elif step == 7:
        input_html = '<input type="text" name="profile_tag" placeholder="@miguel" value="{{ data.get(\'profile_tag\', \'\') }}" required>'
    else:
        field = field_map.get(step, "")
        placeholder = {
            1: "Tu nombre",
            2: "Tus apellidos",
            5: "Tu número de carnet",
        }.get(step, "")
        input_html = f'<input type="text" name="{field}" placeholder="{placeholder}" value="{{{{ data.get(\'{field}\', \'\') }}}}" required>'

    content = """
    <div class="onboarding-shell">
      <div class="step-card">
        <div class="step-progress">
          <div class="step-progress-fill" style="width: {{ progress }}%;"></div>
        </div>

        {% if step < 9 %}
          <div class="step-question">{{ question }}</div>
          <div class="step-helper">{{ helper }}</div>

          <form method="post">
            {{ input_html|safe }}
            <div class="step-actions">
              {% if step > 1 %}
                <a class="btn btn-secondary" href="{{ url_for('register_step', step=step-1) }}">Atrás</a>
              {% endif %}
              <button class="btn btn-primary" type="submit">Continuar</button>
            </div>
          </form>
        {% else %}
          <div class="step-question">{{ question }}</div>
          <div class="step-helper">{{ helper }}</div>

          <div class="card panel" style="margin-bottom:16px; padding:20px;">
            <div><strong>Nombre:</strong> {{ data.get('first_name') }}</div>
            <div><strong>Apellidos:</strong> {{ data.get('last_name') }}</div>
            <div><strong>Correo:</strong> {{ data.get('email') }}</div>
            <div><strong>Carnet:</strong> {{ masked_carnet }}</div>
            <div><strong>Ciudad:</strong> {{ data.get('city') }}</div>
            <div><strong>@tag:</strong> {{ data.get('profile_tag') }}</div>
            <div><strong>Referido:</strong> {{ data.get('referral_code') or 'Ninguno' }}</div>
          </div>

          <form method="post">
            <div class="step-actions">
              <a class="btn btn-secondary" href="{{ url_for('register_step', step=8) }}">Atrás</a>
              <button class="btn btn-primary" type="submit"><span class="loader"></span>Crear cuenta</button>
            </div>
          </form>
        {% endif %}
      </div>
    </div>
    """
    return render_page(
        content,
        title="Crear cuenta",
        user=None,
        hide_container=True,
        step=step,
        question=question_map[step],
        helper=helper_map[step],
        progress=progress,
        input_html=input_html,
        cities=CITIES_CUBA,
        data=data,
        masked_carnet=card_mask(data.get("carnet", "")),
    )

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
            flash("Tu cuenta está bloqueada. Contacta soporte o solicita recuperación.", "error")
        elif not check_password_hash(user["password"], password):
            failed = int(user["failed_attempts"]) + 1
            is_locked = 1 if failed >= 5 else 0
            q(conn, "UPDATE users SET failed_attempts = ?, is_locked = ? WHERE id = ?", (failed, is_locked, user["id"]))
            conn.commit()
            conn.close()
            flash("Correo o contraseña incorrectos.", "error")
        else:
            q(
                conn,
                "UPDATE users SET failed_attempts = 0, is_locked = 0, last_login_at = ? WHERE id = ?",
                (now_str(), user["id"]),
            )
            conn.commit()
            conn.close()

            session["user_id"] = user["id"]
            log_action(user["id"], "user_login", "Inicio de sesión correcto")
            flash("Sesión iniciada correctamente.", "success")

            if user["is_admin"]:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("wallet_page"))

    content = """
    <div class="auth-shell">
      <div class="auth-card card">
        <div class="badge">Accede a tu cuenta</div>
        <h2>Entrar</h2>
        <p class="subtitle" style="margin-top:0;">
          Consulta tu saldo, transfiere, deposita, retira y usa tus servicios desde una sola app.
        </p>

        <form method="post">
          <div>
            <label>Correo electrónico</label>
            <input type="email" name="email" placeholder="tucorreo@email.com" required>
          </div>

          <div>
            <label>Contraseña</label>
            <input type="password" name="password" placeholder="Tu contraseña" required>
          </div>

          <button class="btn btn-primary" type="submit">
            <span class="loader"></span>Entrar
          </button>
        </form>

        <p class="subtitle" style="font-size:1rem; margin:16px 0 0;">
          ¿No tienes cuenta? <a href="{{ url_for('register_step', step=1) }}"><strong>Créala aquí</strong></a><br>
          <a href="{{ url_for('forgot_password') }}"><strong>¿Olvidaste tu contraseña?</strong></a>
        </p>
      </div>
    </div>
    """
    return render_page(content, title="Entrar", user=None, hide_container=True)


@app.route("/logout")
def logout():
    user = current_user()
    if user:
        log_action(user["id"], "user_logout", "Cierre de sesión")
    session.clear()
    flash("Has cerrado sesión.", "info")
    return redirect(url_for("home"))


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
      <div class="auth-card card">
        <div class="badge">Seguridad</div>
        <h2>Recuperar contraseña</h2>
        <p class="subtitle" style="margin-top:0;">
          Escribe tu correo y una nota opcional. Nuestro equipo revisará tu solicitud.
        </p>

        <form method="post">
          <div>
            <label>Correo electrónico</label>
            <input type="email" name="email" placeholder="tucorreo@email.com" required>
          </div>

          <div>
            <label>Nota opcional</label>
            <textarea name="note" placeholder="Ej: olvidé mi contraseña"></textarea>
          </div>

          <button class="btn btn-primary" type="submit">Enviar solicitud</button>
        </form>
      </div>
    </div>
    """
    return render_page(content, title="Recuperar contraseña", user=None, hide_container=True)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()

    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        photo = request.files.get("profile_photo")
        city = request.form.get("city", "").strip()
        photo_path = user["profile_photo"]

        if city not in CITIES_CUBA:
            flash("Selecciona una ciudad válida.", "error")
            return redirect(url_for("profile"))

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
        q(conn, "UPDATE users SET city = ?, profile_photo = ? WHERE id = ?", (city, photo_path, user["id"]))
        conn.commit()
        conn.close()

        log_action(user["id"], "profile_updated", "Actualizó ciudad o foto de perfil")
        flash("Perfil actualizado correctamente.", "success")
        return redirect(url_for("profile"))

    wallet = get_wallet(user["id"])
    profile_photo_url = url_for("uploaded_file", filename=os.path.basename(user["profile_photo"])) if user["profile_photo"] else None

    content = """
    <div class="page-wrap">
      <div class="container" style="max-width:1000px;">
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
            <p class="subtitle" style="font-size:.95rem; margin-top:10px; margin-bottom:0;">
              Código de referido: <strong>{{ user['referral_code'] }}</strong>
            </p>
          </div>

          <a class="btn btn-secondary" href="{{ url_for('wallet_page') }}">Ver billetera</a>
        </div>

        <div class="grid-2">
          <div class="card panel">
            <h3>Datos personales</h3>
            <div style="display:grid; gap:14px;">
              <div>
                <label>Nombre</label>
                <input value="{{ user['first_name'] }}" disabled>
              </div>

              <div>
                <label>Apellidos</label>
                <input value="{{ user['last_name'] }}" disabled>
              </div>

              <div>
                <label>Número de carnet</label>
                <input value="{{ masked_carnet }}" disabled>
              </div>

              <div>
                <label>@tag</label>
                <input value="{{ user['profile_tag'] }}" disabled>
              </div>

              <div class="promo-box" style="margin-top:0;">
                <strong>Datos bloqueados</strong>
                <ul>
                  <li>Nombre, apellidos, carnet y @tag no se pueden editar después del registro.</li>
                  <li>Esto protege la seguridad e integridad de la cuenta.</li>
                </ul>
              </div>
            </div>
          </div>

          <div class="card panel">
            <h3>Actualizar perfil visible</h3>
            <form method="post" enctype="multipart/form-data">
              <div>
                <label>Ciudad</label>
                <select name="city" required>
                  {% for city in cities %}
                    <option value="{{ city }}" {% if user['city'] == city %}selected{% endif %}>{{ city }}</option>
                  {% endfor %}
                </select>
              </div>

              <div>
                <label>Foto de perfil</label>
                <input type="file" name="profile_photo">
              </div>

              <button class="btn btn-primary" type="submit">Guardar cambios</button>
            </form>

            <div class="wallet-grid" style="margin-top:18px; grid-template-columns:repeat(2,minmax(0,1fr));">
              <div class="card wallet-card">
                <div class="wallet-label">USD</div>
                <div class="wallet-amount">{{ '%.2f'|format(wallet['usd_balance']) }}</div>
              </div>
              <div class="card wallet-card">
                <div class="wallet-label">USDT</div>
                <div class="wallet-amount">{{ '%.2f'|format(wallet['usdt_balance']) }}</div>
              </div>
              <div class="card wallet-card">
                <div class="wallet-label">CUP</div>
                <div class="wallet-amount">{{ '%.2f'|format(wallet['cup_balance']) }}</div>
              </div>
              <div class="card wallet-card">
                <div class="wallet-label">Bonus USDT</div>
                <div class="wallet-amount">{{ '%.2f'|format(wallet['bonus_usdt_balance']) }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    """
    return render_page(
        content,
        title="Mi perfil",
        user=user,
        wallet=wallet,
        profile_photo_url=profile_photo_url,
        cities=CITIES_CUBA,
        masked_carnet=card_mask(user["carnet"]),
    )

