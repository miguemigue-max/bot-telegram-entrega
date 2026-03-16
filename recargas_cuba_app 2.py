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
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import secrets
import base64

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

app = Flask(__name__)
app.secret_key = "cambia-esta-clave-secreta-por-una-mas-segura"

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "xypher.alertas@gmail.com"
SMTP_PASSWORD = "dvss htwp pwmq jvsi"

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

def send_email(to_email, subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain", "utf-8"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, to_email, msg.as_string())
        server.quit()

        return True
    except Exception as e:
        print("ERROR ENVIANDO CORREO:", e)
        return False

def send_password_reset_email(to_email, reset_link):
    subject = "Restablece tu contraseña en XyPher"
    body = f"""
Hola,

Recibimos una solicitud para cambiar tu contraseña en XyPher.

Abre este enlace para crear una nueva contraseña:

{reset_link}

Este enlace expirará en 1 hora.

Si no fuiste tú, puedes ignorar este correo.

— XyPher
"""
    return send_email(to_email, subject, body)


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

def save_data_url_image(data_url, prefix="face"):
    if not data_url or "," not in data_url:
        return ""

    header, encoded = data_url.split(",", 1)
    ext = ".png"
    if "image/jpeg" in header:
        ext = ".jpg"
    elif "image/webp" in header:
        ext = ".webp"

    filename = f"{prefix}_{uuid.uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / filename

    with open(file_path, "wb") as f:
        f.write(base64.b64decode(encoded))

    return str(file_path)

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
       CREATE TABLE IF NOT EXISTS gift_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    description TEXT,
    price REAL,
    currency TEXT,
    image TEXT,
    active INTEGER DEFAULT 1
       )
   """)

    q(conn, """
        CREATE TABLE IF NOT EXISTS gift_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    description TEXT,
    price REAL,
    currency TEXT,
    image TEXT,
    active INTEGER DEFAULT 1
       )
   """)

    q(conn, """
    CREATE TABLE IF NOT EXISTS face_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        email TEXT NOT NULL,
        frame_1_path TEXT NOT NULL DEFAULT '',
        frame_2_path TEXT NOT NULL DEFAULT '',
        frame_3_path TEXT NOT NULL DEFAULT '',
        verification_type TEXT NOT NULL DEFAULT 'basic_liveness',
        status TEXT NOT NULL DEFAULT 'Pendiente',
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
       )
   """)

    q(conn, """
    CREATE TABLE IF NOT EXISTS password_resets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        used INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
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
  --bg:#f4ecfb;
  --bg-2:#efe3fa;
  --card:#ffffff;
  --card-2:#f8f1fd;
  --text:#191919;
  --muted:#6f6f7b;
  --accent:#8A05BE;
  --accent-2:#B65CFF;
  --line:rgba(138,5,190,0.10);
  --ok:#16a34a;
  --danger:#e11d48;
  --shadow:0 18px 40px rgba(138,5,190,0.12);
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
    radial-gradient(circle at top left, rgba(138,5,190,0.10), transparent 22%),
    radial-gradient(circle at top right, rgba(182,92,255,0.08), transparent 24%),
    linear-gradient(180deg, var(--bg) 0%, var(--bg-2) 100%);
  min-height:100vh;
}

    a{color:inherit;text-decoration:none}
    .container{width:min(1100px, 92%);margin:0 auto}

    .topbar{
  position:sticky;top:0;z-index:30;
  backdrop-filter:blur(18px);
  background:rgba(255,255,255,0.82);
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
  color:#2a2a2a;
  background:#ffffff;
  border:1px solid rgba(138,5,190,0.12);
  box-shadow:0 10px 24px rgba(138,5,190,0.08);
}
    .btn-danger{
      color:#ffd7df;
      background:rgba(255,92,122,0.10);
      border:1px solid rgba(255,92,122,0.12);
    }

    .icon-btn{
  width:50px;
  height:50px;
  border-radius:18px;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  color:#8A05BE;
  background:#ffffff;
  border:1px solid rgba(138,5,190,0.12);
  box-shadow:0 12px 28px rgba(138,5,190,0.10);
  cursor:pointer;
  font-size:1.25rem;
  font-weight:900;
}

    .menu-wrap{position:relative}
    .menu-dropdown{
  position:absolute;
  right:0;
  top:calc(100% + 10px);
  width:240px;
  border-radius:22px;
  background:#ffffff;
  border:1px solid rgba(138,5,190,0.10);
  box-shadow:0 18px 45px rgba(138,5,190,0.16);
  padding:10px;
  display:none;
  z-index:999;
}
    .menu-wrap:hover .menu-dropdown,
    .menu-wrap:focus-within .menu-dropdown{
      display:block;
    }

    .menu-item{
  display:block;
  padding:13px 14px;
  border-radius:14px;
  font-weight:800;
  color:#191919;
}
.menu-item:hover{
  background:rgba(138,5,190,0.08);
  color:#8A05BE;
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
  background:linear-gradient(180deg, #ffffff, #fbf7fe);
  border:1px solid rgba(138,5,190,0.08);
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
  background:#ffffff;
  border:1px solid rgba(138,5,190,0.10);
  box-shadow:0 12px 24px rgba(138,5,190,0.08);
  text-align:center;
  font-weight:900;
  color:#1d1d1f;
}

    .wallet-grid{
      display:grid;
      grid-template-columns:repeat(4,minmax(0,1fr));
      gap:16px;
      margin-top:20px;
    }

.logout-btn{
    color:white;
    background:#ff4d4f;
    padding:8px 12px;
    border-radius:8px;
}

    .wallet-box{
  padding:24px;
  background:#ffffff;
  border:1px solid rgba(138,5,190,0.08);
  box-shadow:0 14px 28px rgba(138,5,190,0.08);
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
  border:1px solid rgba(138,5,190,0.12);
  background:#ffffff;
  color:#191919;
  padding:15px 16px;
  font-size:1rem;
  outline:none;
}
    input::placeholder,textarea::placeholder{color:#8c8c99}
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

  .wallet-top{
    flex-direction:column;
    align-items:flex-start;
  }

  .hero-figure{
    display:none;
  }

  .hero-grid{
    grid-template-columns:1fr;
  }

  .hero{
    padding:28px 0 20px;
  }
}

.nubank-homer{
  background:#f4ecfb;
  min-height:100vh;
  padding-bottom:40px;
}

.nubank-header{
  background:linear-gradient(180deg,#8A05BE 0%, #9c27e6 100%);
  color:#fff;
  padding:22px 0 34px;
  border-bottom-left-radius:30px;
  border-bottom-right-radius:30px;
}

.nubank-top{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
}

.nubank-user{
  display:flex;
  align-items:center;
  gap:12px;
}

.nubank-avatar{
  width:42px;
  height:42px;
  border-radius:50%;
  background:rgba(255,255,255,0.18);
  display:flex;
  align-items:center;
  justify-content:center;
  font-weight:900;
}

.nubank-hello{
  font-size:1.12rem;
  font-weight:800;
}

.nubank-mini-icons{
  display:flex;
  gap:14px;
  font-size:1rem;
  opacity:.95;
}

.nubank-main{
  margin-top:-22px;
  padding-bottom:40px;
}

.nubank-section-card{
  background:#ffffff;
  border-radius:24px;
  padding:22px;
  box-shadow:0 12px 30px rgba(138,5,190,0.10);
  margin-bottom:18px;
  display:block;
}

.nubank-row{
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:12px;
  margin-bottom:18px;
}

.nubank-section-title{
  font-size:1rem;
  color:#191919;
  font-weight:700;
  margin-bottom:8px;
}

.nubank-balance{
  font-size:2.2rem;
  font-weight:900;
  color:#191919;
  line-height:1;
}

.nubank-arrow{
  font-size:1.8rem;
  color:#767676;
  line-height:1;
}

.nubank-actions-row{
  display:grid;
  grid-template-columns:repeat(4, minmax(0,1fr));
  gap:14px;
  align-items:start;
}

.nubank-action-btn{
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:flex-start;
  text-align:center;
  color:#191919;
  min-width:0;
}

.nubank-action-icon{
  width:58px;
  height:58px;
  margin:0 0 10px 0;
  border-radius:50%;
  background:#f2eef7;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size:1.2rem;
  font-weight:900;
  flex-shrink:0;
}

.nubank-action-text{
  font-size:.92rem;
  font-weight:700;
  color:#191919;
  line-height:1.2;
}

nubank-strip{
  background:#ffffff;
  border-radius:18px;
  padding:16px 18px;
  margin-bottom:14px;
  box-shadow:0 10px 24px rgba(138,5,190,0.08);
  display:flex;
  align-items:center;
  gap:12px;
  color:#191919;
}

.nubank-strip-icon{
  width:28px;
  height:28px;
  border-radius:10px;
  background:#f2eef7;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size:.95rem;
}

.nubank-strip-text{
  font-weight:800;
}

.nubank-wallet-card{
  background:#ffffff;
  border-radius:22px;
  padding:22px;
  box-shadow:0 10px 24px rgba(138,5,190,0.08);
  margin-bottom:14px;
  display:block;
}

.nubank-wallet-title{
  color:#6f6f7b;
  font-size:1rem;
  margin-bottom:12px;
}

.nubank-wallet-value{
  font-size:2rem;
  font-weight:900;
  color:#191919;
  line-height:1;
}

.nubank-tx-list{
  display:grid;
  gap:12px;
}

.nubank-tx-item{
  background:#ffffff;
  border-radius:20px;
  padding:18px;
  box-shadow:0 10px 24px rgba(138,5,190,0.08);
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:14px;
}

.nubank-tx-title{
  font-weight:800;
  color:#191919;
  margin-bottom:6px;
}

.nubank-tx-sub{
  color:#7a7a87;
  font-size:.92rem;
}

.nubank-tx-amount{
  font-size:1.1rem;
  font-weight:900;
}

.tx-plus{color:#16a34a}
.tx-minus{color:#d11a4a}

@media (max-width:640px){
  .nubank-main{
    margin-top:-18px;
  }

  .nubank-actions-row{
    grid-template-columns:repeat(4, minmax(0,1fr));
    gap:10px;
  }

  .nu-hero-copy{
  padding:220px 0 18px;
  max-width:760px;
  position:relative;
  z-index:2;
  }

.nu-hero-copy h1{
  margin:0;
  color:white;
  font-size:clamp(2.8rem, 9vw, 5rem);
  line-height:0.92;
  letter-spacing:-0.05em;
  font-weight:900;
  max-width:10ch;
  text-shadow:0 8px 24px rgba(0,0,0,0.22);
}

  .nubank-action-icon{
    width:54px;
    height:54px;
    font-size:1.1rem;
  }

  .nubank-action-text{
    font-size:.82rem;
  }

  .nubank-balance{
    font-size:2rem;
  }
}

.toast-stack{
  position:fixed;
  top:16px;
  left:50%;
  transform:translateX(-50%);
  z-index:9999;
  display:grid;
  gap:10px;
  width:min(92vw, 460px);
}

.toast{
  padding:14px 18px;
  border-radius:18px;
  font-weight:800;
  box-shadow:0 18px 40px rgba(0,0,0,0.12);
  animation:toastIn .25s ease;
}

.toast-success{
  background:#eaf9ef;
  color:#166534;
  border:1px solid rgba(22,101,52,0.12);
}

.toast-error{
  background:#fff1f3;
  color:#be123c;
  border:1px solid rgba(190,18,60,0.12);
}

.toast-info{
  background:#f3e8ff;
  color:#7e22ce;
  border:1px solid rgba(126,34,206,0.12);
}

@keyframes toastIn{
  from{opacity:0;transform:translateY(-10px)}
  to{opacity:1;transform:translateY(0)}
}

.profile-page{
  min-height:100vh;
  background:
    radial-gradient(circle at top left, rgba(138,5,190,0.08), transparent 22%),
    radial-gradient(circle at top right, rgba(182,92,255,0.06), transparent 24%),
    linear-gradient(180deg, #f5f3ef 0%, #edf2f3 100%);
  padding:24px 0 40px;
}

.profile-topbar{
  display:flex;
  justify-content:space-between;
  align-items:center;
  margin-bottom:18px;
}

.profile-top-btn,
.profile-upgrade-btn{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  min-width:64px;
  height:58px;
  padding:0 22px;
  border-radius:30px;
  background:rgba(255,255,255,0.72);
  border:1px solid rgba(0,0,0,0.06);
  box-shadow:0 10px 24px rgba(0,0,0,0.05);
  color:#111;
  font-weight:800;
  text-decoration:none;
}

.profile-upgrade-btn{
  min-width:auto;
}

.profile-header-card{
  text-align:center;
  padding:10px 0 18px;
}

.profile-avatar-wrap{
  display:flex;
  justify-content:center;
  margin-bottom:16px;
}

.profile-avatar-img,
.profile-avatar-fallback{
  width:118px;
  height:118px;
  border-radius:50%;
  object-fit:cover;
  box-shadow:0 10px 24px rgba(0,0,0,0.08);
}

.profile-avatar-fallback{
  display:flex;
  align-items:center;
  justify-content:center;
  background:linear-gradient(135deg,#8A05BE,#B65CFF);
  color:white;
  font-size:2rem;
  font-weight:900;
}

.profile-name{
  margin:0 0 10px;
  font-size:clamp(2rem, 6vw, 3rem);
  line-height:1;
  letter-spacing:-0.04em;
  font-weight:900;
  color:#171717;
}

.profile-tag-line{
  display:flex;
  justify-content:center;
  align-items:center;
  gap:8px;
  color:#202020;
  font-size:1.05rem;
  font-weight:700;
}

.profile-tag-icon{
  font-size:1.1rem;
  color:#666;
}

.profile-mini-grid{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:16px;
  margin:22px 0;
}

.profile-mini-card{
  background:rgba(255,255,255,0.85);
  border-radius:28px;
  padding:26px;
  min-height:158px;
  box-shadow:0 12px 28px rgba(0,0,0,0.05);
  border:1px solid rgba(0,0,0,0.05);
}

.profile-mini-icon{
  font-size:2rem;
  margin-bottom:28px;
}

.profile-mini-title{
  font-size:1rem;
  font-weight:900;
  color:#171717;
}

.profile-mini-sub{
  color:#6f6f7b;
  margin-top:4px;
  font-size:0.98rem;
}

.profile-section-card{
  background:rgba(255,255,255,0.88);
  border-radius:28px;
  padding:24px;
  margin-bottom:18px;
  box-shadow:0 12px 28px rgba(0,0,0,0.05);
  border:1px solid rgba(0,0,0,0.05);
}

.profile-section-title{
  font-size:1.1rem;
  font-weight:900;
  color:#171717;
  margin-bottom:12px;
}

.profile-item-row,
.profile-balance-row,
.profile-link-row{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:14px;
  padding:16px 0;
  border-bottom:1px solid rgba(0,0,0,0.06);
  color:#202020;
}

.profile-item-row:last-child,
.profile-balance-row:last-child,
.profile-link-row:last-child{
  border-bottom:none;
}

.profile-item-row span,
.profile-balance-row span{
  color:#6f6f7b;
}

.profile-link-row{
  text-decoration:none;
  font-weight:800;
}

.profile-form{
  display:grid;
  gap:14px;
}

@media (max-width:640px){
  .profile-mini-grid{
    grid-template-columns:1fr 1fr;
    gap:14px;
  }

  .profile-mini-card{
    min-height:146px;
    padding:22px;
  }

  .profile-name{
    font-size:2.2rem;
  }
}

.landing-nu{
  min-height:100vh;
  background:#efe7f7;
}

.menu-item-danger{
  color:#dc2626;
  font-weight:900;
}

.menu-item-danger:hover{
  background:rgba(220,38,38,0.08);
  color:#b91c1c;
}

.landing-hero-image{
  position:relative;
  min-height:100vh;
  background:
    linear-gradient(rgba(0,0,0,0.22), rgba(0,0,0,0.30)),
    url("https://images.unsplash.com/photo-1556740749-887f6717d7e4?auto=format&fit=crop&w=1200&q=80");
  background-size:cover;
  background-position:center;
}

.landing-overlay{
  min-height:100vh;
  padding:120px 0 40px;
  display:flex;
  align-items:flex-end;
}

.landing-copy{
  color:white;
  max-width:760px;
  margin-bottom:26px;
}

.landing-copy h1{
  margin:0 0 16px;
  font-size:clamp(2.6rem, 8vw, 4.8rem);
  line-height:0.95;
  letter-spacing:-0.05em;
  font-weight:900;
}

.landing-copy p{
  margin:0;
  font-size:1.2rem;
  line-height:1.7;
  color:rgba(255,255,255,0.94);
  max-width:28ch;
}

.landing-form-card{
  background:white;
  border-radius:34px;
  padding:28px;
  box-shadow:0 24px 60px rgba(0,0,0,0.18);
  max-width:760px;
  margin-top:22px;
}

.landing-form-card h3{
  margin:0 0 10px;
  font-size:2rem;
  line-height:1.1;
  color:#111;
  font-weight:900;
}

.landing-form-card p{
  margin:0 0 22px;
  color:#666;
  font-size:1.05rem;
  line-height:1.6;
}

.landing-main-btn,
.landing-secondary-btn{
  display:flex;
  align-items:center;
  justify-content:center;
  width:100%;
  min-height:64px;
  border-radius:999px;
  font-size:1.15rem;
  font-weight:900;
  text-decoration:none;
}

.landing-main-btn{
  background:linear-gradient(135deg,#8A05BE,#B65CFF);
  color:white;
  box-shadow:0 16px 30px rgba(138,5,190,0.20);
  margin-bottom:14px;
}

.landing-secondary-btn{
  background:#f7f4fb;
  color:#1d1d1f;
  border:1px solid rgba(138,5,190,0.10);
}

@media (max-width:640px){
  .nu-top-cta{
    padding-top:16px;
  }

  .nu-pill-btn{
    min-height:52px;
    padding:0 24px;
    font-size:1rem;
  }

  .nu-hero-copy{
    padding:180px 0 14px;
  }

  .nu-hero-copy h1{
    font-size:clamp(2.5rem, 11vw, 4rem);
    max-width:11ch;
  }

  .nu-floating-card{
    padding:24px;
    border-radius:30px 30px 0 0;
    margin-top:18px;
  }

  .nu-floating-card h3{
    font-size:1.7rem;
  }

  .nu-floating-card p{
    font-size:1rem;
  }

  .nu-main-button{
    min-height:62px;
    font-size:1.08rem;
  }
}

.nu-landing-hero{
  min-height:100vh;
  position:relative;
  background:
    linear-gradient(rgba(0,0,0,0.12), rgba(0,0,0,0.22)),
    url("https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1400&q=80");
  background-size:cover;
  background-position:center;
  border-bottom-left-radius:32px;
  border-bottom-right-radius:32px;
  overflow:hidden;
  display:flex;
  align-items:flex-end;
}

.nu-top-cta{
  display:flex;
  justify-content:center;
  padding-top:24px;
  position:relative;
  z-index:3;
}

.nu-pill-btn{
  background:#8A05BE !important;
  color:white !important;
  padding:14px 24px !important;
  border-radius:999px !important;
  display:inline-flex !important;
}

.nu-floating-card{
  background:#ffffff;
  border-radius:36px 36px 0 0;
  padding:30px;
  max-width:760px;
  margin:34px auto 0;
  box-shadow:0 24px 60px rgba(0,0,0,0.20);
  position:relative;
  z-index:3;
}

.nu-main-button{
  display:flex !important;
  justify-content:center !important;
  align-items:center !important;
  width:100% !important;
  min-height:64px !important;
  border-radius:999px !important;
  background:#8A05BE !important;
  color:white !important;
  text-decoration:none !important;
  font-weight:900 !important;
}

.btn-sm{
  min-height:36px;
  padding:8px 12px;
  font-size:.9rem;
  border-radius:12px;
}

.admin-actions{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
}

  </style>
</head>
<script>
  setTimeout(function () {
    const stack = document.getElementById("toastStack");
    if (stack) {
      stack.style.transition = "opacity .35s ease, transform .35s ease";
      stack.style.opacity = "0";
      stack.style.transform = "translateX(-50%) translateY(-10px)";
      setTimeout(() => stack.remove(), 400);
    }
  }, 3000);
</script>
<body>
  <nav class="topbar">
    <div class="container topbar-inner">
      <div class="brand">
        <a href="{{ url_for('home') }}" style="display:flex;align-items:center;gap:12px;">
          <span class="brand-mark">◉</span>
          <span>XyPher</span>
        </a>
      </div>

      <div class="nav-actions">
  {% if user %}
    <div class="menu-wrap">
      <button class="icon-btn menu-toggle-btn" type="button" onclick="toggleMenu(event)">⋯</button>
      <div class="menu-dropdown" id="mainMenu">
        {% if user['is_admin'] %}
          <a class="menu-item" href="{{ url_for('admin_dashboard') }}">Panel admin</a>
          <a class="menu-item" href="{{ url_for('admin_settings') }}">Configuración</a>
          <a class="menu-item menu-item-danger" href="{{ url_for('logout') }}">Cerrar sesión</a>
        {% else %}
          <a class="menu-item" href="{{ url_for('wallet_page') }}">Inicio</a>
          <a class="menu-item" href="{{ url_for('profile') }}">Mi perfil</a>
          <a class="menu-item menu-item-danger" href="{{ url_for('logout') }}">Cerrar sesión</a>
        {% endif %}
      </div>
    </div>
  {% else %}
    <div class="menu-wrap">
      <button class="icon-btn menu-toggle-btn" type="button" onclick="toggleMenu(event)">⋯</button>
      <div class="menu-dropdown" id="mainMenu">
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
    <div class="toast-stack" id="toastStack">
      {% for category, message in messages %}
        <div class="toast toast-{{ category }}">{{ message }}</div>
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

@app.route("/test-email")
def test_email():
    ok = send_email(
        "torrescervantesmiguel426@gmail.com",
        "Prueba de correo XyPher",
        "Hola, este es un correo de prueba desde tu plataforma XyPher."
    )

    if ok:
        return "Correo enviado correctamente"
    return "No se pudo enviar el correo"

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_db()
    row = q(conn, """
        SELECT pr.*, u.email
        FROM password_resets pr
        JOIN users u ON u.id = pr.user_id
        WHERE pr.token = ?
    """, (token,)).fetchone()

    if not row:
        conn.close()
        flash("Enlace inválido.", "error")
        return redirect(url_for("login"))

    if row["used"]:
        conn.close()
        flash("Este enlace ya fue usado.", "error")
        return redirect(url_for("login"))

    expires_at = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
    if datetime.now() > expires_at:
        conn.close()
        flash("Este enlace expiró.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if len(password) < 6:
            conn.close()
            flash("La contraseña debe tener al menos 6 caracteres.", "error")
            return redirect(url_for("reset_password", token=token))

        if password != confirm_password:
            conn.close()
            flash("Las contraseñas no coinciden.", "error")
            return redirect(url_for("reset_password", token=token))

        q(conn, """
            UPDATE users
            SET password = ?, failed_attempts = 0, is_locked = 0
            WHERE id = ?
        """, (
            generate_password_hash(password),
            row["user_id"]
        ))

        q(conn, "UPDATE password_resets SET used = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()

        flash("Tu contraseña fue actualizada correctamente.", "success")
        return redirect(url_for("login"))

    conn.close()

    content = """
    <div class="auth-shell">
      <div class="auth-card panel">
        <h2 style="margin:0 0 10px;">Crear nueva contraseña</h2>
        <p class="subtitle" style="margin:0 0 18px;">
          Escribe tu nueva contraseña.
        </p>

        <form method="post">
          <div>
            <label>Nueva contraseña</label>
            <input type="password" name="password" required>
          </div>

          <div>
            <label>Confirmar contraseña</label>
            <input type="password" name="confirm_password" required>
          </div>

          <button class="btn btn-primary" type="submit">Guardar contraseña</button>
        </form>
      </div>
    </div>
    """
    return render_page(content, title="Restablecer contraseña", user=None)

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
            LIMIT 4
        """, (user["id"],)).fetchall()
        conn.close()

        content = """
        <section class="nubank-home">
          <div class="nubank-header">
            <div class="container">
              <div class="nubank-top">
                <div class="nubank-user">
                  <div class="nubank-avatar">○</div>
                  <div class="nubank-hello">Hola, {{ user["first_name"] }}</div>
                </div>
                <div class="nubank-mini-icons">
                  <span>◎</span>
                  <span>⌁</span>
                  <span>✦</span>
                </div>
              </div>
            </div>
          </div>

          <div class="container nubank-main">
            <div class="nubank-section-card">
              <div class="nubank-row">
                <div>
                  <div class="nubank-section-title">Cuenta</div>
                  <div class="nubank-balance">${{ "%.2f"|format(total_balance) }}</div>
                </div>
                <div class="nubank-arrow">›</div>
              </div>

              <div class="nubank-actions-row">
                <a href="{{ url_for('transfer_money') }}" class="nubank-action-btn">
                  <div class="nubank-action-icon">↗</div>
                  <div class="nubank-action-text">Enviar</div>
                </a>

                <a href="{{ url_for('deposit_page') }}" class="nubank-action-btn">
                  <div class="nubank-action-icon">＋</div>
                  <div class="nubank-action-text">Depositar</div>
                </a>

                <a href="{{ url_for('withdraw_page') }}" class="nubank-action-btn">
                  <div class="nubank-action-icon">↓</div>
                  <div class="nubank-action-text">Retirar</div>
                </a>

                <a href="{{ url_for('convert_page') }}" class="nubank-action-btn">
                  <div class="nubank-action-icon">⇄</div>
                  <div class="nubank-action-text">Convertir</div>
                </a>
              </div>
            </div>

            <div class="nubank-strip">
              <div class="nubank-strip-icon">⌁</div>
              <div class="nubank-strip-text">Mis saldos</div>
            </div>

            <div class="nubank-wallet-card">
              <div class="nubank-wallet-title">USD</div>
              <div class="nubank-wallet-value">{{ "%.2f"|format(wallet["usd_balance"]) }}</div>
            </div>

            <div class="nubank-wallet-card">
              <div class="nubank-wallet-title">USDT</div>
              <div class="nubank-wallet-value">{{ "%.2f"|format(wallet["usdt_balance"]) }}</div>
            </div>

            <div class="nubank-wallet-card">
              <div class="nubank-wallet-title">CUP</div>
              <div class="nubank-wallet-value">{{ "%.2f"|format(wallet["cup_balance"]) }}</div>
            </div>

            <div class="nubank-wallet-card">
              <div class="nubank-wallet-title">Bonus USDT</div>
              <div class="nubank-wallet-value">{{ "%.2f"|format(wallet["bonus_usdt_balance"]) }}</div>
            </div>

            <div class="nubank-strip" style="margin-top:18px;">
              <div class="nubank-strip-icon">◎</div>
              <div class="nubank-strip-text">Últimos movimientos</div>
            </div>

            <div class="nubank-tx-list">
              {% if txs %}
                {% for tx in txs %}
                  <div class="nubank-tx-item">
                    <div>
                      <div class="nubank-tx-title">{{ tx["description"] }}</div>
                      <div class="nubank-tx-sub">{{ tx["currency"] }} · {{ tx["created_at"] }}</div>
                    </div>
                    <div class="nubank-tx-amount {% if tx['direction'] == 'credit' %}tx-plus{% else %}tx-minus{% endif %}">
                      {% if tx['direction'] == 'credit' %}+{% else %}-{% endif %}{{ "%.2f"|format(tx["amount"]) }}
                    </div>
                  </div>
                {% endfor %}
              {% else %}
                <div class="nubank-tx-item">
                  <div>
                    <div class="nubank-tx-title">Sin movimientos todavía</div>
                    <div class="nubank-tx-sub">Tu actividad aparecerá aquí.</div>
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
    <section class="nu-landing">
      <div class="nu-landing-hero">
        <div class="container">
          <div class="nu-top-cta">
            <a href="{{ url_for('register_step', step=1) }}" class="nu-pill-btn">
              Quiero ser XyPher
            </a>
          </div>

          <div class="nu-hero-copy">
            <h1>Únete a la nueva cuenta digital pensada para Cuba</h1>
          </div>

          <div class="nu-floating-card">
            <h3>Abre tu cuenta XyPher</h3>
            <p>
              Guarda saldo en USD, USDT y CUP. Deposita, retira,
              convierte y transfiere dinero en un solo lugar.
            </p>

            <a class="nu-main-button" href="{{ url_for('register_step', step=1) }}">
              Continuar
            </a>

            <a class="nu-sub-link" href="{{ url_for('login') }}">
              Ya tengo cuenta
            </a>
          </div>
        </div>
      </div>
    </section>
    """
    return render_page(content, title="XyPher", user=None)

@app.route("/admin/shop_orders")
@admin_required
def admin_shop_orders():
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/deliver_gift/<int:order_id>", methods=["POST"])
@admin_required
def deliver_gift(order_id):
    code = request.form["code"]

    conn = get_db()

    q(conn, """
        UPDATE shop_orders
        SET status = 'Entregado', code = ?
        WHERE id = ?
    """, (code, order_id))

    conn.commit()
    conn.close()

    flash("Gift card entregada", "success")

    return redirect(url_for("admin_shop_orders"))

@app.route("/buy/<int:product_id>")
@login_required
def buy_product(product_id):
    user = current_user()

    conn = get_db()
    product = q(conn, "SELECT * FROM gift_cards WHERE id = ?", (product_id,)).fetchone()

    wallet = get_wallet(user["id"])

    if wallet["USD"] < product["price"]:
        flash("Saldo insuficiente", "error")
        return redirect(url_for("shop"))

    adjust_wallet(
        user["id"],
        "USD",
        product["price"],
        "Compra en tienda",
        "debit",
        "shop",
        str(product_id)
    )

    q(conn, """
        INSERT INTO shop_orders (user_id, product_id, amount, currency, status, created_at)
        VALUES (?, ?, ?, ?, 'Pendiente', ?)
    """, (
        user["id"],
        product_id,
        product["price"],
        "USD",
        now_str()
    ))

    conn.commit()
    conn.close()

    flash("Compra realizada. Recibirás tu código pronto.", "success")

    return redirect(url_for("shop"))

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
            return redirect(url_for("forgot_password"))

        conn = get_db()
        user = q(conn, "SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user:
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

            q(conn, """
                INSERT INTO password_resets (user_id, token, expires_at, used, created_at)
                VALUES (?, ?, ?, 0, ?)
            """, (
                user["id"],
                token,
                expires_at,
                now_str()
            ))
            conn.commit()

            reset_link = url_for("reset_password", token=token, _external=True)
            send_password_reset_email(user["email"], reset_link)

        conn.close()

        flash("Si el correo existe, te enviamos un enlace para restablecer tu contraseña.", "success")
        return redirect(url_for("login"))

    content = """
    <div class="auth-shell">
      <div class="auth-card panel">
        <h2 style="margin:0 0 10px;">Recuperar contraseña</h2>
        <p class="subtitle" style="margin:0 0 18px;">
          Escribe tu correo y te enviaremos un enlace para crear una nueva contraseña.
        </p>

        <form method="post">
          <div>
            <label>Correo electrónico</label>
            <input type="email" name="email" placeholder="tucorreo@email.com" required>
          </div>

          <button class="btn btn-primary" type="submit">Enviar enlace</button>
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
        4: "Debe tener al menos 8 caracteres.",
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

            conn.close()

            session["pending_registration"] = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "password": password,
                "carnet": carnet,
                "city": city,
                "profile_tag": profile_tag,
                "referral_code": referral_code,
            }

            return redirect(url_for("register_face_check"))

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
              <input type="text" name="first_name" placeholder="Tu nombre" required>
            {% elif step == 2 %}
              <input type="text" name="last_name" placeholder="Tus apellidos" required>
            {% elif step == 3 %}
              <input type="email" name="email" placeholder="tucorreo@email.com" required>
            {% elif step == 4 %}
              <input type="password" name="password" placeholder="Tu contraseña" required>
            {% elif step == 5 %}
              <input type="text" name="carnet" placeholder="Tu número de carnet" required>
            {% elif step == 6 %}
              <select name="city" required>
                <option value="">Selecciona tu ciudad</option>
                {% for city in cities %}
                  <option value="{{ city }}" {% if data.get('city') == city %}selected{% endif %}>{{ city }}</option>
                {% endfor %}
              </select>
            {% elif step == 7 %}
              <input type="text" name="profile_tag" placeholder="@miguel" required>
            {% elif step == 8 %}
              <input type="text" name="referral_code" placeholder="Código opcional">
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

@app.route("/register/face-check", methods=["GET", "POST"])
def register_face_check():
    pending = session.get("pending_registration")

    if not pending:
        flash("Primero completa el registro.", "error")
        return redirect(url_for("register_step", step=1))

    if request.method == "POST":
        frame_1 = request.form.get("frame_1", "").strip()
        frame_2 = request.form.get("frame_2", "").strip()
        frame_3 = request.form.get("frame_3", "").strip()

        if not frame_1 or not frame_2 or not frame_3:
            flash("No se pudo completar la verificación facial.", "error")
            return redirect(url_for("register_face_check"))

        first_name = pending["first_name"]
        last_name = pending["last_name"]
        email = pending["email"]
        password = pending["password"]
        carnet = pending["carnet"]
        city = pending["city"]
        profile_tag = clean_tag(pending["profile_tag"])
        referral_code = pending["referral_code"].strip().upper()

        conn = get_db()

        email_exists = q(conn, "SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        carnet_exists = q(conn, "SELECT id FROM users WHERE carnet = ?", (carnet,)).fetchone()
        tag_exists = q(conn, "SELECT id FROM users WHERE profile_tag = ?", (profile_tag,)).fetchone()

        if email_exists or carnet_exists or tag_exists:
            conn.close()
            flash("Ya existe una cuenta con esos datos.", "error")
            return redirect(url_for("register_step", step=1))

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
                is_admin, is_locked, failed_attempts, created_at, last_login_at,
                face_verified, face_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, 0, 0, 0, ?, '', 1, ?)
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

        frame_1_path = save_data_url_image(frame_1, "face1")
        frame_2_path = save_data_url_image(frame_2, "face2")
        frame_3_path = save_data_url_image(frame_3, "face3")

        q(conn, """
            INSERT INTO face_verifications (
                user_id, email, frame_1_path, frame_2_path, frame_3_path,
                verification_type, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'basic_liveness', 'Aprobado', ?)
        """, (
            user_id,
            email,
            frame_1_path,
            frame_2_path,
            frame_3_path,
            now_str(),
        ))

        conn.commit()
        conn.close()

        session.pop("register_data", None)
        session.pop("pending_registration", None)
        session["user_id"] = user_id

        send_email(
            email,
            "Bienvenido a XyPher",
            f"Hola {first_name}, tu cuenta fue creada correctamente en XyPher."
        )

        flash("Verificación completada y cuenta creada correctamente.", "success")
        return redirect(url_for("home"))

    content = """
    <div class="auth-shell">
      <div class="auth-card panel" style="max-width:720px;">
        <h2 style="margin:0 0 10px;">Verificación facial</h2>
        <p class="subtitle" style="margin:0 0 18px;">
          Para continuar, centra tu rostro, acércate y luego aléjate de la cámara.
        </p>

        <div style="background:#111;border-radius:24px;overflow:hidden;position:relative;">
          <video id="video" autoplay playsinline style="width:100%;display:block;"></video>
          <canvas id="canvas" style="display:none;"></canvas>

          <div id="faceMessage" style="
            position:absolute;
            left:16px;
            right:16px;
            bottom:16px;
            background:rgba(255,255,255,0.92);
            color:#111;
            border-radius:16px;
            padding:14px 16px;
            font-weight:800;
          ">
            Preparando cámara...
          </div>
        </div>

        <form method="post" id="faceForm" style="margin-top:18px;">
          <input type="hidden" name="frame_1" id="frame_1">
          <input type="hidden" name="frame_2" id="frame_2">
          <input type="hidden" name="frame_3" id="frame_3">
          <button class="btn btn-primary" type="button" id="startFaceCheck">Iniciar verificación</button>
        </form>
      </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/@mediapipe/face_detection/face_detection.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/@mediapipe/camera_utils/camera_utils.js"></script>

    <script>
      const videoEl = document.getElementById("video");
      const canvasEl = document.getElementById("canvas");
      const messageEl = document.getElementById("faceMessage");
      const startBtn = document.getElementById("startFaceCheck");
      const formEl = document.getElementById("faceForm");

      const frame1Input = document.getElementById("frame_1");
      const frame2Input = document.getElementById("frame_2");
      const frame3Input = document.getElementById("frame_3");

      let challengeStep = 0;
      let done = false;

      function captureFrame(targetInput) {
        const ctx = canvasEl.getContext("2d");
        canvasEl.width = videoEl.videoWidth;
        canvasEl.height = videoEl.videoHeight;
        ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
        targetInput.value = canvasEl.toDataURL("image/png");
      }

      const faceDetection = new FaceDetection.FaceDetection({
        locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_detection/${file}`
      });

      faceDetection.setOptions({
        model: "short",
        minDetectionConfidence: 0.6
      });

      faceDetection.onResults((results) => {
        if (done) return;

        if (!results.detections || results.detections.length === 0) {
          messageEl.textContent = "No detecto tu rostro. Ponte frente a la cámara.";
          return;
        }

        const box = results.detections[0].boundingBox;
        const width = box.width;

        if (challengeStep === 0) {
          messageEl.textContent = "Paso 1: centra tu rostro";
          if (width > 0.18 && width < 0.32) {
            captureFrame(frame1Input);
            challengeStep = 1;
          }
        } else if (challengeStep === 1) {
          messageEl.textContent = "Paso 2: acércate un poco más";
          if (width >= 0.35) {
            captureFrame(frame2Input);
            challengeStep = 2;
          }
        } else if (challengeStep === 2) {
          messageEl.textContent = "Paso 3: aléjate un poco";
          if (width <= 0.24) {
            captureFrame(frame3Input);
            done = true;
            messageEl.textContent = "Verificación completada. Enviando...";
            setTimeout(() => formEl.submit(), 800);
          }
        }
      });

      startBtn.addEventListener("click", async () => {
        startBtn.style.display = "none";
        messageEl.textContent = "Solicitando acceso a la cámara...";

        const camera = new Camera(videoEl, {
          onFrame: async () => {
            await faceDetection.send({image: videoEl});
          },
          width: 480,
          height: 640
        });

        camera.start();
      });
    </script>
    """
    return render_page(content, title="Verificación facial", user=None)


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
    <section class="profile-page">
      <div class="container" style="max-width:760px;">

        <div class="profile-topbar">
          <a href="{{ url_for('home') }}" class="profile-top-btn">✕</a>
          <a href="{{ url_for('referrals_page') }}" class="profile-upgrade-btn">Invitar</a>
        </div>

        <div class="profile-header-card">
          <div class="profile-avatar-wrap">
            {% if profile_photo_url %}
              <img src="{{ profile_photo_url }}" alt="Foto de perfil" class="profile-avatar-img">
            {% else %}
              <div class="profile-avatar-fallback">
                {{ user['first_name'][0] }}{{ user['last_name'][0] }}
              </div>
            {% endif %}
          </div>

          <h1 class="profile-name">
            {{ user['first_name']|upper }} {{ user['last_name']|upper }}
          </h1>

          <div class="profile-tag-line">
            {{ user['profile_tag'] }}
            <span class="profile-tag-icon">⌁</span>
          </div>
        </div>

        <div class="profile-mini-grid">
          <div class="profile-mini-card">
            <div class="profile-mini-icon">◫</div>
            <div class="profile-mini-title">Cuenta</div>
            <div class="profile-mini-sub">Tu perfil</div>
          </div>

          <a href="{{ url_for('referrals_page') }}" class="profile-mini-card" style="text-decoration:none;color:inherit;">
            <div class="profile-mini-icon">👥</div>
            <div class="profile-mini-title">Invitar amigos</div>
            <div class="profile-mini-sub">Gana bonus en USDT</div>
          </a>
        </div>

        <div class="profile-section-card">
          <div class="profile-section-title">Cuenta</div>

          <div class="profile-item-row">
            <span>Correo</span>
            <strong>{{ user['email'] }}</strong>
          </div>

          <div class="profile-item-row">
            <span>Ciudad</span>
            <strong>{{ user['city'] }}</strong>
          </div>

          <div class="profile-item-row">
            <span>Carnet</span>
            <strong>{{ masked_carnet }}</strong>
          </div>

          <div class="profile-item-row">
            <span>Código de referido</span>
            <strong>{{ user['referral_code'] }}</strong>
          </div>
        </div>

        <div class="profile-section-card">
          <div class="profile-section-title">Saldos</div>

          <div class="profile-balance-row">
            <span>USD</span>
            <strong>{{ "%.2f"|format(wallet["usd_balance"]) }}</strong>
          </div>

          <div class="profile-balance-row">
            <span>USDT</span>
            <strong>{{ "%.2f"|format(wallet["usdt_balance"]) }}</strong>
          </div>

          <div class="profile-balance-row">
            <span>CUP</span>
            <strong>{{ "%.2f"|format(wallet["cup_balance"]) }}</strong>
          </div>

          <div class="profile-balance-row">
            <span>Bonus USDT</span>
            <strong>{{ "%.2f"|format(wallet["bonus_usdt_balance"]) }}</strong>
          </div>
        </div>

        <div class="profile-section-card">
          <div class="profile-section-title">Editar perfil visible</div>

          <form method="post" enctype="multipart/form-data" class="profile-form">
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

        <div class="profile-section-card">
          <div class="profile-section-title">Seguridad</div>

          <a class="profile-link-row" href="{{ url_for('forgot_password') }}">
            <span>Recuperar contraseña</span>
            <span>›</span>
          </a>

          <a class="profile-link-row" href="{{ url_for('logout') }}">
            <span>Cerrar sesión</span>
            <span>›</span>
          </a>
        </div>

      </div>
    </section>
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

@app.route("/transfer", methods=["GET", "POST"])
@login_required
def transfer_money():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    wallet = get_wallet(user["id"])

    if request.method == "POST":
        tag = clean_tag(request.form.get("tag", ""))
        currency = request.form.get("currency", "").strip().upper()
        amount = parse_float(request.form.get("amount", "0"), 0)

        if not tag or currency not in {"USD", "USDT", "CUP"} or amount <= 0:
            flash("Completa correctamente los datos de la transferencia.", "error")
            return redirect(url_for("transfer_money"))

        conn = get_db()
        receiver = q(conn, "SELECT * FROM users WHERE profile_tag = ?", (tag,)).fetchone()

        if not receiver:
            conn.close()
            flash("No encontramos ese @tag.", "error")
            return redirect(url_for("transfer_money"))

        if receiver["id"] == user["id"]:
            conn.close()
            flash("No puedes enviarte dinero a ti mismo.", "error")
            return redirect(url_for("transfer_money"))

        if not can_debit_wallet(user["id"], currency, amount):
            conn.close()
            flash("Saldo insuficiente.", "error")
            return redirect(url_for("transfer_money"))

        q(conn, """
            INSERT INTO transfers (
                sender_user_id, receiver_user_id, currency, amount, status, created_at
            ) VALUES (?, ?, ?, ?, 'Completado', ?)
        """, (
            user["id"],
            receiver["id"],
            currency,
            amount,
            now_str()
        ))
        conn.commit()
        conn.close()

        adjust_wallet(user["id"], currency, amount, f"Transferencia enviada a {tag}", "debit", "transfer_out", tag)
        adjust_wallet(receiver["id"], currency, amount, f"Transferencia recibida de {user['profile_tag']}", "credit", "transfer_in", user["profile_tag"])

        log_action(user["id"], "transfer_sent", f"{amount} {currency} a {tag}")
        flash("Transferencia realizada correctamente.", "success")
        send_email(
    receiver["email"],
    "Has recibido dinero",
    f"Has recibido {amount:.2f} {currency} de {user['profile_tag']} en XyPher."
)
        return redirect(url_for("home"))

    content = """
    <div class="page-wrap">
      <div class="container" style="max-width:620px;">
        <div class="panel">
          <h2 style="margin:0 0 8px;">Enviar dinero</h2>
          <p class="subtitle" style="margin:0 0 18px;">
            Envía saldo instantáneamente a otro usuario usando su @tag.
          </p>

          <form method="post">
            <div>
              <label>@tag destino</label>
              <input type="text" name="tag" placeholder="@usuario" required>
            </div>

            <div>
              <label>Moneda</label>
              <select name="currency" required>
                <option value="USD">USD</option>
                <option value="USDT">USDT</option>
                <option value="CUP">CUP</option>
              </select>
            </div>

            <div>
              <label>Monto</label>
              <input type="text" name="amount" placeholder="0.00" required>
            </div>

            <button class="btn btn-primary" type="submit">Enviar ahora</button>
          </form>

          <div class="wallet-grid" style="margin-top:18px;grid-template-columns:repeat(3,minmax(0,1fr));">
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
          </div>
        </div>
      </div>
    </div>
    """
    return render_page(content, title="Enviar dinero", user=user, wallet=wallet)


@app.route("/deposit", methods=["GET", "POST"])
@login_required
def deposit_page():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        method = request.form.get("method", "").strip()
        currency = request.form.get("currency", "").strip().upper()
        amount = parse_float(request.form.get("amount", "0"), 0)
        detail = request.form.get("detail", "").strip()
        proof = request.files.get("proof")
        proof_path = ""

        if method not in DEPOSIT_METHODS or currency not in {"USD", "USDT", "CUP"} or amount <= 0:
            flash("Completa correctamente los datos del depósito.", "error")
            return redirect(url_for("deposit_page"))

        if proof and proof.filename:
            safe_name = secure_filename(proof.filename)
            ext = os.path.splitext(safe_name)[1].lower()
            if ext not in [".jpg", ".jpeg", ".png", ".pdf", ".webp"]:
                flash("El comprobante debe ser JPG, PNG, WEBP o PDF.", "error")
                return redirect(url_for("deposit_page"))

            final_name = f"deposit_{uuid.uuid4().hex}{ext}"
            final_path = UPLOAD_DIR / final_name
            proof.save(final_path)
            proof_path = str(final_path)

        conn = get_db()
        q(conn, """
            INSERT INTO deposits (
                user_id, method, currency, amount, detail, proof_path, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Pendiente', ?)
        """, (
            user["id"],
            method,
            currency,
            amount,
            detail,
            proof_path,
            now_str()
        ))
        conn.commit()
        conn.close()

        log_action(user["id"], "deposit_created", f"{amount} {currency} por {method}")
        flash("Depósito enviado para revisión.", "success")
        send_email(
    user["email"],
    "Depósito recibido",
    f"Hola {user['first_name']}, recibimos tu solicitud de depósito por {amount:.2f} {currency}. La revisaremos pronto."
)
        return redirect(url_for("home"))

    content = """
    <div class="page-wrap">
      <div class="container" style="max-width:680px;">
        <div class="panel">
          <h2 style="margin:0 0 8px;">Depositar fondos</h2>
          <p class="subtitle" style="margin:0 0 18px;">
            Solicita un depósito a tu cuenta digital y sube el comprobante.
          </p>

          <form method="post" enctype="multipart/form-data">
            <div>
              <label>Método</label>
              <select name="method" required>
                {% for method in methods %}
                  <option value="{{ method }}">{{ method }}</option>
                {% endfor %}
              </select>
            </div>

            <div>
              <label>Moneda</label>
              <select name="currency" required>
                <option value="USD">USD</option>
                <option value="USDT">USDT</option>
                <option value="CUP">CUP</option>
              </select>
            </div>

            <div>
              <label>Monto</label>
              <input type="text" name="amount" placeholder="0.00" required>
            </div>

            <div>
              <label>Detalle</label>
              <textarea name="detail" placeholder="Ej: transferencia enviada, wallet, referencia, etc."></textarea>
            </div>

            <div>
              <label>Comprobante</label>
              <input type="file" name="proof">
            </div>

            <button class="btn btn-primary" type="submit">Enviar depósito</button>
          </form>
        </div>
      </div>
    </div>
    """
    return render_page(content, title="Depositar", user=user, methods=DEPOSIT_METHODS)


@app.route("/withdraw", methods=["GET", "POST"])
@login_required
def withdraw_page():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    wallet = get_wallet(user["id"])
    settings = get_settings()

    usd_sell = parse_float(settings.get("usd_sell_cup", "490"), 490)
    usdt_sell = parse_float(settings.get("usdt_sell_cup", "575"), 575)
    bonus_withdraw_min = parse_float(settings.get("bonus_withdraw_min_usdt", "1"), 1)

    if request.method == "POST":
        currency = request.form.get("currency", "").strip().upper()
        method = request.form.get("method", "").strip()
        amount = parse_float(request.form.get("amount", "0"), 0)
        destination = request.form.get("destination", "").strip()
        use_bonus = request.form.get("use_bonus", "") == "yes"

        if method not in WITHDRAW_METHODS or currency not in {"USD", "USDT", "CUP"} or amount <= 0 or not destination:
            flash("Completa correctamente los datos del retiro.", "error")
            return redirect(url_for("withdraw_page"))

        payout_amount = amount
        payout_currency = currency

        if method == "Tarjeta CUP":
            payout_currency = "CUP"
            if currency == "USD":
                payout_amount = amount * usd_sell
            elif currency == "USDT":
                payout_amount = amount * usdt_sell
            elif currency == "CUP":
                payout_amount = amount

        if method == "PIX Brasil":
            payout_currency = currency

        if use_bonus:
            if currency != "USDT":
                flash("El bonus solo puede retirarse en USDT.", "error")
                return redirect(url_for("withdraw_page"))
            if amount < bonus_withdraw_min:
                flash(f"El mínimo para retirar bonus es {bonus_withdraw_min:.2f} USDT.", "error")
                return redirect(url_for("withdraw_page"))
            if not can_debit_wallet(user["id"], "BONUS_USDT", amount):
                flash("Saldo de bonus insuficiente.", "error")
                return redirect(url_for("withdraw_page"))
            debit_currency = "BONUS_USDT"
            debit_desc = "Solicitud de retiro desde bonus"
        else:
            if not can_debit_wallet(user["id"], currency, amount):
                flash("Saldo insuficiente.", "error")
                return redirect(url_for("withdraw_page"))
            debit_currency = currency
            debit_desc = "Solicitud de retiro"

        conn = get_db()
        q(conn, """
            INSERT INTO withdrawals (
                user_id, method, currency, amount, destination,
                payout_amount, payout_currency, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Pendiente', ?)
        """, (
            user["id"],
            method,
            currency,
            amount,
            destination,
            payout_amount,
            payout_currency,
            now_str()
        ))
        conn.commit()
        conn.close()

        adjust_wallet(user["id"], debit_currency, amount, debit_desc, "debit", "withdraw_request", destination)
        log_action(user["id"], "withdraw_created", f"{amount} {currency} por {method}")
        flash("Solicitud de retiro enviada.", "success")
        send_email(
    user["email"],
    "Retiro solicitado",
    f"Recibimos tu solicitud de retiro por {amount:.2f} {currency}. Estado actual: Pendiente."
)
        return redirect(url_for("home"))

    content = """
    <div class="page-wrap">
      <div class="container" style="max-width:700px;">
        <div class="panel">
          <h2 style="margin:0 0 8px;">Retirar fondos</h2>
          <p class="subtitle" style="margin:0 0 18px;">
            Retira por cripto, Paypal, tarjeta en CUP o PIX Brasil.
          </p>

          <form method="post">
            <div>
              <label>Moneda</label>
              <select name="currency" required>
                <option value="USD">USD</option>
                <option value="USDT">USDT</option>
                <option value="CUP">CUP</option>
              </select>
            </div>

            <div>
              <label>Método de retiro</label>
              <select name="method" required>
                {% for method in methods %}
                  <option value="{{ method }}">{{ method }}</option>
                {% endfor %}
              </select>
            </div>

            <div>
              <label>Monto</label>
              <input type="text" name="amount" placeholder="0.00" required>
            </div>

            <div>
              <label>Destino</label>
              <input type="text" name="destination" placeholder="Wallet / Email / Tarjeta / PIX" required>
            </div>

            <div>
              <label style="display:flex;gap:10px;align-items:center;">
                <input type="checkbox" name="use_bonus" value="yes" style="width:auto;">
                Usar saldo de bonus USDT
              </label>
            </div>

            <button class="btn btn-primary" type="submit">Solicitar retiro</button>
          </form>

          <div class="wallet-grid" style="margin-top:18px;grid-template-columns:repeat(4,minmax(0,1fr));">
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
              <div class="wallet-label">Bonus</div>
              <div class="wallet-amount">{{ "%.2f"|format(wallet["bonus_usdt_balance"]) }}</div>
            </div>
          </div>

          <div class="panel" style="padding:18px;margin-top:18px;">
            <div class="subtitle">
              USD retiro a tarjeta CUP: {{ "%.2f"|format(usd_sell) }} CUP<br>
              USDT retiro a tarjeta CUP: {{ "%.2f"|format(usdt_sell) }} CUP<br>
              Mínimo retiro de bonus: {{ "%.2f"|format(bonus_withdraw_min) }} USDT
            </div>
          </div>
        </div>
      </div>
    </div>
    """
    return render_page(
        content,
        title="Retirar",
        user=user,
        wallet=wallet,
        methods=WITHDRAW_METHODS,
        usd_sell=usd_sell,
        usdt_sell=usdt_sell,
        bonus_withdraw_min=bonus_withdraw_min
    )


@app.route("/convert", methods=["GET", "POST"])
@login_required
def convert_page():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    wallet = get_wallet(user["id"])
    settings = get_settings()

    usd_buy = parse_float(settings.get("usd_buy_cup", "510"), 510)
    usd_sell = parse_float(settings.get("usd_sell_cup", "490"), 490)
    usdt_buy = parse_float(settings.get("usdt_buy_cup", "585"), 585)
    usdt_sell = parse_float(settings.get("usdt_sell_cup", "575"), 575)
    usd_to_usdt = parse_float(settings.get("usd_to_usdt", "1.00"), 1.00)
    usdt_to_usd = parse_float(settings.get("usdt_to_usd", "1.00"), 1.00)

    if request.method == "POST":
        from_currency = request.form.get("from_currency", "").strip().upper()
        to_currency = request.form.get("to_currency", "").strip().upper()
        amount = parse_float(request.form.get("amount", "0"), 0)

        if amount <= 0 or from_currency == to_currency:
            flash("Conversión inválida.", "error")
            return redirect(url_for("convert_page"))

        if from_currency not in {"USD", "USDT", "CUP"} or to_currency not in {"USD", "USDT", "CUP"}:
            flash("Monedas no válidas.", "error")
            return redirect(url_for("convert_page"))

        if not can_debit_wallet(user["id"], from_currency, amount):
            flash("Saldo insuficiente.", "error")
            return redirect(url_for("convert_page"))

        rate_used = 0.0
        receive_amount = 0.0

        if from_currency == "USD" and to_currency == "USDT":
            rate_used = usd_to_usdt
            receive_amount = amount * rate_used
        elif from_currency == "USDT" and to_currency == "USD":
            rate_used = usdt_to_usd
            receive_amount = amount * rate_used
        elif from_currency == "USD" and to_currency == "CUP":
            rate_used = usd_sell
            receive_amount = amount * rate_used
        elif from_currency == "CUP" and to_currency == "USD":
            rate_used = usd_buy
            receive_amount = amount / rate_used
        elif from_currency == "USDT" and to_currency == "CUP":
            rate_used = usdt_sell
            receive_amount = amount * rate_used
        elif from_currency == "CUP" and to_currency == "USDT":
            rate_used = usdt_buy
            receive_amount = amount / rate_used
        else:
            flash("Esa conversión todavía no está disponible.", "error")
            return redirect(url_for("convert_page"))

        adjust_wallet(user["id"], from_currency, amount, f"Conversión a {to_currency}", "debit", "convert_out")
        adjust_wallet(user["id"], to_currency, receive_amount, f"Conversión desde {from_currency}", "credit", "convert_in")

        conn = get_db()
        q(conn, """
            INSERT INTO conversions (
                user_id, from_currency, to_currency, from_amount, to_amount, rate_used, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user["id"],
            from_currency,
            to_currency,
            amount,
            receive_amount,
            rate_used,
            now_str()
        ))
        conn.commit()
        conn.close()

        log_action(user["id"], "convert", f"{amount} {from_currency} -> {receive_amount} {to_currency}")
        flash("Conversión realizada correctamente.", "success")
        return redirect(url_for("home"))

    content = """
    <div class="page-wrap">
      <div class="container" style="max-width:680px;">
        <div class="panel">
          <h2 style="margin:0 0 8px;">Convertir monedas</h2>
          <p class="subtitle" style="margin:0 0 18px;">
            Convierte saldo dentro de la plataforma usando tus tasas configuradas.
          </p>

          <form method="post">
            <div>
              <label>De</label>
              <select name="from_currency" required>
                <option value="USD">USD</option>
                <option value="USDT">USDT</option>
                <option value="CUP">CUP</option>
              </select>
            </div>

            <div>
              <label>A</label>
              <select name="to_currency" required>
                <option value="USD">USD</option>
                <option value="USDT">USDT</option>
                <option value="CUP">CUP</option>
              </select>
            </div>

            <div>
              <label>Monto</label>
              <input type="text" name="amount" placeholder="0.00" required>
            </div>

            <button class="btn btn-primary" type="submit">Convertir ahora</button>
          </form>

          <div class="panel" style="padding:18px;margin-top:18px;">
            <div class="subtitle">
              USD compra: {{ "%.2f"|format(usd_buy) }} CUP<br>
              USD venta: {{ "%.2f"|format(usd_sell) }} CUP<br>
              USDT compra: {{ "%.2f"|format(usdt_buy) }} CUP<br>
              USDT venta: {{ "%.2f"|format(usdt_sell) }} CUP<br>
              USD → USDT: {{ "%.2f"|format(usd_to_usdt) }}<br>
              USDT → USD: {{ "%.2f"|format(usdt_to_usd) }}
            </div>
          </div>

          <div class="wallet-grid" style="margin-top:18px;grid-template-columns:repeat(3,minmax(0,1fr));">
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
          </div>
        </div>
      </div>
    </div>
    """
    return render_page(
        content,
        title="Convertir",
        user=user,
        wallet=wallet,
        usd_buy=usd_buy,
        usd_sell=usd_sell,
        usdt_buy=usdt_buy,
        usdt_sell=usdt_sell,
        usd_to_usdt=usd_to_usdt,
        usdt_to_usd=usdt_to_usd
    )

@app.route("/referrals")
@login_required
def referrals_page():
    user = current_user()
    if user["is_admin"]:
        return redirect(url_for("admin_dashboard"))

    settings = get_settings()
    reward = parse_float(settings.get("referral_reward_usdt", "0.25"), 0.25)
    required_deposit = parse_float(settings.get("referral_required_deposit_usd", "5"), 5)
    bonus_min = parse_float(settings.get("bonus_withdraw_min_usdt", "1"), 1)

    wallet = get_wallet(user["id"])

    conn = get_db()
    referrals = q(conn, """
        SELECT
            r.*,
            u.first_name,
            u.last_name,
            u.email,
            u.profile_tag
        FROM referrals r
        JOIN users u ON u.id = r.invited_user_id
        WHERE r.inviter_user_id = ?
        ORDER BY r.id DESC
    """, (user["id"],)).fetchall()
    conn.close()

    content = """
    <div class="page-wrap">
      <div class="container">
        <div class="grid-2">
          <div class="panel">
            <h2 style="margin:0 0 8px;">Programa de referidos</h2>
            <p class="subtitle" style="margin:0 0 18px;">
              Invita usuarios reales y gana bonus cuando completen su primer depósito válido.
            </p>

            <div class="wallet-box" style="margin-bottom:16px;">
              <div class="wallet-label">Tu código</div>
              <div class="wallet-amount" style="font-size:1.7rem;">{{ user["referral_code"] }}</div>
            </div>

            <div class="panel" style="padding:18px;">
              <div class="subtitle">
                Bono por referido válido: <strong>{{ "%.2f"|format(reward) }} USDT</strong><br>
                Depósito mínimo del referido: <strong>{{ "%.2f"|format(required_deposit) }} USD</strong><br>
                Mínimo para retirar bonus: <strong>{{ "%.2f"|format(bonus_min) }} USDT</strong><br><br>
                El bonus no se paga por registro. Se activa solo cuando el referido haga
                un depósito aprobado de al menos {{ "%.2f"|format(required_deposit) }} USD.
              </div>
            </div>
          </div>

          <div class="panel">
            <h2 style="margin:0 0 8px;">Saldo bonus</h2>
            <p class="subtitle" style="margin:0 0 18px;">
              Este saldo se guarda separado del saldo normal.
            </p>

            <div class="wallet-grid" style="grid-template-columns:1fr;">
              <div class="wallet-box">
                <div class="wallet-label">Bonus USDT</div>
                <div class="wallet-amount">{{ "%.2f"|format(wallet["bonus_usdt_balance"]) }}</div>
              </div>
            </div>
          </div>
        </div>

        <div class="section-title">
          <div>
            <h2 style="margin:0 0 6px;">Mis referidos</h2>
            <div class="subtitle">Estado de cada referido registrado.</div>
          </div>
        </div>

        <div class="panel">
          {% if referrals %}
            <table>
              <thead>
                <tr>
                  <th>Nombre</th>
                  <th>@tag</th>
                  <th>Correo</th>
                  <th>Bono</th>
                  <th>Estado</th>
                  <th>Fecha</th>
                </tr>
              </thead>
              <tbody>
                {% for ref in referrals %}
                <tr>
                  <td data-label="Nombre">{{ ref["first_name"] }} {{ ref["last_name"] }}</td>
                  <td data-label="@tag">{{ ref["profile_tag"] }}</td>
                  <td data-label="Correo">{{ ref["email"] }}</td>
                  <td data-label="Bono">{{ "%.2f"|format(ref["reward_usdt"]) }} USDT</td>
                  <td data-label="Estado"><span class="status status-{{ ref['status'] }}">{{ ref["status"] }}</span></td>
                  <td data-label="Fecha">{{ ref["created_at"] }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          {% else %}
            <div class="empty">Todavía no tienes referidos.</div>
          {% endif %}
        </div>
      </div>
    </div>
    """
    return render_page(
        content,
        title="Referidos",
        user=user,
        wallet=wallet,
        referrals=referrals,
        reward=reward,
        required_deposit=required_deposit,
        bonus_min=bonus_min
    )


@app.route("/admin")
@admin_required
def admin_dashboard():
    user = current_user()

    conn = get_db()
    users = q(conn, "SELECT * FROM users ORDER BY id DESC LIMIT 50").fetchall()

    deposits = q(conn, """
        SELECT d.*, u.email, u.profile_tag
        FROM deposits d
        JOIN users u ON u.id = d.user_id
        ORDER BY d.id DESC
        LIMIT 100
    """).fetchall()

    withdrawals = q(conn, """
        SELECT w.*, u.email, u.profile_tag
        FROM withdrawals w
        JOIN users u ON u.id = w.user_id
        ORDER BY w.id DESC
        LIMIT 100
    """).fetchall()

    conn.close()

    content = """
    <div class="page-wrap">
      <div class="container">
        <div class="section-title">
          <div>
            <h2 style="margin:0 0 6px;">Panel admin</h2>
            <div class="subtitle">Control total de usuarios, depósitos y retiros.</div>
          </div>
          <a class="btn btn-secondary" href="{{ url_for('admin_settings') }}">Configuración</a>
        </div>

        <div class="grid-2">
          <div class="panel">
            <h3 style="margin:0 0 14px;">Usuarios recientes</h3>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Nombre</th>
                  <th>Correo</th>
                  <th>@tag</th>
                </tr>
              </thead>
              <tbody>
                {% for u in users %}
                <tr>
                  <td data-label="ID">{{ u["id"] }}</td>
                  <td data-label="Nombre">{{ u["first_name"] }} {{ u["last_name"] }}</td>
                  <td data-label="Correo">{{ u["email"] }}</td>
                  <td data-label="@tag">{{ u["profile_tag"] }}</td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>

          <div class="panel">
            <h3 style="margin:0 0 14px;">Depósitos</h3>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Usuario</th>
                  <th>Moneda</th>
                  <th>Monto</th>
                  <th>Estado</th>
                  <th>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {% for d in deposits %}
                <tr>
                  <td data-label="ID">{{ d["id"] }}</td>
                  <td data-label="Usuario">{{ d["email"] }}</td>
                  <td data-label="Moneda">{{ d["currency"] }}</td>
                  <td data-label="Monto">{{ "%.2f"|format(d["amount"]) }}</td>
                  <td data-label="Estado">
                    <span class="status status-{{ d['status']|lower }}">{{ d["status"] }}</span>
                  </td>
                  <td data-label="Acciones">
                    <div class="admin-actions">
                      {% if d["status"] == "Pendiente" %}
                        <a class="btn btn-primary btn-sm" href="{{ url_for('approve_deposit', deposit_id=d['id']) }}">Aprobar</a>
                        <a class="btn btn-danger btn-sm" href="{{ url_for('reject_deposit', deposit_id=d['id']) }}">Rechazar</a>
                        <a class="btn btn-secondary btn-sm" href="{{ url_for('cancel_deposit', deposit_id=d['id']) }}">Cancelar</a>
                      {% else %}
                        <span class="subtitle">Procesado</span>
                      {% endif %}
                    </div>
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        </div>

        <div class="panel" style="margin-top:20px;">
          <h3 style="margin:0 0 14px;">Retiros</h3>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Usuario</th>
                <th>Método</th>
                <th>Moneda</th>
                <th>Monto</th>
                <th>Estado</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {% for w in withdrawals %}
              <tr>
                <td data-label="ID">{{ w["id"] }}</td>
                <td data-label="Usuario">{{ w["email"] }}</td>
                <td data-label="Método">{{ w["method"] }}</td>
                <td data-label="Moneda">{{ w["currency"] }}</td>
                <td data-label="Monto">{{ "%.2f"|format(w["amount"]) }}</td>
                <td data-label="Estado">
                  <span class="status status-{{ w['status']|lower }}">{{ w["status"] }}</span>
                </td>
                <td data-label="Acciones">
                  <div class="admin-actions">
                    {% if w["status"] == "Pendiente" %}
                      <a class="btn btn-primary btn-sm" href="{{ url_for('approve_withdraw', withdraw_id=w['id']) }}">Aprobar</a>
                      <a class="btn btn-danger btn-sm" href="{{ url_for('reject_withdraw', withdraw_id=w['id']) }}">Rechazar</a>
                      <a class="btn btn-secondary btn-sm" href="{{ url_for('cancel_withdraw', withdraw_id=w['id']) }}">Cancelar</a>
                    {% elif w["status"] == "Aprobado" %}
                      <a class="btn btn-primary btn-sm" href="{{ url_for('mark_withdraw_paid', withdraw_id=w['id']) }}">Marcar pagado</a>
                    {% else %}
                      <span class="subtitle">Procesado</span>
                    {% endif %}
                  </div>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return render_page(content, title="Admin", user=user, users=users, deposits=deposits, withdrawals=withdrawals)

@app.route("/admin/approve_withdraw/<int:withdraw_id>")
@admin_required
def approve_withdraw(withdraw_id):
    user = current_user()
    conn = get_db()
    withdraw = q(conn, "SELECT * FROM withdrawals WHERE id = ?", (withdraw_id,)).fetchone()

    if not withdraw:
        conn.close()
        flash("Retiro no encontrado.", "error")
        return redirect(url_for("admin_dashboard"))

    if withdraw["status"] != "Pendiente":
        conn.close()
        flash("Ese retiro ya fue procesado.", "error")
        return redirect(url_for("admin_dashboard"))

    q(conn, "UPDATE withdrawals SET status = 'Aprobado' WHERE id = ?", (withdraw_id,))
    conn.commit()
    conn.close()

    log_action(user["id"], "approve_withdraw", f"withdraw_id={withdraw_id}")
    flash("Retiro aprobado. Ahora puedes marcarlo como pagado.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/reject_withdraw/<int:withdraw_id>")
@admin_required
def reject_withdraw(withdraw_id):
    user = current_user()
    conn = get_db()
    withdraw = q(conn, "SELECT * FROM withdrawals WHERE id = ?", (withdraw_id,)).fetchone()

    if not withdraw:
        conn.close()
        flash("Retiro no encontrado.", "error")
        return redirect(url_for("admin_dashboard"))

    if withdraw["status"] != "Pendiente":
        conn.close()
        flash("Ese retiro ya fue procesado.", "error")
        return redirect(url_for("admin_dashboard"))

    q(conn, "UPDATE withdrawals SET status = 'Rechazado' WHERE id = ?", (withdraw_id,))
    conn.commit()
    conn.close()

    adjust_wallet(
        withdraw["user_id"],
        withdraw["currency"],
        withdraw["amount"],
        "Retiro rechazado - devolución",
        "credit",
        "withdraw_refund",
        str(withdraw_id)
    )

    log_action(user["id"], "reject_withdraw", f"withdraw_id={withdraw_id}")
    flash("Retiro rechazado y saldo devuelto.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/cancel_withdraw/<int:withdraw_id>")
@admin_required
def cancel_withdraw(withdraw_id):
    user = current_user()
    conn = get_db()
    withdraw = q(conn, "SELECT * FROM withdrawals WHERE id = ?", (withdraw_id,)).fetchone()

    if not withdraw:
        conn.close()
        flash("Retiro no encontrado.", "error")
        return redirect(url_for("admin_dashboard"))

    if withdraw["status"] != "Pendiente":
        conn.close()
        flash("Ese retiro ya fue procesado.", "error")
        return redirect(url_for("admin_dashboard"))

    q(conn, "UPDATE withdrawals SET status = 'Cancelado' WHERE id = ?", (withdraw_id,))
    conn.commit()
    conn.close()

    adjust_wallet(
        withdraw["user_id"],
        withdraw["currency"],
        withdraw["amount"],
        "Retiro cancelado - devolución",
        "credit",
        "withdraw_refund",
        str(withdraw_id)
    )

    log_action(user["id"], "cancel_withdraw", f"withdraw_id={withdraw_id}")
    flash("Retiro cancelado y saldo devuelto.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/reject_deposit/<int:deposit_id>")
@admin_required
def reject_deposit(deposit_id):
    user = current_user()
    conn = get_db()
    deposit = q(conn, "SELECT * FROM deposits WHERE id = ?", (deposit_id,)).fetchone()

    if not deposit:
        conn.close()
        flash("Depósito no encontrado.", "error")
        return redirect(url_for("admin_dashboard"))

    if deposit["status"] != "Pendiente":
        conn.close()
        flash("Ese depósito ya fue procesado.", "error")
        return redirect(url_for("admin_dashboard"))

    q(conn, "UPDATE deposits SET status = 'Rechazado' WHERE id = ?", (deposit_id,))
    conn.commit()
    conn.close()

    log_action(user["id"], "reject_deposit", f"deposit_id={deposit_id}")
    flash("Depósito rechazado.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/mark_withdraw_paid/<int:withdraw_id>")
@admin_required
def mark_withdraw_paid(withdraw_id):
    user = current_user()
    conn = get_db()

    withdraw = q(conn, "SELECT * FROM withdrawals WHERE id = ?", (withdraw_id,)).fetchone()

    q(conn, "UPDATE withdrawals SET status = 'Pagado' WHERE id = ?", (withdraw_id,))
    conn.commit()

    log_action(user["id"], "mark_withdraw_paid", f"withdraw_id={withdraw_id}")
    flash("Retiro marcado como pagado.", "success")

    conn = get_db()
    target_user = q(conn, "SELECT * FROM users WHERE id = ?", (withdraw["user_id"],)).fetchone()
    conn.close()

    if target_user:
        send_email(
            target_user["email"],
            "Retiro pagado",
            f"Tu retiro de {withdraw['amount']:.2f} {withdraw['currency']} fue marcado como pagado."
        )

    return redirect(url_for("admin_dashboard"))

@app.route("/admin/cancel_deposit/<int:deposit_id>")
@admin_required
def cancel_deposit(deposit_id):
    user = current_user()
    conn = get_db()
    deposit = q(conn, "SELECT * FROM deposits WHERE id = ?", (deposit_id,)).fetchone()

    if not deposit:
        conn.close()
        flash("Depósito no encontrado.", "error")
        return redirect(url_for("admin_dashboard"))

    if deposit["status"] != "Pendiente":
        conn.close()
        flash("Ese depósito ya fue procesado.", "error")
        return redirect(url_for("admin_dashboard"))

    q(conn, "UPDATE deposits SET status = 'Cancelado' WHERE id = ?", (deposit_id,))
    conn.commit()
    conn.close()

    log_action(user["id"], "cancel_deposit", f"deposit_id={deposit_id}")
    flash("Depósito cancelado.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/approve_deposit/<int:deposit_id>")
@admin_required
def approve_deposit(deposit_id):
    user = current_user()
    conn = get_db()
    deposit = q(conn, "SELECT * FROM deposits WHERE id = ?", (deposit_id,)).fetchone()

    if not deposit:
        conn.close()
        flash("Depósito no encontrado.", "error")
        return redirect(url_for("admin_dashboard"))

    if deposit["status"] != "Pendiente":
        conn.close()
        flash("Ese depósito ya fue procesado.", "error")
        return redirect(url_for("admin_dashboard"))

    q(conn, "UPDATE deposits SET status = 'Aprobado' WHERE id = ?", (deposit_id,))
    conn.commit()
    conn.close()

    adjust_wallet(
        deposit["user_id"],
        deposit["currency"],
        deposit["amount"],
        "Depósito aprobado",
        "credit",
        "deposit",
        str(deposit_id)
    )

    if deposit["currency"] == "USD":
        activate_referral_if_needed(deposit["user_id"], deposit["amount"])

    log_action(user["id"], "approve_deposit", f"deposit_id={deposit_id}")
    flash("Depósito aprobado correctamente.", "success")

    conn = get_db()
    target_user = q(conn, "SELECT * FROM users WHERE id = ?", (deposit["user_id"],)).fetchone()
    conn.close()

    if target_user:
        send_email(
            target_user["email"],
            "Depósito aprobado",
            f"Tu depósito de {deposit['amount']:.2f} {deposit['currency']} fue aprobado y ya está disponible en tu cuenta."
        )

    return redirect(url_for("admin_dashboard"))

@app.route("/shop")
@login_required
def shop():
    user = current_user()
    conn = get_db()

    products = q(conn, "SELECT * FROM gift_cards WHERE active = 1").fetchall()
    conn.close()

    content = """
    <h2>Tienda</h2>

    <div class="shop-grid">
    {% for p in products %}
        <div class="shop-card">
            <img src="{{ p.image }}">
            <h3>{{ p.name }}</h3>
            <p>{{ p.price }} USD</p>

            <a class="btn" href="/buy/{{ p.id }}">Comprar</a>
        </div>
    {% endfor %}
    </div>
    """

    return render_page(content, title="Tienda", user=user, products=products)

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    user = current_user()
    settings = get_settings()

    if request.method == "POST":
        conn = get_db()

        for key in [
            "usd_buy_cup",
            "usd_sell_cup",
            "usdt_buy_cup",
            "usdt_sell_cup",
            "usd_to_usdt",
            "usdt_to_usd",
            "referral_reward_usdt",
            "referral_required_deposit_usd",
            "bonus_withdraw_min_usdt",
        ]:
            value = request.form.get(key, "").strip()
            if value:
                q(conn, "UPDATE settings SET value = ? WHERE key = ?", (value, key))

        conn.commit()
        conn.close()

        log_action(user["id"], "admin_update_settings")
        flash("Configuración actualizada.", "success")
        return redirect(url_for("admin_settings"))

    content = """
    <div class="page-wrap">
      <div class="container">
        <div class="panel">
          <h2>Configuración del sistema</h2>

          <form method="post">

            <h3>Tasas USD</h3>

            <label>USD compra (CUP)</label>
            <input name="usd_buy_cup" value="{{ settings['usd_buy_cup'] }}">

            <label>USD venta (CUP)</label>
            <input name="usd_sell_cup" value="{{ settings['usd_sell_cup'] }}">

            <h3>USDT</h3>

            <label>USDT compra (CUP)</label>
            <input name="usdt_buy_cup" value="{{ settings['usdt_buy_cup'] }}">

            <label>USDT venta (CUP)</label>
            <input name="usdt_sell_cup" value="{{ settings['usdt_sell_cup'] }}">

            <h3>Conversión</h3>

            <label>USD → USDT</label>
            <input name="usd_to_usdt" value="{{ settings['usd_to_usdt'] }}">

            <label>USDT → USD</label>
            <input name="usdt_to_usd" value="{{ settings['usdt_to_usd'] }}">

            <h3>Referidos</h3>

            <label>Bonus USDT</label>
            <input name="referral_reward_usdt" value="{{ settings['referral_reward_usdt'] }}">

            <label>Depósito mínimo referido (USD)</label>
            <input name="referral_required_deposit_usd" value="{{ settings['referral_required_deposit_usd'] }}">

            <label>Mínimo retiro bonus (USDT)</label>
            <input name="bonus_withdraw_min_usdt" value="{{ settings['bonus_withdraw_min_usdt'] }}">

            <br><br>
            <button class="btn btn-primary">Guardar cambios</button>

          </form>
        </div>
      </div>
    </div>
    """

    return render_page(
        content,
        title="Configuración",
        user=user,
        settings=settings
    )


@app.route("/admin/adjust_wallet", methods=["GET","POST"])
@admin_required
def admin_adjust_wallet():
    user = current_user()

    if request.method == "POST":
        tag = clean_tag(request.form.get("tag",""))
        currency = request.form.get("currency","")
        amount = parse_float(request.form.get("amount","0"),0)
        direction = request.form.get("direction","credit")

        conn = get_db()
        target = q(conn,"SELECT * FROM users WHERE profile_tag = ?",(tag,)).fetchone()
        conn.close()

        if not target:
            flash("Usuario no encontrado.","error")
            return redirect(url_for("admin_adjust_wallet"))

        adjust_wallet(
            target["id"],
            currency,
            amount,
            "Ajuste admin",
            direction,
            "admin_adjust"
        )

        flash("Saldo ajustado correctamente.","success")
        return redirect(url_for("admin_dashboard"))

    content = """
    <div class="page-wrap">
      <div class="container">
        <div class="panel">
          <h2>Ajustar saldo usuario</h2>

          <form method="post">

            <label>@tag usuario</label>
            <input name="tag" placeholder="@usuario">

            <label>Moneda</label>
            <select name="currency">
              <option>USD</option>
              <option>USDT</option>
              <option>CUP</option>
              <option>BONUS_USDT</option>
            </select>

            <label>Monto</label>
            <input name="amount">

            <label>Tipo</label>
            <select name="direction">
              <option value="credit">Agregar</option>
              <option value="debit">Quitar</option>
            </select>

            <br><br>
            <button class="btn btn-primary">Aplicar ajuste</button>

          </form>
        </div>
      </div>
    </div>
    """

    return render_page(content,title="Ajustar saldo",user=user)


@app.route("/receipt/<int:tx_id>")
@login_required
def receipt(tx_id):
    user = current_user()

    conn = get_db()
    tx = q(conn,"SELECT * FROM wallet_transactions WHERE id = ?",(tx_id,)).fetchone()
    conn.close()

    if not tx:
        abort(404)

    if tx["user_id"] != user["id"] and not user["is_admin"]:
        abort(403)

    pdf = generate_receipt_pdf(
        "Recibo Banco Cuba",
        [
            f"Transacción: {tx_id}",
            f"Tipo: {tx['tx_type']}",
            f"Moneda: {tx['currency']}",
            f"Monto: {tx['amount']}",
            f"Dirección: {tx['direction']}",
            f"Descripción: {tx['description']}",
            f"Fecha: {tx['created_at']}"
        ]
    )

    if not pdf:
        flash("PDF no disponible en este servidor.","error")
        return redirect(url_for("home"))

    return send_file(
        pdf,
        as_attachment=True,
        download_name=f"recibo_{tx_id}.pdf",
        mimetype="application/pdf"
    )

def ensure_database():
    if not DB_PATH.exists():
        init_db()
    else:
        try:
            conn = get_db()
            q(conn, "SELECT id FROM users LIMIT 1")
            conn.close()
        except Exception:
            init_db()


ensure_database()


@app.context_processor
def inject_globals():
    return {
        "now": now_str()
    }


@app.errorhandler(403)
def forbidden(e):
    return render_page(
        """
        <div class="page-wrap">
          <div class="container">
            <div class="panel">
              <h2>Acceso denegado</h2>
              <p class="subtitle">No tienes permisos para acceder a esta página.</p>
              <a class="btn btn-primary" href="{{ url_for('home') }}">Volver al inicio</a>
            </div>
          </div>
        </div>
        """,
        title="403",
        user=current_user()
    ), 403


@app.errorhandler(404)
def not_found(e):
    return render_page(
        """
        <div class="page-wrap">
          <div class="container">
            <div class="panel">
              <h2>Página no encontrada</h2>
              <p class="subtitle">La página que buscas no existe.</p>
              <a class="btn btn-primary" href="{{ url_for('home') }}">Volver al inicio</a>
            </div>
          </div>
        </div>
        """,
        title="404",
        user=current_user()
    ), 404


@app.errorhandler(500)
def server_error(e):
    return render_page(
        """
        <div class="page-wrap">
          <div class="container">
            <div class="panel">
              <h2>Error interno</h2>
              <p class="subtitle">Algo salió mal en el servidor.</p>
              <a class="btn btn-primary" href="{{ url_for('home') }}">Volver al inicio</a>
            </div>
          </div>
        </div>
        """,
        title="500",
        user=current_user()
    ), 500


if __name__ == "__main__":
    ensure_database()
    app.run()
