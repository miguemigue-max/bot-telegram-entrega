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

BASE_HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root{
      --bg:#070b18;
      --bg-2:#0b1020;
      --card:#11182c;
      --card-2:#171f37;
      --text:#f8f8fb;
      --muted:#b7b9c9;
      --accent:#7c5cff;
      --accent-2:#9b6dff;
      --line:rgba(255,255,255,0.06);
      --ok:#34c759;
      --danger:#ff5c7a;
      --shadow:0 20px 40px rgba(0,0,0,0.28);
      --radius-xl:28px;
      --radius-lg:22px;
      --radius-md:18px;
    }

    *{box-sizing:border-box}
    html,body{margin:0;padding:0}
    body{
      font-family: Inter, Arial, Helvetica, sans-serif;
      color:var(--text);
      background:
        radial-gradient(circle at 20% 0%, rgba(124,92,255,0.18), transparent 24%),
        radial-gradient(circle at 80% 0%, rgba(0,155,255,0.16), transparent 28%),
        linear-gradient(180deg, var(--bg) 0%, var(--bg-2) 100%);
      min-height:100vh;
    }

    a{color:inherit;text-decoration:none}
    .container{width:min(1100px, 92%);margin:0 auto}

    .topbar{
      position:sticky;top:0;z-index:30;
      backdrop-filter:blur(18px);
      background:rgba(7,11,24,0.72);
      border-bottom:1px solid var(--line);
    }

    .topbar-inner{
      display:flex;align-items:center;justify-content:space-between;
      gap:14px;padding:16px 0;
    }

    .brand{
      display:flex;align-items:center;gap:12px;font-weight:800;font-size:1.02rem;
    }

    .brand-mark{
      width:34px;height:34px;border-radius:14px;
      display:inline-flex;align-items:center;justify-content:center;
      background:linear-gradient(135deg,var(--accent),var(--accent-2));
      box-shadow:0 14px 28px rgba(124,92,255,0.25);
      font-size:1rem;
    }

    .nav-actions{
      display:flex;align-items:center;gap:10px;flex-wrap:wrap;
    }

    .btn{
      border:0;
      border-radius:18px;
      padding:12px 18px;
      font-weight:800;
      cursor:pointer;
      display:inline-flex;align-items:center;justify-content:center;
      transition:transform .18s ease, opacity .18s ease;
    }

    .btn:hover{transform:translateY(-2px)}
    .btn-primary{
      color:white;
      background:linear-gradient(135deg,var(--accent),var(--accent-2));
      box-shadow:0 16px 30px rgba(124,92,255,0.24);
    }
    .btn-secondary{
      color:white;
      background:rgba(255,255,255,0.05);
      border:1px solid rgba(255,255,255,0.08);
    }
    .btn-danger{
      color:#ffd7df;
      background:rgba(255,92,122,0.10);
      border:1px solid rgba(255,92,122,0.12);
    }

    .icon-btn{
      width:46px;height:46px;border-radius:16px;
      display:inline-flex;align-items:center;justify-content:center;
      color:white;
      background:rgba(255,255,255,0.05);
      border:1px solid rgba(255,255,255,0.08);
      cursor:pointer;
    }

    .menu-wrap{position:relative}
    .menu-dropdown{
      position:absolute;right:0;top:calc(100% + 10px);
      width:230px;
      border-radius:20px;
      background:rgba(17,24,44,0.98);
      border:1px solid rgba(255,255,255,0.08);
      box-shadow:var(--shadow);
      padding:10px;
      display:none;
      z-index:60;
    }
    .menu-wrap:hover .menu-dropdown,
    .menu-wrap:focus-within .menu-dropdown{
      display:block;
    }

    .menu-item{
      display:block;
      padding:12px 14px;
      border-radius:14px;
      font-weight:700;
      color:var(--text);
    }
    .menu-item:hover{background:rgba(255,255,255,0.06)}

    .flash-wrap{display:grid;gap:10px;margin:18px 0}
    .flash{
      padding:14px 16px;border-radius:16px;font-weight:800;
      border:1px solid rgba(255,255,255,0.08);
    }
    .flash-success{background:rgba(52,199,89,0.14);color:#aaf0bf}
    .flash-error{background:rgba(255,92,122,0.12);color:#ffd1da}
    .flash-info{background:rgba(124,92,255,0.16);color:#e3dcff}

    .hero{
      padding:40px 0 32px;
      position:relative;
      overflow:hidden;
    }

    .hero-grid{
      display:grid;
      grid-template-columns:1.04fr 0.96fr;
      gap:24px;
      align-items:center;
    }

    .hero-badge{
      display:inline-flex;align-items:center;gap:10px;
      padding:10px 16px;border-radius:999px;
      background:rgba(124,92,255,0.10);
      border:1px solid rgba(124,92,255,0.18);
      color:#cdbfff;font-weight:800;font-size:.95rem;
      margin-bottom:18px;
    }

    .hero-title{
      margin:0 0 18px;
      font-size:clamp(2.7rem, 7vw, 5.2rem);
      line-height:0.96;
      letter-spacing:-0.05em;
      font-weight:900;
    }

    .hero-subtitle{
      margin:0 0 24px;
      color:var(--muted);
      font-size:1.14rem;
      line-height:1.75;
      max-width:60ch;
    }

    .hero-actions{
      display:flex;gap:14px;flex-wrap:wrap;
    }

    .hero-card,
    .panel,
    .auth-card,
    .step-card,
    .wallet-box,
    .stat-card,
    .tx-card{
      background:linear-gradient(180deg, rgba(23,31,55,0.96), rgba(17,24,44,0.98));
      border:1px solid rgba(255,255,255,0.07);
      box-shadow:var(--shadow);
      border-radius:var(--radius-xl);
    }

    .hero-card{padding:24px}

    .hero-figure{
      min-height:520px;
      position:relative;
      overflow:hidden;
    }

    .float-chip{
      position:absolute;
      border-radius:999px;
      padding:10px 16px;
      background:rgba(124,92,255,0.10);
      border:1px solid rgba(124,92,255,0.15);
      color:#cdbfff;font-weight:800;
      animation:floaty 4s ease-in-out infinite;
    }

    .coin{
      position:absolute;
      width:88px;height:88px;border-radius:50%;
      display:flex;align-items:center;justify-content:center;
      font-size:2rem;
      background:rgba(255,255,255,0.08);
      border:1px solid rgba(255,255,255,0.08);
      animation:floaty 5s ease-in-out infinite;
    }

    .hero-figure-title{
      position:absolute;left:34px;right:34px;top:120px;
      font-size:clamp(2.6rem, 8vw, 5.1rem);
      line-height:0.98;
      letter-spacing:-0.05em;
      font-weight:900;
    }

    .gradient-word{
      background:linear-gradient(90deg,#ffcb45,#ff8d2f,#ff5b57);
      -webkit-background-clip:text;
      background-clip:text;
      color:transparent;
    }

    .under-line{
      display:block;
      width:220px;height:10px;border-radius:999px;
      margin-top:14px;
      background:linear-gradient(90deg,#ffcb45,#ff8d2f,#ff5b57);
    }

    .hero-desc{
      position:absolute;left:34px;right:34px;bottom:42px;
      color:var(--muted);font-size:1.05rem;line-height:1.8;
    }

    .page-wrap{padding:34px 0 54px}
    .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
    .panel{padding:24px}
    .panel h2,.panel h3{margin:0 0 10px}
    .subtitle{color:var(--muted);line-height:1.7}

    .wallet-hero{
      padding:18px 0 10px;
    }

    .wallet-top{
      display:flex;align-items:center;justify-content:space-between;
      gap:14px;margin-bottom:16px;
    }

    .wallet-balance{
      font-size:4rem;font-weight:900;line-height:1;letter-spacing:-0.04em;
    }

    .quick-actions{
      display:flex;gap:12px;flex-wrap:wrap;
    }

    .quick-card{
      flex:1;
      min-width:120px;
      padding:18px;
      border-radius:22px;
      background:rgba(255,255,255,0.04);
      border:1px solid rgba(255,255,255,0.07);
      text-align:center;
      font-weight:800;
    }

    .wallet-grid{
      display:grid;
      grid-template-columns:repeat(4,minmax(0,1fr));
      gap:16px;
      margin-top:20px;
    }

    .wallet-box{
      padding:22px;
    }

    .wallet-label{
      color:var(--muted);
      font-size:1rem;
      margin-bottom:14px;
    }

    .wallet-amount{
      font-size:2rem;
      font-weight:900;
      line-height:1;
    }

    .section-title{
      display:flex;align-items:end;justify-content:space-between;
      gap:14px;margin:28px 0 16px;
    }

    .tx-list{
      display:grid;gap:14px;
    }

    .tx-card{
      padding:18px 20px;
      display:flex;align-items:center;justify-content:space-between;gap:16px;
    }

    .tx-left{display:flex;gap:14px;align-items:center}
    .tx-icon{
      width:54px;height:54px;border-radius:18px;
      display:flex;align-items:center;justify-content:center;
      background:rgba(124,92,255,0.12);
      font-size:1.3rem;
    }

    .tx-title{font-size:1.15rem;font-weight:900}
    .tx-sub{color:var(--muted);margin-top:4px}
    .tx-amount{font-size:1.4rem;font-weight:900}
    .tx-plus{color:#9af0af}
    .tx-minus{color:#ffd2d9}

    .auth-shell,.onboarding-shell{
      min-height:calc(100vh - 85px);
      display:flex;align-items:center;justify-content:center;
      padding:28px 0 44px;
    }

    .auth-card,.step-card{
      width:min(560px,94vw);
      padding:28px;
    }

    .step-progress{
      width:100%;height:10px;border-radius:999px;
      background:rgba(255,255,255,0.08);
      overflow:hidden;margin-bottom:24px;
    }

    .step-progress-fill{
      height:100%;
      background:linear-gradient(90deg,var(--accent),var(--accent-2));
      border-radius:999px;
    }

    .step-question{
      font-size:clamp(2rem,5vw,3rem);
      line-height:1.03;
      letter-spacing:-0.04em;
      font-weight:900;
      margin:0 0 14px;
    }

    .step-helper{
      color:var(--muted);
      line-height:1.7;
      margin-bottom:20px;
      font-size:1.05rem;
    }

    form{display:grid;gap:14px}
    label{font-size:.92rem;font-weight:800;margin-bottom:6px;display:block}
    input,select,textarea{
      width:100%;
      border-radius:18px;
      border:1px solid rgba(255,255,255,0.08);
      background:rgba(255,255,255,0.04);
      color:white;
      padding:15px 16px;
      font-size:1rem;
      outline:none;
    }
    input::placeholder,textarea::placeholder{color:#9da3b7}
    input:focus,select:focus,textarea:focus{
      border-color:rgba(124,92,255,0.45);
      box-shadow:0 0 0 4px rgba(124,92,255,0.12);
    }
    textarea{min-height:110px;resize:vertical}

    table{width:100%;border-collapse:collapse}
    th,td{
      padding:14px;
      text-align:left;
      border-bottom:1px solid rgba(255,255,255,0.06);
      vertical-align:top;
    }
    th{color:#d8d9e6;font-size:.92rem}
    td{color:var(--muted)}
    .empty{padding:22px;color:var(--muted);text-align:center}

    .status{
      display:inline-flex;align-items:center;justify-content:center;
      padding:7px 12px;border-radius:999px;font-size:.84rem;font-weight:900;
      border:1px solid rgba(255,255,255,0.07);
    }
    .status-pendiente{background:rgba(255,178,0,0.12);color:#ffd788}
    .status-activado,.status-completado,.status-aprobado{background:rgba(52,199,89,0.12);color:#abefbe}
    .status-rechazado,.status-cancelado{background:rgba(255,92,122,0.12);color:#ffd0d8}

    .footer{
      padding:28px 0 44px;
      color:var(--muted);
      border-top:1px solid rgba(255,255,255,0.05);
      margin-top:10px;
    }

    @keyframes floaty{
      0%,100%{transform:translateY(0)}
      50%{transform:translateY(-8px)}
    }

    @media (max-width:980px){
      .hero-grid,.grid-2,.wallet-grid{grid-template-columns:1fr}
      .hero-figure{min-height:460px}
    }

    @media (max-width:740px){
      .container{width:min(94%,100%)}
      .wallet-balance{font-size:3.2rem}
      .topbar-inner{padding:14px 0}
      table,thead,tbody,th,td,tr{display:block}
      thead{display:none}
      tr{border-bottom:1px solid rgba(255,255,255,0.06);padding:10px 0}
      td{border-bottom:none;padding:8px 14px}
      td::before{
        content:attr(data-label);
        display:block;
        color:#f1f1f8;
        font-size:.82rem;
        font-weight:900;
        margin-bottom:4px;
      }
    }

    @media (max-width:640px){
      .hero-actions .btn,
      .quick-actions .quick-card{width:100%}
      .wallet-top{flex-direction:column;align-items:flex-start}
      .hero-figure-title{top:110px}
      .hero-desc{bottom:30px}
    }
  </style>
</head>
<body>
  <nav class="topbar">
    <div class="container topbar-inner">
      <div class="brand">
        <a href="{{ url_for('home') }}" style="display:flex;align-items:center;gap:12px;">
          <span class="brand-mark">◉</span>
          <span>Banco Cuba</span>
        </a>
      </div>

      <div class="nav-actions">
        {% if user %}
          <div class="menu-wrap">
            <button class="icon-btn" type="button">⋯</button>
            <div class="menu-dropdown">
              {% if user['is_admin'] %}
                <a class="menu-item" href="{{ url_for('admin_dashboard') }}">Panel admin</a>
                <a class="menu-item" href="{{ url_for('admin_settings') }}">Configuración</a>
              {% else %}
                <a class="menu-item" href="{{ url_for('wallet_page') }}">Inicio</a>
                <a class="menu-item" href="{{ url_for('profile') }}">Mi perfil</a>
                <a class="menu-item" href="{{ url_for('transfer_money') }}">Enviar dinero</a>
                <a class="menu-item" href="{{ url_for('deposit_page') }}">Depositar</a>
                <a class="menu-item" href="{{ url_for('withdraw_page') }}">Retirar</a>
                <a class="menu-item" href="{{ url_for('convert_page') }}">Convertir</a>
                <a class="menu-item" href="{{ url_for('referrals_page') }}">Referidos</a>
              {% endif %}
              <a class="menu-item" href="{{ url_for('forgot_password') }}">Seguridad</a>
              <a class="menu-item" href="{{ url_for('logout') }}">Cerrar sesión</a>
            </div>
          </div>
        {% else %}
          <div class="menu-wrap">
            <button class="icon-btn" type="button">⋯</button>
            <div class="menu-dropdown">
              <a class="menu-item" href="{{ url_for('login') }}">Entrar</a>
              <a class="menu-item" href="{{ url_for('register_step', step=1) }}">Crear cuenta</a>
            </div>
          </div>
        {% endif %}
      </div>
    </div>
  </nav>

  <div class="container">
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        <div class="flash-wrap">
          {% for category, message in messages %}
            <div class="flash flash-{{ category }}">{{ message }}</div>
          {% endfor %}
        </div>
      {% endif %}
    {% endwith %}
  </div>

  {{ content|safe }}
</body>
</html>
"""


def render_page(content, title="Banco Cuba", user=None, **context):
    rendered = render_template_string(content, user=user, **context)
    return render_template_string(
        BASE_HTML,
        content=rendered,
        title=title,
        user=user
    )


@app.route("/")
def home():
    user = current_user()

    if user and not user["is_admin"]:
        wallet = get_wallet(user["id"])

        conn = get_db()
        txs = q(conn, """
            SELECT * FROM wallet_transactions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 6
        """, (user["id"],)).fetchall()
        conn.close()

        content = """
        <section class="page-wrap wallet-hero">
          <div class="container">
            <div class="wallet-top">
              <div>
                <div class="subtitle" style="margin:0 0 8px;">Saldo total</div>
                <div class="wallet-balance">${{ "%.2f"|format(total_balance) }}</div>
              </div>
              <div class="quick-actions">
                <a class="quick-card" href="{{ url_for('transfer_money') }}">Enviar</a>
                <a class="quick-card" href="{{ url_for('deposit_page') }}">Depositar</a>
                <a class="quick-card" href="{{ url_for('withdraw_page') }}">Retirar</a>
              </div>
            </div>

            <div class="wallet-grid">
              <div class="wallet-box">
                <div class="wallet-label">USD</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["usd_balance"]) }}</div>
              </div>
              <div class="wallet-box">
                <div class="wallet-label">USDT</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["usdt_balance"]) }}</div>
              </div>
              <div class="wallet-box">
                <div class="wallet-label">CUP</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["cup_balance"]) }}</div>
              </div>
              <div class="wallet-box">
                <div class="wallet-label">Bonus USDT</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["bonus_usdt_balance"]) }}</div>
              </div>
            </div>

            <div class="section-title">
              <div>
                <h2 style="margin:0 0 6px;">Últimas transacciones</h2>
                <div class="subtitle">Actividad reciente de tu cuenta.</div>
              </div>
              <a href="{{ url_for('wallet_page') }}" class="subtitle" style="font-weight:800;">Ver todas</a>
            </div>

            <div class="tx-list">
              {% if txs %}
                {% for tx in txs %}
                <div class="tx-card">
                  <div class="tx-left">
                    <div class="tx-icon">↔</div>
                    <div>
                      <div class="tx-title">{{ tx["description"] }}</div>
                      <div class="tx-sub">{{ tx["currency"] }} · {{ tx["created_at"] }}</div>
                    </div>
                  </div>
                  <div class="tx-amount {% if tx['direction']=='credit' %}tx-plus{% else %}tx-minus{% endif %}">
                    {% if tx['direction']=='credit' %}+{% else %}-{% endif %}{{ "%.2f"|format(tx["amount"]) }}
                  </div>
                </div>
                {% endfor %}
              {% else %}
                <div class="tx-card">
                  <div class="tx-left">
                    <div class="tx-icon">◎</div>
                    <div>
                      <div class="tx-title">Sin movimientos todavía</div>
                      <div class="tx-sub">Tu actividad aparecerá aquí.</div>
                    </div>
                  </div>
                </div>
              {% endif %}
            </div>
          </div>
        </section>
        """
        return render_page(
            content,
            title="Inicio",
            user=user,
            wallet=wallet,
            txs=txs,
            total_balance=total_usd_equivalent(wallet)
        )

    if user and user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    content = """
    <section class="hero">
      <div class="container hero-grid">
        <div>
          <div class="hero-badge">● Nuevo: cuenta digital para Cuba</div>
          <h1 class="hero-title">Tu cuenta digital<br>en <span class="gradient-word">dólares</span></h1>
          <p class="hero-subtitle">
            Guarda saldo en USD, USDT y CUP. Deposita, retira, convierte y transfiere dinero
            desde una sola cuenta digital pensada para Cuba.
          </p>
          <div class="hero-actions">
            <a class="btn btn-primary" href="{{ url_for('register_step', step=1) }}">Crear cuenta gratis →</a>
            <a class="btn btn-secondary" href="{{ url_for('login') }}">Entrar →</a>
          </div>
        </div>

        <div class="hero-card hero-figure">
          <div class="float-chip" style="left:24px;top:24px;">Nuevo: cuenta digital y P2P</div>
          <div class="coin" style="left:18px;top:110px;">₿</div>
          <div class="coin" style="right:22px;top:160px;">◇</div>
          <div class="coin" style="right:36px;bottom:54px;">₮</div>

          <div class="hero-figure-title">
            Tu cuenta<br>digital<br>en <span class="gradient-word">dólares</span>
            <span class="under-line"></span>
          </div>

          <div class="hero-desc">
            Compra, vende e intercambia USD, USDT y CUP.
            Transfiere saldo entre usuarios y maneja tu dinero desde un solo lugar.
          </div>
        </div>
      </div>
    </section>

    <div class="footer">
      <div class="container">Banco Cuba · Cuenta digital y pagos</div>
    </div>
    """
    return render_page(content, title="Banco Cuba", user=None)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_file(UPLOAD_DIR / filename)


@app.route("/wallet")
@login_required
def wallet_page():
    return redirect(url_for("home"))

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
            flash("Tu cuenta está bloqueada. Solicita recuperación.", "error")
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
      <div class="auth-card panel">
        <h2 style="margin:0 0 10px;">Entrar</h2>
        <p class="subtitle" style="margin:0 0 18px;">
          Accede a tu cuenta digital para gestionar saldo, depósitos, retiros y transferencias.
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

          <button class="btn btn-primary" type="submit">Entrar</button>
        </form>

        <div class="subtitle" style="margin-top:16px;">
          ¿No tienes cuenta? <a href="{{ url_for('register_step', step=1) }}" style="font-weight:800;color:#fff;">Crea una</a><br>
          <a href="{{ url_for('forgot_password') }}" style="font-weight:800;color:#fff;">¿Olvidaste tu contraseña?</a>
        </div>
      </div>
    </div>
    """
    return render_page(content, title="Entrar", user=None)


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

        if not email:
            flash("Escribe tu correo.", "error")
        else:
            flash("Solicitud enviada. Un administrador revisará tu caso.", "success")
            return redirect(url_for("login"))

    content = """
    <div class="auth-shell">
      <div class="auth-card panel">
        <h2 style="margin:0 0 10px;">Recuperar contraseña</h2>
        <p class="subtitle" style="margin:0 0 18px;">
          Escribe tu correo para solicitar recuperación de acceso.
        </p>

        <form method="post">
          <div>
            <label>Correo electrónico</label>
            <input type="email" name="email" placeholder="tucorreo@email.com" required>
          </div>

          <button class="btn btn-primary" type="submit">Enviar solicitud</button>
        </form>
      </div>
    </div>
    """
    return render_page(content, title="Recuperar contraseña", user=None)


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
                flash("Faltan datos del registro.", "error")
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
            log_action(user_id, "user_registered", "Registro completado")
            flash("Cuenta creada correctamente.", "success")
            return redirect(url_for("home"))

    progress = int((step / 9) * 100)

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
            {% if step == 1 %}
              <input type="text" name="first_name" placeholder="Tu nombre" value="{{ data.get('first_name', '') }}" required>
            {% elif step == 2 %}
              <input type="text" name="last_name" placeholder="Tus apellidos" value="{{ data.get('last_name', '') }}" required>
            {% elif step == 3 %}
              <input type="email" name="email" placeholder="tucorreo@email.com" value="{{ data.get('email', '') }}" required>
            {% elif step == 4 %}
              <input type="password" name="password" placeholder="Tu contraseña" required>
            {% elif step == 5 %}
              <input type="text" name="carnet" placeholder="Tu número de carnet" value="{{ data.get('carnet', '') }}" required>
            {% elif step == 6 %}
              <select name="city" required>
                <option value="">Selecciona tu ciudad</option>
                {% for city in cities %}
                  <option value="{{ city }}" {% if data.get('city') == city %}selected{% endif %}>{{ city }}</option>
                {% endfor %}
              </select>
            {% elif step == 7 %}
              <input type="text" name="profile_tag" placeholder="@miguel" value="{{ data.get('profile_tag', '') }}" required>
            {% elif step == 8 %}
              <input type="text" name="referral_code" placeholder="Código opcional" value="{{ data.get('referral_code', '') }}">
            {% endif %}

            <div style="display:grid;gap:12px;">
              {% if step > 1 %}
                <a class="btn btn-secondary" href="{{ url_for('register_step', step=step-1) }}">Atrás</a>
              {% endif %}
              <button class="btn btn-primary" type="submit">Continuar</button>
            </div>
          </form>
        {% else %}
          <div class="step-question">{{ question }}</div>
          <div class="step-helper">{{ helper }}</div>

          <div class="panel" style="padding:20px;margin-bottom:16px;">
            <div><strong>Nombre:</strong> {{ data.get('first_name') }}</div>
            <div><strong>Apellidos:</strong> {{ data.get('last_name') }}</div>
            <div><strong>Correo:</strong> {{ data.get('email') }}</div>
            <div><strong>Carnet:</strong> {{ masked_carnet }}</div>
            <div><strong>Ciudad:</strong> {{ data.get('city') }}</div>
            <div><strong>@tag:</strong> {{ data.get('profile_tag') }}</div>
            <div><strong>Referido:</strong> {{ data.get('referral_code') or 'Ninguno' }}</div>
          </div>

          <form method="post">
            <div style="display:grid;gap:12px;">
              <a class="btn btn-secondary" href="{{ url_for('register_step', step=8) }}">Atrás</a>
              <button class="btn btn-primary" type="submit">Crear cuenta</button>
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
        step=step,
        question=question_map[step],
        helper=helper_map[step],
        progress=progress,
        cities=CITIES_CUBA,
        data=data,
        masked_carnet=mask_carnet(data.get("carnet", "")),
    )


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

        flash("Perfil actualizado correctamente.", "success")
        return redirect(url_for("profile"))

    wallet = get_wallet(user["id"])
    profile_photo_url = url_for("uploaded_file", filename=os.path.basename(user["profile_photo"])) if user["profile_photo"] else None

    content = """
    <div class="page-wrap">
      <div class="container">
        <div class="grid-2">
          <div class="panel">
            <h2>Mi perfil</h2>
            <div class="subtitle" style="margin-bottom:18px;">Datos protegidos de tu cuenta digital.</div>

            <form method="post" enctype="multipart/form-data">
              <div>
                <label>Nombre</label>
                <input value="{{ user['first_name'] }}" disabled>
              </div>

              <div>
                <label>Apellidos</label>
                <input value="{{ user['last_name'] }}" disabled>
              </div>

              <div>
                <label>Carnet</label>
                <input value="{{ masked_carnet }}" disabled>
              </div>

              <div>
                <label>@tag</label>
                <input value="{{ user['profile_tag'] }}" disabled>
              </div>

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
          </div>

          <div class="panel">
            <h2>Resumen rápido</h2>
            <div class="wallet-grid" style="grid-template-columns:repeat(2,minmax(0,1fr));">
              <div class="wallet-box">
                <div class="wallet-label">USD</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["usd_balance"]) }}</div>
              </div>
              <div class="wallet-box">
                <div class="wallet-label">USDT</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["usdt_balance"]) }}</div>
              </div>
              <div class="wallet-box">
                <div class="wallet-label">CUP</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["cup_balance"]) }}</div>
              </div>
              <div class="wallet-box">
                <div class="wallet-label">Bonus USDT</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["bonus_usdt_balance"]) }}</div>
              </div>
            </div>

            <div class="subtitle" style="margin-top:16px;">
              Correo: {{ user["email"] }}<br>
              Código de referido: <strong>{{ user["referral_code"] }}</strong>
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
        cities=CITIES_CUBA,
        masked_carnet=mask_carnet(user["carnet"]),
        profile_photo_url=profile_photo_url,
    )

