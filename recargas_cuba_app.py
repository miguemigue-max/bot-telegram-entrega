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
DB_PATH = BASE_DIR / "banco_cuba_v2.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

CITIES_CUBA = [
    "Pinar del Río", "Artemisa", "La Habana", "Mayabeque", "Matanzas",
    "Cienfuegos", "Villa Clara", "Sancti Spíritus", "Ciego de Ávila",
    "Camagüey", "Las Tunas", "Holguín", "Granma", "Santiago de Cuba",
    "Guantánamo", "Isla de la Juventud"
]

DEPOSIT_METHODS = ["Cripto", "Paypal", "Tarjeta CUP", "PIX Brasil"]
WITHDRAW_METHODS = ["Cripto", "Paypal", "Tarjeta CUP", "PIX Brasil"]


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


def mask_carnet(carnet: str):
    carnet = (carnet or "").strip()
    if len(carnet) <= 4:
        return "*" * len(carnet)
    return "*" * (len(carnet) - 4) + carnet[-4:]


def get_settings():
    conn = get_db()
    rows = q(conn, "SELECT key, value FROM settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def get_setting(key: str, default=None):
    return get_settings().get(key, default)


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    user = q(conn, "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def wallet_field(currency):
    return {
        "CUP": "cup_balance",
        "USD": "usd_balance",
        "USDT": "usdt_balance",
        "BONUS_USDT": "bonus_usdt_balance",
    }[currency]


def ensure_wallet(user_id):
    conn = get_db()
    exists = q(conn, "SELECT user_id FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    if not exists:
        q(conn, """
            INSERT INTO wallets (
                user_id, cup_balance, usd_balance, usdt_balance, bonus_usdt_balance, created_at
            ) VALUES (?, 0, 0, 0, 0, ?)
        """, (user_id, now_str()))
        conn.commit()
    conn.close()


def get_wallet(user_id):
    ensure_wallet(user_id)
    conn = get_db()
    wallet = q(conn, "SELECT * FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return wallet


def can_debit_wallet(user_id, currency, amount):
    wallet = get_wallet(user_id)
    field = wallet_field(currency)
    return float(wallet[field]) >= float(amount)


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
    field = wallet_field(currency)
    sign = 1 if direction == "credit" else -1
    conn = get_db()
    q(conn, f"UPDATE wallets SET {field} = {field} + ? WHERE user_id = ?", (sign * amount, user_id))
    conn.commit()
    conn.close()
    add_wallet_tx(user_id, currency, amount, direction, tx_type, description, reference)


def log_action(actor_user_id, action, details=""):
    conn = get_db()
    q(conn, """
        INSERT INTO audit_logs (actor_user_id, action, details, created_at)
        VALUES (?, ?, ?, ?)
    """, (actor_user_id, action, details, now_str()))
    conn.commit()
    conn.close()


def activate_referral_if_needed(user_id, deposit_amount_usd):
    settings = get_settings()
    required_deposit = parse_float(settings.get("referral_required_deposit_usd", "5"), 5)
    reward = parse_float(settings.get("referral_reward_usdt", "0.25"), 0.25)

    if deposit_amount_usd < required_deposit:
        return

    conn = get_db()
    referral = q(conn, """
        SELECT * FROM referrals
        WHERE invited_user_id = ? AND status = 'pendiente'
        ORDER BY id DESC LIMIT 1
    """, (user_id,)).fetchone()

    if not referral:
        conn.close()
        return

    q(conn, """
        UPDATE referrals
        SET status = 'activado', activated_at = ?
        WHERE id = ?
    """, (now_str(), referral["id"]))
    conn.commit()
    conn.close()

    adjust_wallet(
        referral["inviter_user_id"],
        "BONUS_USDT",
        reward,
        "Bonus de referido activado",
        "credit",
        "referral_bonus"
    )


def total_usd_equivalent(wallet):
    usdt_to_usd = parse_float(get_setting("usdt_to_usd", "1"), 1)
    usd = float(wallet["usd_balance"])
    usdt = float(wallet["usdt_balance"]) * usdt_to_usd
    return usd + usdt


def generate_receipt_pdf(title_text, lines):
    if not REPORTLAB_AVAILABLE:
        return None

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    y = height - 60

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(50, y, title_text)
    y -= 30

    pdf.setFont("Helvetica", 11)
    for line in lines:
        pdf.drawString(50, y, str(line))
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
            destination TEXT NOT NULL DEFAULT '',
            payout_amount REAL NOT NULL DEFAULT 0,
            payout_currency TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Pendiente',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
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
        "usd_to_usdt": "1.00",
        "usdt_to_usd": "1.00",
        "referral_reward_usdt": "0.25",
        "referral_required_deposit_usd": "5",
        "bonus_withdraw_min_usdt": "1",
    }

    for key, value in defaults.items():
        if not q(conn, "SELECT key FROM settings WHERE key = ?", (key,)).fetchone():
            q(conn, "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    admin_email = "admin@bancocuba.local"
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

    conn.commit()
    conn.close()
