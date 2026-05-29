import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import zipfile
import ipaddress
from datetime import datetime, timedelta
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from urllib.parse import urlparse

import pikepdf
import pytesseract
import requests
from PIL import Image
from pyzbar.pyzbar import decode
from pypdf import PdfReader, PdfWriter

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


APP_VERSION = "6.8.5"
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/uploads"))
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/exports"))
DB_PATH = DATA_DIR / "medvault.sqlite3"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "10"))
MAX_CONTENT_LENGTH_MB = int(os.getenv("MAX_CONTENT_LENGTH_MB", "100"))
HA_WEBHOOK_URL = os.getenv("HA_WEBHOOK_URL", "").strip()
N8N_INGEST_TOKEN = os.getenv("N8N_INGEST_TOKEN", "").strip()
SHARE_BASE_URL = os.getenv("SHARE_BASE_URL", "").rstrip("/")
SELF_BASE_URL = os.getenv("SELF_BASE_URL", "http://192.168.50.201:8088").rstrip("/")
CALENDAR_ICS_URL = os.getenv("CALENDAR_ICS_URL", "").strip()
CALENDAR_SYNC_KEYWORDS = [x.strip().lower() for x in os.getenv("CALENDAR_SYNC_KEYWORDS", "consulta,consultório,medico,médico,endocrino,endocrinologista,urologista,telemedicina,exame,laboratório,laboratorio,dasa,amil,porto").split(",") if x.strip()]
MANDATORY_HEALTH_CALENDAR_KEYWORDS = [
    "consulta", "consultório", "consultorio", "medico", "médico", "telemedicina",
    "exame", "laboratório", "laboratorio", "dasa", "amil", "porto",
    "aplicação", "aplicacao", "aplicaçao", "injetável", "injetavel",
    "injetáveis", "injetaveis", "injeção", "injecao", "raia", "drogaria",
    "farmacia", "farmácia", "mounjaro", "deposteron"
]
CALENDAR_SYNC_KEYWORDS = list(dict.fromkeys(CALENDAR_SYNC_KEYWORDS + MANDATORY_HEALTH_CALENDAR_KEYWORDS))
CALENDAR_AI_SYNC_ENABLED = os.getenv("CALENDAR_AI_SYNC_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
CALENDAR_AI_SYNC_MAX_EVENTS = int(os.getenv("CALENDAR_AI_SYNC_MAX_EVENTS", "3") or 3)


DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="MedVault HealthOps API", version=APP_VERSION)

scheduler = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

@app.get("/api/uploads/{name}")
def get_upload_safe(name: str):
    safe_name = Path(name).name
    p = UPLOAD_DIR / safe_name
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(p)



def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_ai_field(ai, key, default=""):
    if not isinstance(ai, dict):
        return default
    return safe_db_text(ai.get(key, default) or default)


def fts_query(q: str) -> str:
    value = safe_db_text(q).strip()
    if not value:
        return '""'
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def safe_db_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def safe_filename(value):
    name = safe_db_text(value or "").strip()
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = name.strip(" ._-")
    return name or "arquivo"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def today_date():
    return datetime.now().date().isoformat()


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except Exception:
        return None


def parse_datetime_value(value):
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    for candidate in (raw, raw.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate)
        except Exception:
            pass
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:len(fmt)], fmt)
        except Exception:
            pass
    return None


def text_norm(value):
    return safe_db_text(value).strip().lower()



def calendar_keyword_text(value):
    value = text_norm(value)
    replacements = {
        "ç": "c", "ã": "a", "á": "a", "à": "a", "â": "a",
        "é": "e", "ê": "e", "í": "i", "ó": "o", "ô": "o",
        "õ": "o", "ú": "u"
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def is_injection_medication(name):
    value = text_norm(name)
    return any(x in value for x in ["deposteron", "mounjaro", "inje", "injeta", "injet"])


def opposite_side(side):
    value = text_norm(side)
    if "direit" in value:
        return "esquerdo"
    if "esquerd" in value:
        return "direito"
    return ""


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def db():
    # Autocommit evita manter transações abertas durante OCR/Ollama.
    # Isso reduz "database is locked" quando a UI consulta /api/status enquanto um upload está processando.
    conn = sqlite3.connect(DB_PATH, timeout=120, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def rows(items):
    return [dict(x) for x in items]


def row(item):
    return dict(item) if item else None




def core_schema_ready():
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name IN ('profiles','treatment_events','ingest_jobs','source_documents')"
            ).fetchall()
            names = {r["name"] for r in rows}
            return {"profiles", "treatment_events", "ingest_jobs", "source_documents"}.issubset(names)
    except Exception:
        return False


def ensure_core_schema():
    if not core_schema_ready():
        init_db()

def get_setting(key, default=""):
    try:
        with db() as conn:
            r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if r and r["value"] not in (None, ""):
                return r["value"]
    except Exception:
        pass
    return default


def set_setting(key, value, is_secret=0):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value, is_secret, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value=excluded.value,
              is_secret=excluded.is_secret,
              updated_at=excluded.updated_at
            """,
            (key, value or "", int(is_secret), now_iso(), now_iso()),
        )



def apply_settings_payload(payload: dict | None, keys=None):
    if not payload:
        return
    allowed = {
        "ollama_base_url": 0,
        "ollama_model": 0,
        "calendar_ics_url": 1,
        "ha_webhook_url": 1,
        "n8n_ingest_token": 1,
        "share_base_url": 0,
        "self_base_url": 0,
    }
    if keys:
        allowed = {k: v for k, v in allowed.items() if k in keys}
    for key, secret in allowed.items():
        if key in payload:
            value = safe_db_text(payload.get(key) or "").strip()
            if value:
                set_setting(key, value, secret)


def masked_value(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return value[:6] + "••••••" + value[-4:]


def current_ollama_url():
    return get_setting("ollama_base_url", OLLAMA_BASE_URL).rstrip("/")


def current_ollama_model():
    return get_setting("ollama_model", OLLAMA_MODEL) or OLLAMA_MODEL


def current_calendar_ics_url():
    return get_setting("calendar_ics_url", CALENDAR_ICS_URL)


def current_ha_webhook_url():
    return get_setting("ha_webhook_url", HA_WEBHOOK_URL)


def current_n8n_token():
    return get_setting("n8n_ingest_token", N8N_INGEST_TOKEN)


def current_share_base_url():
    return get_setting("share_base_url", SHARE_BASE_URL).rstrip("/")


def current_self_base_url():
    return get_setting("self_base_url", SELF_BASE_URL).rstrip("/") or SELF_BASE_URL.rstrip("/")


def log_event(level, area, message, details="", related_type="", related_id=None):
    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO app_logs(level, area, message, details, related_type, related_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (level, area, message, (details or "")[:8000], related_type, related_id, now_iso()),
            )
    except Exception:
        pass


def ensure_col(conn, table, col, definition):
    existing = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")


def init_db():
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT '',
                is_secret INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone_suffix TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS source_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                file_name TEXT NOT NULL,
                original_name TEXT NOT NULL,
                source_type TEXT DEFAULT 'upload',
                source_url TEXT DEFAULT '',
                document_date TEXT DEFAULT '',
                historical_only INTEGER DEFAULT 0,
                status TEXT DEFAULT 'processed',
                created_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                source_document_id INTEGER,
                title TEXT NOT NULL,
                doctor_name TEXT DEFAULT '',
                crm TEXT DEFAULT '',
                issue_date TEXT DEFAULT '',
                validity_date TEXT DEFAULT '',
                file_name TEXT DEFAULT '',
                page_range TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                historical_only INTEGER DEFAULT 0,
                ai_json TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE RESTRICT,
                FOREIGN KEY(source_document_id) REFERENCES source_documents(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS prescription_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prescription_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                medication_name TEXT NOT NULL,
                substance TEXT DEFAULT '',
                dosage TEXT DEFAULT '',
                frequency TEXT DEFAULT '',
                duration TEXT DEFAULT '',
                quantity_text TEXT DEFAULT '',
                route TEXT DEFAULT '',
                continuous_use INTEGER DEFAULT 0,
                estimated_start_date TEXT DEFAULT '',
                estimated_end_date TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                FOREIGN KEY(prescription_id) REFERENCES prescriptions(id) ON DELETE CASCADE,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS exam_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                source_document_id INTEGER,
                title TEXT NOT NULL,
                doctor_name TEXT DEFAULT '',
                crm TEXT DEFAULT '',
                issue_date TEXT DEFAULT '',
                file_name TEXT DEFAULT '',
                page_range TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                scheduled_at TEXT DEFAULT '',
                scheduled_location TEXT DEFAULT '',
                scheduled_calendar_title TEXT DEFAULT '',
                performed_at TEXT DEFAULT '',
                result_expected_at TEXT DEFAULT '',
                result_reminded_at TEXT DEFAULT '',
                result_notes TEXT DEFAULT '',
                historical_only INTEGER DEFAULT 0,
                ai_json TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE RESTRICT,
                FOREIGN KEY(source_document_id) REFERENCES source_documents(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS exam_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_order_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                exam_name TEXT NOT NULL,
                normalized_name TEXT DEFAULT '',
                tuss TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY(exam_order_id) REFERENCES exam_orders(id) ON DELETE CASCADE,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS exam_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                source_document_id INTEGER,
                lab_name TEXT DEFAULT '',
                result_date TEXT DEFAULT '',
                ai_json TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(source_document_id) REFERENCES source_documents(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS exam_markers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exam_result_id INTEGER,
                profile_id INTEGER NOT NULL,
                marker_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                value REAL,
                value_text TEXT DEFAULT '',
                unit TEXT DEFAULT '',
                reference_range TEXT DEFAULT '',
                result_date TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(exam_result_id) REFERENCES exam_results(id) ON DELETE CASCADE,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS treatments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT DEFAULT 'medication',
                dosage TEXT DEFAULT '',
                frequency_text TEXT DEFAULT '',
                interval_days INTEGER DEFAULT 0,
                requires_prescription INTEGER DEFAULT 1,
                default_action TEXT DEFAULT 'taken',
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS treatment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                treatment_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                prescription_id INTEGER,
                scheduled_for TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                action_label TEXT DEFAULT '',
                completed_at TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                ha_notified_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(treatment_id) REFERENCES treatments(id) ON DELETE CASCADE,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(prescription_id) REFERENCES prescriptions(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS medication_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                medication_name TEXT NOT NULL,
                unit_label TEXT DEFAULT 'unidade',
                units_on_hand REAL DEFAULT 0,
                low_stock_threshold REAL DEFAULT 1,
                requires_prescription INTEGER DEFAULT 0,
                treatment_id INTEGER DEFAULT 0,
                default_frequency TEXT DEFAULT '',
                interval_days INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                last_low_stock_notified_at TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS inventory_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inventory_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                quantity REAL NOT NULL,
                total_price REAL DEFAULT 0,
                unit_price REAL DEFAULT 0,
                purchase_date TEXT DEFAULT '',
                vendor TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(inventory_id) REFERENCES medication_inventory(id) ON DELETE CASCADE,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS inventory_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inventory_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                movement_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                units_after REAL DEFAULT 0,
                related_type TEXT DEFAULT '',
                related_id INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(inventory_id) REFERENCES medication_inventory(id) ON DELETE CASCADE,
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS inbox_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                source_document_id INTEGER,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT DEFAULT 'needs_review',
                historical_only INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                reviewed_at TEXT DEFAULT '',
                FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(source_document_id) REFERENCES source_documents(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS app_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                area TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT DEFAULT '',
                related_type TEXT DEFAULT '',
                related_id INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ingest_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                progress INTEGER NOT NULL DEFAULT 0,
                stage TEXT DEFAULT '',
                message TEXT DEFAULT '',
                error TEXT DEFAULT '',
                result_json TEXT DEFAULT '',
                file_name TEXT DEFAULT '',
                original_name TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                qr_text TEXT DEFAULT '',
                profile_id INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS share_tokens (
                token TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS calendar_events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                ends_at TEXT DEFAULT '',
                location TEXT DEFAULT '',
                description TEXT DEFAULT '',
                source TEXT DEFAULT 'ics',
                matched_keyword TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT ''
            );
            """)

            # Migrações idempotentes para quem vem das versões antigas.
            for table, cols in {
                "source_documents": {
                    "file_sha256": "TEXT DEFAULT ''",
                    "historical_only": "INTEGER DEFAULT 0",
                    "source_url": "TEXT DEFAULT ''",
                    "status": "TEXT DEFAULT 'processed'",
                },
                "prescriptions": {
                    "historical_only": "INTEGER DEFAULT 0",
                    "page_range": "TEXT DEFAULT ''",
                    "ai_json": "TEXT DEFAULT ''",
                },
                "exam_orders": {
                    "historical_only": "INTEGER DEFAULT 0",
                    "page_range": "TEXT DEFAULT ''",
                    "ai_json": "TEXT DEFAULT ''",
                    "scheduled_at": "TEXT DEFAULT ''",
                    "scheduled_location": "TEXT DEFAULT ''",
                    "scheduled_calendar_title": "TEXT DEFAULT ''",
                    "performed_at": "TEXT DEFAULT ''",
                    "result_expected_at": "TEXT DEFAULT ''",
                    "result_reminded_at": "TEXT DEFAULT ''",
                    "result_notes": "TEXT DEFAULT ''",
                },
                "treatments": {
                    "current_prescription_id": "INTEGER DEFAULT 0",
                    "routine_preset": "TEXT DEFAULT ''",
                    "preferred_time": "TEXT DEFAULT ''",
                    "daily_times": "TEXT DEFAULT ''",
                    "inventory_id": "INTEGER DEFAULT 0",
                    "supply_total": "INTEGER DEFAULT 0",
                    "supply_used": "INTEGER DEFAULT 0",
                    "rule_notes": "TEXT DEFAULT ''",
                    "side_mode": "TEXT DEFAULT ''",
                    "preferred_start_side": "TEXT DEFAULT ''",
                    "side_anchor_treatment": "TEXT DEFAULT ''"
                },
                "treatment_events": {
                    "ha_notified_at": "TEXT DEFAULT ''",
                    "action_label": "TEXT DEFAULT ''",
                    "updated_at": "TEXT DEFAULT ''",
                    "scheduled_at": "TEXT DEFAULT ''",
                    "linked_calendar_event_id": "TEXT DEFAULT ''",
                    "administration_side": "TEXT DEFAULT ''",
                    "reminder_due_at": "TEXT DEFAULT ''",
                    "reminder_sent_at": "TEXT DEFAULT ''",
                    "prescription_file_name": "TEXT DEFAULT ''"
                },
                "calendar_events": {
                    "classification_type": "TEXT DEFAULT ''",
                    "classification_confidence": "REAL DEFAULT 0",
                    "classification_reason": "TEXT DEFAULT ''"
                },
                "medication_inventory": {
                    "unit_label": "TEXT DEFAULT 'unidade'",
                    "routine_preset": "TEXT DEFAULT ''",
                    "preferred_time": "TEXT DEFAULT ''",
                    "units_on_hand": "REAL DEFAULT 0",
                    "low_stock_threshold": "REAL DEFAULT 1",
                    "dose_quantity": "REAL DEFAULT 1",
                    "requires_prescription": "INTEGER DEFAULT 0",
                    "prescription_id": "INTEGER DEFAULT 0",
                    "treatment_id": "INTEGER DEFAULT 0",
                    "default_frequency": "TEXT DEFAULT ''",
                    "interval_days": "INTEGER DEFAULT 0",
                    "active": "INTEGER DEFAULT 1",
                    "last_low_stock_notified_at": "TEXT DEFAULT ''",
                    "notes": "TEXT DEFAULT ''",
                    "updated_at": "TEXT DEFAULT ''"
                },
            }.items():
                for col, definition in cols.items():
                    ensure_col(conn, table, col, definition)

            conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS health_fts USING fts5(
                entity_type,
                entity_id UNINDEXED,
                profile_name,
                title,
                body,
                tokenize='unicode61 remove_diacritics 2'
            )
            """)

            count = conn.execute("SELECT COUNT(*) c FROM profiles").fetchone()["c"]
            if count == 0:
                conn.execute("INSERT INTO profiles(name, phone_suffix, notes, created_at) VALUES (?, ?, ?, ?)", ("Paulo", "", "", now_iso()))
                conn.execute("INSERT INTO profiles(name, phone_suffix, notes, created_at) VALUES (?, ?, ?, ?)", ("Filho", "", "", now_iso()))

            default_settings = [
                ("ollama_base_url", OLLAMA_BASE_URL, 0),
                ("ollama_model", OLLAMA_MODEL, 0),
                ("calendar_ics_url", CALENDAR_ICS_URL, 1),
                ("ha_webhook_url", HA_WEBHOOK_URL, 1),
                ("n8n_ingest_token", N8N_INGEST_TOKEN, 1),
                ("share_base_url", SHARE_BASE_URL, 0),
                ("self_base_url", SELF_BASE_URL, 0),
            ]
            for key, value, is_secret in default_settings:
                exists = conn.execute("SELECT key FROM settings WHERE key=?", (key,)).fetchone()
                if not exists and value:
                    conn.execute(
                        "INSERT INTO settings(key, value, is_secret, created_at) VALUES (?, ?, ?, ?)",
                        (key, value, is_secret, now_iso()),
                    )

            conn.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version','7') ON CONFLICT(key) DO UPDATE SET value='7'")
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def run_cmd(args, timeout=120):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as e:
        return 1, "", str(e)


def require_profile(conn, profile_id):
    r = conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
    if not r:
        raise HTTPException(400, "Perfil inválido.")
    return r


def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace("R$", "").replace(" ", "")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return default


def is_private_or_local_host(hostname):
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
    except Exception:
        return hostname.lower() in {"localhost", "host.docker.internal"} or hostname.endswith(".local")


def safe_url_for_download(raw_url):
    try:
        p = urlparse(raw_url)
        if p.scheme not in {"http", "https"}:
            return False
        if not p.hostname:
            return False
        if is_private_or_local_host(p.hostname):
            return False
        return True
    except Exception:
        return False


def download_pdf_from_url(url):
    if not safe_url_for_download(url):
        log_event("warning", "download", "URL bloqueada por segurança.", url)
        return ""
    r = requests.get(url, timeout=30, allow_redirects=True)
    if not safe_url_for_download(r.url):
        log_event("warning", "download", "Redirect bloqueado por segurança.", r.url)
        return ""
    ct = r.headers.get("content-type", "")
    if r.ok and ("pdf" in ct.lower() or r.content[:4] == b"%PDF"):
        name = f"{uuid.uuid4().hex}.pdf"
        (UPLOAD_DIR / name).write_bytes(r.content)
        return name
    raise HTTPException(400, f"Link não retornou PDF direto. Content-Type: {ct}")


def extract_qr_from_image(path):
    try:
        img = Image.open(path)
        found = decode(img)
        return "\n".join([x.data.decode("utf-8", errors="ignore") for x in found])
    except Exception as e:
        log_event("warning", "qr", "Falha ao ler QRCode.", str(e))
        return ""


def extract_qr_from_pdf_first_page(path):
    with tempfile.TemporaryDirectory(prefix="mv_qr_") as tmp:
        prefix = Path(tmp) / "page"
        code, _, err = run_cmd(["pdftoppm", "-png", "-singlefile", "-f", "1", "-l", "1", str(path), str(prefix)], 90)
        img = Path(str(prefix) + ".png")
        if code == 0 and img.exists():
            return extract_qr_from_image(img)
        if err:
            log_event("warning", "qr", "Falha ao converter PDF para QR.", err)
        return ""


def unlock_pdf_to_temp(path, password):
    if not password:
        return path, None, "Sem senha cadastrada."
    tmp = tempfile.TemporaryDirectory(prefix="mv_unlock_")
    out = Path(tmp.name) / "unlocked.pdf"
    try:
        with pikepdf.open(path, password=password) as pdf:
            pdf.save(out)
        return out, tmp, "PDF desbloqueado temporariamente."
    except Exception as e:
        tmp.cleanup()
        log_event("warning", "pdf", "Falha ao desbloquear PDF.", str(e))
        return path, None, "PDF não desbloqueado."


def ocr_pdf_page_text(path, page_num):
    with tempfile.TemporaryDirectory(prefix="mv_page_") as tmp:
        prefix = Path(tmp) / "page"
        code, _, err = run_cmd(["pdftoppm", "-png", "-r", "220", "-f", str(page_num), "-l", str(page_num), str(path), str(prefix)], 120)
        imgs = sorted(Path(tmp).glob("page-*.png"))
        if code == 0 and imgs:
            try:
                return pytesseract.image_to_string(Image.open(imgs[0]), lang="por+eng")
            except Exception as e:
                log_event("warning", "ocr", f"Falha OCR página {page_num}.", str(e))
        return ""


def pdf_text_layer_page(path, page_num):
    with tempfile.TemporaryDirectory(prefix="mv_txt_") as tmp:
        txt = Path(tmp) / "out.txt"
        code, _, _ = run_cmd(["pdftotext", "-f", str(page_num), "-l", str(page_num), str(path), str(txt)], 60)
        if code == 0 and txt.exists():
            return txt.read_text(errors="ignore")
        return ""


def ocr_pdf_by_page(path):
    reader = PdfReader(str(path))
    total = len(reader.pages)
    pages = []
    for page_num in range(1, min(total, OCR_MAX_PAGES) + 1):
        text = pdf_text_layer_page(path, page_num)
        method = "text-layer"
        if len(text.strip()) < 60:
            text = ocr_pdf_page_text(path, page_num)
            method = "image-ocr"
        pages.append({"page": page_num, "text": text or "", "method": method})
    return pages


def ocr_image(path):
    try:
        return pytesseract.image_to_string(Image.open(path), lang="por+eng")
    except Exception as e:
        log_event("warning", "ocr", "Falha OCR imagem.", str(e))
        return ""


def classify_page(text):
    value = (text or "").lower()

    prescription_terms = [
        "receita", "prescrição", "prescricao", "uso oral", "uso contínuo", "uso continuo",
        "tomar", "aplicar", "comprimido", "cápsula", "capsula", "ampola", "deposteron",
        "mounjaro", "medicamento", "posologia"
    ]
    exam_order_terms = [
        "solicito", "solicitação de exames", "solicitacao de exames", "pedido de exame",
        "exames solicitados", "laboratório", "laboratorio", "hemograma", "tsh",
        "testosterona", "ultrassonografia", "ressonância", "tomografia"
    ]
    result_terms = [
        "resultado", "laudo", "valor de referência", "valor de referencia", "material:",
        "método", "metodo", "liberado em", "coleta", "resultado do exame"
    ]

    scores = {
        "prescription": sum(1 for t in prescription_terms if t in value),
        "exam_order": sum(1 for t in exam_order_terms if t in value),
        "exam_result": sum(1 for t in result_terms if t in value),
    }

    best_score = max(scores.values())
    # Evita classificar OCR curto/ruidoso como receita por empate ou score baixo.
    if best_score < 2:
        return "document"

    winners = [kind for kind, score in scores.items() if score == best_score]
    if len(winners) != 1:
        return "document"

    return winners[0]


    prescription_terms = [
        "receita", "prescrição", "prescricao", "uso oral", "uso contínuo", "uso continuo",
        "tomar", "aplicar", "comprimido", "cápsula", "capsula", "ampola", "deposteron",
        "mounjaro", "medicamento", "posologia"
    ]
    exam_order_terms = [
        "solicito", "solicitação de exames", "solicitacao de exames", "pedido de exame",
        "exames solicitados", "laboratório", "laboratorio", "hemograma", "tsh",
        "testosterona", "ultrassonografia", "ressonância", "tomografia"
    ]
    result_terms = [
        "resultado", "laudo", "valor de referência", "valor de referencia", "material:",
        "método", "metodo", "liberado em", "coleta", "resultado do exame"
    ]

    scores = {
        "prescription": sum(1 for t in prescription_terms if t in value),
        "exam_order": sum(1 for t in exam_order_terms if t in value),
        "exam_result": sum(1 for t in result_terms if t in value),
    }

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "document"
    return best



def group_pages(pages):
    groups = []
    current = None
    for p in pages:
        kind = classify_page(p["text"])
        if not current or current["kind"] != kind:
            current = {"kind": kind, "pages": [], "text": ""}
            groups.append(current)
        current["pages"].append(p["page"])
        current["text"] += "\n\n--- página %s ---\n\n%s" % (p["page"], p["text"])
    return groups


def write_pdf_pages(src_path, pages):
    reader = PdfReader(str(src_path))
    writer = PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p - 1])
    name = f"{uuid.uuid4().hex}.pdf"
    out = UPLOAD_DIR / name
    with out.open("wb") as f:
        writer.write(f)
    return name


def analyze_group_with_ollama(kind, text):
    if not current_ollama_url():
        return {"erro": "OLLAMA_BASE_URL não configurado"}

    prompt = f"""
Extraia os dados médicos em JSON válido, sem markdown.

Tipo detectado: {kind}

Campos esperados:
- titulo
- paciente
- medico
- crm
- data_documento
- validade_receita
- medicamentos: lista com nome, substancia, dosagem, frequencia, duracao, quantidade, via, uso_continuo
- exames_solicitados: lista com nome, tuss
- resultados_exames: lista com nome, valor, unidade, referencia, data_resultado, laboratorio
- observacoes

Texto:
{text[:15000]}
"""
    try:
        r = requests.post(
            f"{current_ollama_url()}/api/generate",
            json={"model": current_ollama_model(), "prompt": prompt, "format": "json", "stream": False},
            timeout=240,
        )
        r.raise_for_status()
        out = r.json().get("response", "{}")
        try:
            return json.loads(out)
        except Exception:
            return {"raw": out}
    except Exception as e:
        log_event("error", "ollama", "Falha na análise Ollama.", str(e))
        return {"erro": str(e)}



def extract_text_from_purchase_file(path):
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            pages = ocr_pdf_by_page(path)
            return "\n\n".join([p.get("text", "") for p in pages]).strip()
        return ocr_image(path).strip()
    except Exception as e:
        log_event("warning", "inventory", "Falha ao extrair texto da compra.", str(e))
        return ""


def normalize_unit_label(value, name=""):
    text = text_norm(f"{value} {name}")
    mapping = [
        ("ampola", "ampola"),
        ("ampolas", "ampola"),
        ("caneta", "caneta"),
        ("canetas", "caneta"),
        ("comprimido", "comprimido"),
        ("comprimidos", "comprimido"),
        ("capsula", "cápsula"),
        ("cápsula", "cápsula"),
        ("capsulas", "cápsula"),
        ("cápsulas", "cápsula"),
        ("cartela", "cartela"),
        ("cartelas", "cartela"),
        ("caixa", "caixa"),
        ("caixas", "caixa"),
        ("frasco", "frasco"),
        ("frascos", "frasco"),
        ("sache", "sachê"),
        ("sachê", "sachê"),
        ("dose", "dose"),
        ("doses", "dose"),
        ("ml", "ml"),
    ]
    for key, label in mapping:
        if key in text:
            return label
    return "unidade"


def infer_unit_quantity_from_text(name, quantity):
    q = safe_float(quantity, 0)
    text = text_norm(name)

    # NF/pedido às vezes traz "1 caixa", mas o estoque útil é por unidade de uso.
    if "deposteron" in text and ("caixa" in text or q <= 2):
        return max(q * 3, 3), "ampola"

    if "mounjaro" in text and ("caixa" in text or "caneta" in text):
        # Cada caixa geralmente representa 1 caneta/caneta dose semanal, manter unidade caneta.
        return max(q, 1), "caneta"

    unit = normalize_unit_label("", name)
    return max(q, 1), unit


def fallback_purchase_parse(text):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    purchase_date = today_date()
    vendor = ""
    joined = "\n".join(lines)

    m = re.search(r"(\d{2}[/-]\d{2}[/-]\d{4})", joined)
    if m:
        raw = m.group(1).replace("/", "-")
        dd, mm, yyyy = raw.split("-")
        purchase_date = f"{yyyy}-{mm}-{dd}"

    for ln in lines[:12]:
        if any(x in text_norm(ln) for x in ["drogaria", "farmacia", "farmácia", "raia", "pacheco", "drogasil", "panvel"]):
            vendor = ln[:120]
            break

    medicine_terms = ["deposteron", "mounjaro", "ozempic", "centrum", "vitamina", "testosterona", "dipirona", "ibuprofeno", "paracetamol"]
    items = []
    for ln in lines:
        norm = text_norm(ln)
        if any(t in norm for t in medicine_terms):
            qty = 1
            qmatch = re.search(r"(?:qtd|qtde|quantidade)?\s*[:x]?\s*(\d+(?:[,.]\d+)?)", norm)
            if qmatch:
                qty = safe_float(qmatch.group(1), 1)
            total = 0
            prices = re.findall(r"(?:r\$)?\s*(\d+[,.]\d{2})", ln.lower())
            if prices:
                total = safe_float(prices[-1], 0)
            quantity, unit = infer_unit_quantity_from_text(ln, qty)
            clean_name = re.sub(r"\s+", " ", ln)
            clean_name = re.sub(r"(?i)r\$\s*\d+[,.]\d{2}", "", clean_name).strip(" -|")
            items.append({
                "name": clean_name[:120],
                "quantity": quantity,
                "unit_label": unit,
                "total_price": total,
                "requires_prescription": any(x in norm for x in ["deposteron", "mounjaro", "ozempic", "testosterona"]),
                "confidence": 0.55,
            })

    # dedup simples
    unique = []
    seen = set()
    for it in items:
        key = text_norm(it.get("name", ""))[:40]
        if key and key not in seen:
            seen.add(key)
            unique.append(it)

    return {
        "vendor": vendor,
        "purchase_date": purchase_date,
        "items": unique,
        "raw_text": text[:5000],
        "source": "fallback",
    }


def analyze_purchase_with_ollama(text):
    fallback = fallback_purchase_parse(text)
    if not current_ollama_url() or len((text or "").strip()) < 20:
        return fallback

    prompt = f"""
Você é um extrator de compras de farmácia para controle de estoque pessoal.

Extraia de uma nota fiscal, cupom, comprovante ou print de pedido de farmácia.

Responda SOMENTE JSON válido, sem markdown, neste formato:
{{
  "vendor": "nome da farmácia/local",
  "purchase_date": "YYYY-MM-DD",
  "items": [
    {{
      "name": "nome comercial do medicamento/produto",
      "quantity": 1,
      "unit_label": "ampola|caneta|comprimido|cápsula|frasco|cartela|caixa|sachê|gota|ml|dose|unidade",
      "total_price": 0,
      "requires_prescription": false,
      "confidence": 0.0
    }}
  ]
}}

Regras:
- Inclua apenas medicamentos, vitaminas, suplementos ou itens de saúde.
- Ignore frete, desconto, taxa, cashback, endereço, CPF/CNPJ e forma de pagamento.
- Se aparecer Deposteron em caixa, considere 1 caixa = 3 ampolas e unit_label="ampola".
- Se aparecer Mounjaro/Ozempic em caixa/caneta, use unit_label="caneta".
- Se não souber o preço do item, use 0.
- Se não souber a data, use "{today_date()}".
- requires_prescription=true para anabolizantes, testosterona, Deposteron, Mounjaro/Ozempic e medicamentos controlados.

Texto OCR:
{text[:12000]}
"""
    try:
        r = requests.post(
            f"{current_ollama_url()}/api/generate",
            json={"model": current_ollama_model(), "prompt": prompt, "format": "json", "stream": False},
            timeout=180,
        )
        r.raise_for_status()
        out = r.json().get("response", "{}")
        parsed = json.loads(out)
        items = parsed.get("items") if isinstance(parsed, dict) else []
        clean_items = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = safe_db_text(item.get("name") or "").strip()
            if not name:
                continue
            quantity = safe_float(item.get("quantity"), 1)
            if quantity <= 0:
                quantity = 1
            unit_label = normalize_unit_label(item.get("unit_label") or "", name)
            # Ajusta casos comuns em que a IA manteve "caixa".
            quantity, inferred_unit = infer_unit_quantity_from_text(name, quantity)
            if inferred_unit != "unidade":
                unit_label = inferred_unit
            clean_items.append({
                "name": name,
                "quantity": quantity,
                "unit_label": unit_label,
                "total_price": safe_float(item.get("total_price"), 0),
                "requires_prescription": bool(item.get("requires_prescription")),
                "confidence": safe_float(item.get("confidence"), 0.7),
            })
        if clean_items:
            return {
                "vendor": safe_db_text(parsed.get("vendor") or fallback.get("vendor") or "").strip(),
                "purchase_date": safe_db_text(parsed.get("purchase_date") or fallback.get("purchase_date") or today_date()).strip(),
                "items": clean_items,
                "raw_text": text[:5000],
                "source": "ollama",
            }
    except Exception as e:
        log_event("warning", "inventory", "Falha na extração IA da compra; usando fallback.", str(e))

    return fallback


def find_matching_inventory_item(conn, profile_id, name):
    needle = text_norm(name)
    if not needle:
        return None
    rows_ = conn.execute(
        "SELECT * FROM medication_inventory WHERE profile_id=? AND active=1 ORDER BY id DESC",
        (profile_id,),
    ).fetchall()
    for item in rows_:
        current = text_norm(item["medication_name"])
        if not current:
            continue
        if current in needle or needle in current:
            return item
        # match por primeira palavra relevante
        tokens = [t for t in re.split(r"\W+", needle) if len(t) >= 5]
        if tokens and any(t in current for t in tokens[:3]):
            return item
    return None


def upsert_inventory_from_purchase(conn, profile_id, item, purchase_date, vendor):
    name = safe_db_text(item.get("name") or "").strip()
    if not name:
        return {"status": "ignored", "reason": "missing_name"}

    quantity = safe_float(item.get("quantity"), 1)
    if quantity <= 0:
        quantity = 1

    unit_label = normalize_unit_label(item.get("unit_label") or "", name)
    total_price = safe_float(item.get("total_price"), 0)
    unit_price = total_price / quantity if total_price and quantity else 0
    requires_prescription = int(bool(item.get("requires_prescription")))

    existing = find_matching_inventory_item(conn, profile_id, name)

    if existing:
        inventory_id = existing["id"]
        new_total = float(existing["units_on_hand"] or 0) + quantity
        conn.execute(
            """
            UPDATE medication_inventory
            SET units_on_hand=?, unit_label=COALESCE(NULLIF(?,''), unit_label),
                requires_prescription=MAX(IFNULL(requires_prescription,0), ?),
                last_low_stock_notified_at='', updated_at=?
            WHERE id=?
            """,
            (new_total, unit_label, requires_prescription, now_iso(), inventory_id),
        )
        action = "updated"
    else:
        cur = conn.execute(
            """
            INSERT INTO medication_inventory(
                profile_id, medication_name, unit_label, units_on_hand, low_stock_threshold,
                dose_quantity, requires_prescription, default_frequency, interval_days, notes, created_at
            )
            VALUES (?, ?, ?, ?, 1, 1, ?, '', 0, ?, ?)
            """,
            (
                profile_id,
                name,
                unit_label,
                quantity,
                requires_prescription,
                f"Importado de nota fiscal/pedido em {purchase_date}.",
                now_iso(),
            ),
        )
        inventory_id = cur.lastrowid
        new_total = quantity
        action = "created"

    conn.execute(
        """
        INSERT INTO inventory_purchases(inventory_id, profile_id, quantity, total_price, unit_price, purchase_date, vendor, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            inventory_id,
            profile_id,
            quantity,
            total_price,
            unit_price,
            safe_db_text(purchase_date or today_date()),
            safe_db_text(vendor or ""),
            "Importado por nota fiscal/print de pedido",
            now_iso(),
        ),
    )
    conn.execute(
        """
        INSERT INTO inventory_movements(inventory_id, profile_id, movement_type, quantity, units_after, notes, created_at)
        VALUES (?, ?, 'purchase', ?, ?, ?, ?)
        """,
        (
            inventory_id,
            profile_id,
            quantity,
            new_total,
            "Compra importada por nota fiscal/print de pedido",
            now_iso(),
        ),
    )
    return {"status": action, "inventory_id": inventory_id, "name": name, "quantity": quantity, "unit_label": unit_label}


def normalize_exam_name(name):
    n = (name or "").lower()
    table = [
        ("hormônio tireoestimulante", "TSH"),
        ("tsh", "TSH"),
        ("hemoglobina glicada", "Hemoglobina glicada"),
        ("hba1c", "Hemoglobina glicada"),
        ("25-hidroxivitamina d", "Vitamina D"),
        ("vitamina d", "Vitamina D"),
        ("testosterona total", "Testosterona total"),
        ("testosterona", "Testosterona total"),
        ("t4 livre", "T4 livre"),
        ("ferritina", "Ferritina"),
        ("vitamina b12", "Vitamina B12"),
        ("creatinina", "Creatinina"),
        ("glicemia", "Glicemia em jejum"),
        ("hemograma", "Hemograma"),
        ("colesterol", "Colesterol"),
        ("triglicer", "Triglicerídeos"),
    ]
    for key, value in table:
        if key in n:
            return value
    return (name or "").strip()


def estimate_end_date(issue_date, duration, continuous=False):
    if continuous:
        return ""
    start = parse_date(issue_date) or datetime.now().date()
    text = (duration or "").lower()
    m = re.search(r"(\d+)\s*(dia|dias)", text)
    if m:
        return (start + timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s*(semana|semanas)", text)
    if m:
        return (start + timedelta(days=7 * int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s*(m[eê]s|meses)", text)
    if m:
        return (start + timedelta(days=30 * int(m.group(1)))).isoformat()
    return ""


def infer_interval_days(frequency):
    text = (frequency or "").lower()
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if "hora" in text or "h" in text:
        return 1
    if ("dia" in text or "dias" in text) and nums:
        return nums[0]
    if "30" in text and "dia" in text:
        return 30
    if "15" in text and "dia" in text:
        return 15
    if "10" in text and "dia" in text:
        return 10
    if "7" in text and "dia" in text:
        return 7
    if "3" in text and "dia" in text:
        return 3
    if "semana" in text or "1x/sem" in text or "1 vez por semana" in text:
        return 7
    if "dia" in text or "diário" in text or "diario" in text:
        return 1
    return 0


def is_historical(historical_only, issue_date, end_date):
    if historical_only:
        return True
    today = datetime.now().date()
    if end_date:
        d = parse_date(end_date)
        if d and d < today:
            return True
    return False


def index_fts(conn, entity_type, entity_id, profile_name, title, body):
    conn.execute(
        "INSERT INTO health_fts(entity_type, entity_id, profile_name, title, body) VALUES (?, ?, ?, ?, ?)",
        (entity_type, entity_id, profile_name or "", title or "", body or ""),
    )


def detect_supply_total(medication_name, quantity_text):
    text = text_norm(quantity_text)
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if "caix" in text and nums:
        caixas = nums[0]
        if "deposteron" in text_norm(medication_name):
            return caixas * 3
    if any(x in text for x in ["ampola", "ampolas", "seringa", "seringas", "caneta", "canetas"]) and nums:
        return max(nums)
    if nums:
        known = [n for n in nums if n in {1, 2, 3, 4, 5, 6, 8, 12}]
        if known:
            return max(known)
    if "deposteron" in text_norm(medication_name):
        return 3
    return 0


def infer_treatment_rule_defaults(medication_name):
    name = text_norm(medication_name)
    if "deposteron" in name:
        return {"side_mode": "alternate", "preferred_start_side": "direito", "side_anchor_treatment": ""}
    if "mounjaro" in name:
        return {"side_mode": "opposite_last_anchor", "preferred_start_side": "", "side_anchor_treatment": "deposteron"}
    return {"side_mode": "", "preferred_start_side": "", "side_anchor_treatment": ""}


def parse_rule_notes(base_cfg, rule_notes):
    cfg = dict(base_cfg or {})
    notes = text_norm(rule_notes)
    if notes:
        if "altern" in notes:
            cfg["side_mode"] = "alternate"
        if "lado oposto" in notes or "oposto da ultima" in notes or "oposto da última" in notes:
            cfg["side_mode"] = "opposite_last_anchor"
            if "deposteron" in notes:
                cfg["side_anchor_treatment"] = "deposteron"
        if "comece" in notes and "direit" in notes:
            cfg["preferred_start_side"] = "direito"
        if "comece" in notes and "esquerd" in notes:
            cfg["preferred_start_side"] = "esquerdo"
    return cfg


def find_anchor_treatment(conn, profile_id, anchor_name):
    if not anchor_name:
        return None
    return conn.execute(
        "SELECT * FROM treatments WHERE profile_id=? AND lower(name) LIKE ? ORDER BY id DESC LIMIT 1",
        (profile_id, f"%{text_norm(anchor_name)}%"),
    ).fetchone()


def last_completed_side(conn, treatment_id):
    r = conn.execute(
        """
        SELECT administration_side
        FROM treatment_events
        WHERE treatment_id=?
          AND status IN ('applied','taken')
          AND IFNULL(administration_side,'')<>''
        ORDER BY COALESCE(completed_at, updated_at, created_at) DESC, id DESC
        LIMIT 1
        """,
        (treatment_id,),
    ).fetchone()
    return r["administration_side"] if r else ""


def next_side_for_treatment(conn, treatment):
    cfg = infer_treatment_rule_defaults(treatment["name"])
    cfg["side_mode"] = treatment["side_mode"] or cfg["side_mode"]
    cfg["preferred_start_side"] = treatment["preferred_start_side"] or cfg["preferred_start_side"]
    cfg["side_anchor_treatment"] = treatment["side_anchor_treatment"] or cfg["side_anchor_treatment"]
    cfg = parse_rule_notes(cfg, treatment["rule_notes"])

    if cfg.get("side_mode") == "alternate":
        last_side = last_completed_side(conn, treatment["id"])
        return opposite_side(last_side) if last_side else (cfg.get("preferred_start_side") or "direito")

    if cfg.get("side_mode") == "opposite_last_anchor":
        anchor = find_anchor_treatment(conn, treatment["profile_id"], cfg.get("side_anchor_treatment") or "deposteron")
        anchor_last = last_completed_side(conn, anchor["id"]) if anchor else ""
        if anchor_last:
            return opposite_side(anchor_last)
        if anchor:
            anchor_cfg = parse_rule_notes(infer_treatment_rule_defaults(anchor["name"]), anchor["rule_notes"])
            if anchor_cfg.get("preferred_start_side"):
                return opposite_side(anchor_cfg.get("preferred_start_side"))
        return cfg.get("preferred_start_side") or "esquerdo"
    return ""


def find_calendar_event_for_treatment(conn, treatment, from_dt=None, exclude_event_id=0):
    from_dt = from_dt or datetime.now()
    if exclude_event_id:
        used_ids = {r[0] for r in conn.execute("SELECT linked_calendar_event_id FROM treatment_events WHERE status='pending' AND id<>? AND IFNULL(linked_calendar_event_id,'')<>'-' AND IFNULL(linked_calendar_event_id,'')<>''", (exclude_event_id,)).fetchall()}
    else:
        used_ids = {r[0] for r in conn.execute("SELECT linked_calendar_event_id FROM treatment_events WHERE status='pending' AND IFNULL(linked_calendar_event_id,'')<>''").fetchall()}
    events = conn.execute(
        "SELECT * FROM calendar_events WHERE starts_at >= ? ORDER BY starts_at ASC LIMIT 120",
        ((from_dt - timedelta(days=2)).isoformat(timespec='minutes'),),
    ).fetchall()

    name = text_norm(treatment["name"])
    injection = treatment["kind"] == "injection" or is_injection_medication(treatment["name"])
    profile_name = text_norm(treatment["profile_name"]) if hasattr(treatment, "keys") and "profile_name" in treatment.keys() else ""

    # Tokens por medicamento. Deposteron geralmente aparece no app como serviço genérico de aplicação,
    # então não exigir o nome do remédio no título do calendário.
    med_tokens = []
    if "deposteron" in name:
        med_tokens = ["deposteron"]
    elif "mounjaro" in name:
        med_tokens = ["mounjaro"]

    best = None
    best_score = -1
    for ev in events:
        if ev["id"] in used_ids:
            continue
        payload = text_norm(f"{ev['title']} {ev['description']} {ev['location']}")
        ev_dt = parse_datetime_value(ev["starts_at"])
        if not ev_dt or ev_dt < from_dt - timedelta(days=2):
            continue

        score = 0
        if profile_name and profile_name in payload:
            score += 2

        if med_tokens and any(token in payload for token in med_tokens):
            score += 10
        elif name and name in payload:
            score += 8

        if injection:
            if any(x in payload for x in ["aplica", "aplicacao", "aplicação", "injet", "medicamentos injet", "raia", "drogaria", "farmacia", "farmácia"]):
                score += 12

        # Excluir consultas/exames genéricos para tratamento injetável.
        if injection and any(x in payload for x in ["consulta", "exame", "laboratorio", "laboratório"]) and not any(x in payload for x in ["aplica", "injet", "raia", "drogaria", "farmacia", "farmácia"]):
            score -= 8

        if score < 6:
            continue

        # Bônus para evento mais próximo.
        days = max((ev_dt.date() - from_dt.date()).days, 0)
        score += max(0, 10 - days)

        if score > best_score:
            best = ev
            best_score = score
    return best


def next_interval_candidate(issue_date, interval_days):
    candidate = parse_date(issue_date) or datetime.now().date()
    if interval_days <= 0:
        return candidate
    today = datetime.now().date()
    while candidate < today:
        candidate = candidate + timedelta(days=interval_days)
    return candidate


def plan_next_treatment_event(conn, treatment, prescription_id, issue_date, prescription_file_name="", exclude_event_id=0):
    remaining = int(treatment["supply_total"] or 0) - int(treatment["supply_used"] or 0)
    if int(treatment["requires_prescription"] or 0) and int(treatment["supply_total"] or 0) > 0 and remaining <= 0:
        return None

    reference_dt = datetime.now()
    calendar_event = find_calendar_event_for_treatment(conn, treatment, reference_dt, exclude_event_id)
    side = next_side_for_treatment(conn, treatment)
    if calendar_event:
        starts_at = calendar_event["starts_at"]
        starts_dt = parse_datetime_value(starts_at)
        return {
            "scheduled_for": starts_dt.date().isoformat(),
            "scheduled_at": starts_at,
            "linked_calendar_event_id": calendar_event["id"],
            "administration_side": side,
            "reminder_due_at": (starts_dt + timedelta(minutes=30)).isoformat(timespec='minutes') if starts_dt else "",
            "prescription_file_name": prescription_file_name or "",
        }

    # Receita importada NÃO deve criar aplicação automaticamente por intervalo/data da receita.
    # Para medicamentos que exigem receita, a pendência só nasce quando houver evento real no calendário.
    # Isso evita o bug de criar eventos "25/05, 28/05..." só porque uma receita foi cadastrada.
    if int(treatment["requires_prescription"] or 0) or int(prescription_id or 0):
        return None

    candidate = next_interval_candidate(issue_date, int(treatment["interval_days"] or 0))
    return {
        "scheduled_for": candidate.isoformat(),
        "scheduled_at": "",
        "linked_calendar_event_id": "",
        "administration_side": side,
        "reminder_due_at": "",
        "prescription_file_name": prescription_file_name or "",
    }


def upsert_pending_treatment_event(conn, treatment_id, prescription_id, issue_date, prescription_file_name=""):
    treatment = conn.execute(
        "SELECT t.*, p.name profile_name FROM treatments t JOIN profiles p ON p.id=t.profile_id WHERE t.id=?",
        (treatment_id,),
    ).fetchone()
    if not treatment:
        return None

    plan = plan_next_treatment_event(conn, treatment, prescription_id, issue_date, prescription_file_name)
    if not plan:
        return None

    pending = conn.execute(
        "SELECT * FROM treatment_events WHERE treatment_id=? AND status='pending' ORDER BY COALESCE(scheduled_at, scheduled_for) ASC, id ASC LIMIT 1",
        (treatment_id,),
    ).fetchone()
    action_label = "Aplicar" if treatment["kind"] == "injection" or is_injection_medication(treatment["name"]) else "Tomar"
    if pending:
        conn.execute(
            """
            UPDATE treatment_events
            SET prescription_id=?,
                scheduled_for=?,
                scheduled_at=?,
                linked_calendar_event_id=?,
                administration_side=?,
                reminder_due_at=?,
                reminder_sent_at='',
                action_label=?,
                prescription_file_name=?,
                updated_at=?
            WHERE id=?
            """,
            (
                prescription_id,
                plan["scheduled_for"],
                plan["scheduled_at"],
                plan["linked_calendar_event_id"],
                plan["administration_side"],
                plan["reminder_due_at"],
                action_label,
                plan["prescription_file_name"],
                now_iso(),
                pending["id"],
            ),
        )
        return pending["id"]

    cur = conn.execute(
        """
        INSERT INTO treatment_events(
            treatment_id, profile_id, prescription_id, scheduled_for, status, action_label, created_at,
            scheduled_at, linked_calendar_event_id, administration_side, reminder_due_at, prescription_file_name
        )
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            treatment_id, treatment["profile_id"], prescription_id, plan["scheduled_for"], action_label, now_iso(),
            plan["scheduled_at"], plan["linked_calendar_event_id"], plan["administration_side"], plan["reminder_due_at"], plan["prescription_file_name"],
        ),
    )
    return cur.lastrowid


def create_or_update_treatment(conn, profile_id, medication_name, dosage, frequency, prescription_id, issue_date, historical_only, quantity_text=""):
    if historical_only:
        return None

    interval = infer_interval_days(frequency)
    defaults = infer_treatment_rule_defaults(medication_name)
    detected_supply = detect_supply_total(medication_name, quantity_text)
    active_existing = conn.execute(
        "SELECT * FROM treatments WHERE profile_id=? AND lower(name)=lower(?) AND active=1",
        (profile_id, medication_name),
    ).fetchone()

    if active_existing:
        treatment_id = active_existing["id"]
        supply_total = int(active_existing["supply_total"] or 0)
        supply_used = int(active_existing["supply_used"] or 0)
        current_prescription_id = int(active_existing["current_prescription_id"] or 0)
        if prescription_id and prescription_id != current_prescription_id and detected_supply > 0:
            supply_total = detected_supply
            supply_used = 0
            current_prescription_id = prescription_id
        elif detected_supply > supply_total:
            supply_total = detected_supply
        conn.execute(
            """
            UPDATE treatments
            SET dosage=?, frequency_text=?, interval_days=?, updated_at=?, kind=?, requires_prescription=?,
                current_prescription_id=?, supply_total=?, supply_used=?,
                side_mode=CASE WHEN IFNULL(side_mode,'')='' THEN ? ELSE side_mode END,
                preferred_start_side=CASE WHEN IFNULL(preferred_start_side,'')='' THEN ? ELSE preferred_start_side END,
                side_anchor_treatment=CASE WHEN IFNULL(side_anchor_treatment,'')='' THEN ? ELSE side_anchor_treatment END
            WHERE id=?
            """,
            (
                dosage, frequency, interval, now_iso(),
                "injection" if is_injection_medication(medication_name) else "medication",
                1, current_prescription_id or prescription_id or 0, supply_total, supply_used,
                defaults["side_mode"], defaults["preferred_start_side"], defaults["side_anchor_treatment"], treatment_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO treatments(
                profile_id, name, kind, dosage, frequency_text, interval_days, requires_prescription,
                default_action, active, created_at, current_prescription_id, supply_total, supply_used,
                side_mode, preferred_start_side, side_anchor_treatment
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id, medication_name,
                "injection" if is_injection_medication(medication_name) else "medication",
                dosage, frequency, interval, 1,
                "applied" if is_injection_medication(medication_name) else "taken",
                1, now_iso(), prescription_id or 0, detected_supply, 0,
                defaults["side_mode"], defaults["preferred_start_side"], defaults["side_anchor_treatment"],
            ),
        )
        treatment_id = cur.lastrowid

    rx = conn.execute("SELECT file_name FROM prescriptions WHERE id=?", (prescription_id,)).fetchone() if prescription_id else None
    upsert_pending_treatment_event(conn, treatment_id, prescription_id, issue_date, rx["file_name"] if rx else "")
    return treatment_id


def save_group(conn, profile, source_id, source_file, group, historical_only, fallback_date):
    kind = group["kind"]
    ai = analyze_group_with_ollama(kind, group["text"])
    title = safe_ai_field(ai, "titulo")
    doctor = safe_ai_field(ai, "medico")
    crm = safe_ai_field(ai, "crm")
    issue_date = safe_db_text((ai.get("data_documento") if isinstance(ai, dict) else "") or fallback_date or today_date())
    # O status histórico é inferido automaticamente por data/validade; o parâmetro manual fica apenas como override interno/API.
    auto_historical = bool(historical_only)
    page_range = "%s-%s" % (min(group["pages"]), max(group["pages"]))

    split_file = write_pdf_pages(UPLOAD_DIR / source_file, group["pages"]) if source_file.lower().endswith(".pdf") else source_file

    if kind == "prescription":
        validity = safe_db_text((ai.get("validade_receita") if isinstance(ai, dict) else "") or "")
        validity_date_obj = parse_date(validity)
        if validity_date_obj and validity_date_obj < datetime.now().date():
            auto_historical = True
        status = "historical" if auto_historical else "active"
        cur = conn.execute(
            """
            INSERT INTO prescriptions(profile_id, source_document_id, title, doctor_name, crm, issue_date, validity_date, file_name, page_range, status, historical_only, ai_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile["id"], source_id, safe_db_text(title or f"Receita médica - {issue_date}"), safe_db_text(doctor or ""), safe_db_text(crm or ""),
                issue_date, validity, split_file, page_range, status, int(auto_historical),
                json.dumps(ai, ensure_ascii=False, indent=2), now_iso()
            ),
        )
        prescription_id = cur.lastrowid

        meds = ai.get("medicamentos") if isinstance(ai, dict) else []
        if isinstance(meds, list):
            for m in meds:
                if not isinstance(m, dict):
                    continue
                name = (m.get("nome") or m.get("name") or "").strip()
                if not name:
                    continue
                duration = str(m.get("duracao") or "")
                continuous = bool(m.get("uso_continuo") or ("cont" in duration.lower()))
                end_date = estimate_end_date(issue_date, duration, continuous)
                item_historical = is_historical(auto_historical, issue_date, end_date)
                frequency = str(m.get("frequencia") or "")
                dosage = str(m.get("dosagem") or "")

                conn.execute(
                    """
                    INSERT INTO prescription_items(prescription_id, profile_id, medication_name, substance, dosage, frequency, duration, quantity_text, route, continuous_use, estimated_start_date, estimated_end_date, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prescription_id, profile["id"], name, str(m.get("substancia") or ""), dosage, frequency,
                        duration, str(m.get("quantidade") or ""), str(m.get("via") or ""), int(continuous),
                        issue_date, end_date, "historical" if item_historical else "active", now_iso()
                    ),
                )
                create_or_update_treatment(conn, profile["id"], name, dosage, frequency, prescription_id, issue_date, item_historical, str(m.get("quantidade") or ""))

        index_fts(conn, "prescription", prescription_id, profile["name"], title or "Receita", group["text"])
        return {"type": "prescription", "id": prescription_id}

    if kind == "exam_order":
        issue_date_obj = parse_date(issue_date)
        if issue_date_obj and (datetime.now().date() - issue_date_obj).days > 120:
            auto_historical = True
        status = "historical" if auto_historical else "pending"
        cur = conn.execute(
            """
            INSERT INTO exam_orders(profile_id, source_document_id, title, doctor_name, crm, issue_date, file_name, page_range, status, historical_only, ai_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile["id"], source_id, safe_db_text(title or f"Pedido de exames - {issue_date}"), safe_db_text(doctor or ""), safe_db_text(crm or ""),
                issue_date, split_file, page_range, status, int(auto_historical),
                json.dumps(ai, ensure_ascii=False, indent=2), now_iso()
            ),
        )
        order_id = cur.lastrowid
        exams = ai.get("exames_solicitados") if isinstance(ai, dict) else []
        if isinstance(exams, list):
            for e in exams:
                if isinstance(e, dict):
                    name = str(e.get("nome") or e.get("exame") or "").strip()
                    tuss = str(e.get("tuss") or "")
                else:
                    name = str(e).strip()
                    tuss = ""
                if not name:
                    continue
                conn.execute(
                    """
                    INSERT INTO exam_order_items(exam_order_id, profile_id, exam_name, normalized_name, tuss, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (order_id, profile["id"], name, normalize_exam_name(name), tuss, status, now_iso()),
                )
        index_fts(conn, "exam_order", order_id, profile["name"], title or "Pedido de exame", group["text"])
        return {"type": "exam_order", "id": order_id}

    if kind == "exam_result":
        cur = conn.execute(
            """
            INSERT INTO exam_results(profile_id, source_document_id, lab_name, result_date, ai_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile["id"], source_id,
                str(ai.get("laboratorio") or "") if isinstance(ai, dict) else "",
                str(ai.get("data_resultado") or issue_date) if isinstance(ai, dict) else issue_date,
                json.dumps(ai, ensure_ascii=False, indent=2), now_iso()
            ),
        )
        result_id = cur.lastrowid
        results = ai.get("resultados_exames") if isinstance(ai, dict) else []
        if isinstance(results, list):
            for r in results:
                if not isinstance(r, dict):
                    continue
                name = str(r.get("nome") or r.get("exame") or "").strip()
                if not name:
                    continue
                value_text = str(r.get("valor") or "")
                try:
                    value = float(re.sub(r"[^0-9,.\-]", "", value_text).replace(",", "."))
                except Exception:
                    value = None
                conn.execute(
                    """
                    INSERT INTO exam_markers(exam_result_id, profile_id, marker_name, normalized_name, value, value_text, unit, reference_range, result_date, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (result_id, profile["id"], name, normalize_exam_name(name), value, value_text, str(r.get("unidade") or ""), str(r.get("referencia") or ""), issue_date, now_iso()),
                )
        index_fts(conn, "exam_result", result_id, profile["name"], title or "Resultado de exame", group["text"])
        return {"type": "exam_result", "id": result_id}

    cur = conn.execute(
        """
        INSERT INTO inbox_items(profile_id, source_document_id, type, title, payload_json, status, historical_only, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (profile["id"], source_id, kind, title or "Documento médico", json.dumps({"ai": ai, "text": group["text"][:4000]}, ensure_ascii=False), "needs_review", int(historical_only), now_iso()),
    )
    return {"type": "inbox", "id": cur.lastrowid}




def parse_ics_datetime(value):
    value = (value or "").strip()
    if not value:
        return ""

    # Google Calendar pode enviar DTSTART em UTC com Z.
    # O MedVault trabalha em horário local do Brasil; converter evita dia/hora deslocados na UI.
    is_utc = value.endswith("Z")
    raw = value[:-1] if is_utc else value

    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            dt = datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt)
            if is_utc:
                dt = dt - timedelta(hours=3)
            return dt.isoformat(timespec="minutes")
        except Exception:
            pass
    return value


def unfold_ics_lines(text):
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = []
    for line in raw:
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines



def build_calendar_health_context(conn):
    try:
        treatments = conn.execute(
            "SELECT name, kind, frequency_text FROM treatments WHERE active=1 ORDER BY id DESC LIMIT 50"
        ).fetchall()
    except Exception:
        treatments = []
    try:
        inventory = conn.execute(
            "SELECT medication_name, default_frequency, notes FROM medication_inventory WHERE active=1 ORDER BY id DESC LIMIT 50"
        ).fetchall()
    except Exception:
        inventory = []

    lines = []
    for t in treatments:
        lines.append(f"Tratamento: {t['name']} | tipo: {t['kind']} | frequência: {t['frequency_text']}")
    for item in inventory:
        lines.append(f"Estoque/medicação: {item['medication_name']} | frequência: {item['default_frequency']} | observações: {item['notes']}")
    return "\n".join(lines[:80])


def fallback_calendar_classification(event, health_context=""):
    payload_raw = (event.get("title", "") + " " + event.get("description", "") + " " + event.get("location", "")).lower()
    payload = calendar_keyword_text(payload_raw)
    context = calendar_keyword_text(health_context or "")
    matched = ""

    for kw in CALENDAR_SYNC_KEYWORDS:
        kw_raw = (kw or "").lower()
        kw_norm = calendar_keyword_text(kw_raw)
        if kw_raw and (kw_raw in payload_raw or kw_norm in payload):
            matched = kw_raw
            break

    # Match inteligente por nomes cadastrados, mesmo sem palavras fixas.
    known_tokens = []
    for source in [context]:
        for token in re.findall(r"[a-z0-9]{4,}", source):
            if token not in {"tratamento", "tipo", "frequencia", "frequência", "estoque", "medicacao", "medicação", "observacoes", "observações"}:
                known_tokens.append(token)
    known_tokens = list(dict.fromkeys(known_tokens))[:80]
    for token in known_tokens:
        if token and token in payload:
            return {
                "is_health_event": True,
                "type": "known_treatment",
                "matched_keyword": token,
                "confidence": 0.82,
                "reason": "Evento contém nome de tratamento/medicação cadastrado."
            }

    if matched:
        event_type = "health"
        if any(x in payload for x in ["aplica", "aplicacao", "aplicação", "injet", "injecao", "injeção", "ampola", "caneta"]):
            event_type = "medication_application"
        elif any(x in payload for x in ["mounjaro", "deposteron", "centrum", "vitamina", "remedio", "remédio", "tomar"]):
            event_type = "medication_reminder"
        elif any(x in payload for x in ["consulta", "consultorio", "consultório", "medico", "médico", "telemedicina"]):
            event_type = "consultation"
        elif any(x in payload for x in ["exame", "laboratorio", "laboratório"]):
            event_type = "exam"
        return {
            "is_health_event": True,
            "type": event_type,
            "matched_keyword": matched,
            "confidence": 0.82,
            "reason": "Evento reconhecido por contexto local de saúde."
        }

    return {
        "is_health_event": False,
        "type": "other",
        "matched_keyword": "",
        "confidence": 0.0,
        "reason": "Sem evidência local."
    }


def classify_calendar_event_with_ai(event, health_context=""):
    fallback = fallback_calendar_classification(event, health_context)
    # Se a heurística já tem confiança boa, não gasta IA.
    if fallback.get("is_health_event") and float(fallback.get("confidence") or 0) >= 0.80:
        return fallback

    if not current_ollama_url():
        return fallback

    prompt = f"""
Você classifica eventos de calendário para um sistema pessoal de saúde chamado MedVault.

Objetivo:
Detectar se o evento é relevante para saúde/tratamento, mesmo que NÃO contenha palavras fixas como consulta, exame ou farmácia.

Contexto de tratamentos/medicamentos cadastrados:
{health_context or "Sem contexto cadastrado."}

Evento:
Título: {event.get("title", "")}
Início: {event.get("starts_at", "")}
Fim: {event.get("ends_at", "")}
Local: {event.get("location", "")}
Descrição: {event.get("description", "")[:1200]}

Responda APENAS JSON válido:
{{
  "is_health_event": true/false,
  "type": "consultation|exam|medication_application|medication_reminder|purchase|other",
  "medication_name": "",
  "matched_keyword": "",
  "confidence": 0.0,
  "reason": ""
}}

Regras:
- Marque como medication_application se for aplicação de injetável, injeção, medicação em farmácia, ou aplicação vinculada a tratamento.
- Marque como medication_reminder se for uso em casa, por exemplo Mounjaro, vitamina, Centrum, remédio diário.
- Marque como health_event se combinar com qualquer tratamento/medicação do contexto.
- Não marque futebol, trabalho, reuniões comuns, lazer ou lembretes financeiros como saúde.
"""
    try:
        r = requests.post(
            f"{current_ollama_url()}/api/generate",
            json={"model": current_ollama_model(), "prompt": prompt, "format": "json", "stream": False},
            timeout=45,
        )
        r.raise_for_status()
        raw = r.json().get("response", "{}")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return fallback

        confidence = float(data.get("confidence") or 0)
        is_health = bool(data.get("is_health_event")) and confidence >= 0.55
        return {
            "is_health_event": is_health,
            "type": safe_db_text(data.get("type") or fallback.get("type") or "other"),
            "medication_name": safe_db_text(data.get("medication_name") or ""),
            "matched_keyword": safe_db_text(data.get("matched_keyword") or data.get("medication_name") or fallback.get("matched_keyword") or "ai"),
            "confidence": confidence,
            "reason": safe_db_text(data.get("reason") or "")
        }
    except Exception:
        # Não registrar warning em massa. A classificação local já cobre o uso normal.
        return fallback


def parse_ics_events(ics_text, health_context=""):
    lines = unfold_ics_lines(ics_text or "")
    events = []
    current = None

    def clean(v):
        return (v or "").replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").strip()

    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT" and current is not None:
            if current.get("title") and current.get("starts_at"):
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue

        key, value = line.split(":", 1)
        key_name = key.split(";", 1)[0].upper()

        if key_name == "UID":
            current["id"] = clean(value)
        elif key_name == "SUMMARY":
            current["title"] = clean(value)
        elif key_name == "DTSTART":
            current["starts_at"] = parse_ics_datetime(value)
        elif key_name == "DTEND":
            current["ends_at"] = parse_ics_datetime(value)
        elif key_name == "LOCATION":
            current["location"] = clean(value)
        elif key_name == "DESCRIPTION":
            current["description"] = clean(value)

    filtered = []
    now_dt = datetime.now() - timedelta(days=2)
    max_dt = datetime.now() + timedelta(days=240)
    ai_used = 0

    for e in events:
        ev_dt = parse_datetime_value(e.get("starts_at", ""))
        if ev_dt and (ev_dt < now_dt or ev_dt > max_dt):
            continue

        # V6.6.4: sincronização de calendário não chama Ollama.
        # Classificação por IA em lote causou warnings repetidos e risco de 502.
        # A inteligência prática aqui usa contexto local: tratamentos, estoque e termos de saúde.
        classification = fallback_calendar_classification(e, health_context)

        if not classification.get("is_health_event"):
            continue

        if not e.get("id"):
            e["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, e.get("title", "") + e.get("starts_at", "")))

        e["matched_keyword"] = safe_db_text(classification.get("matched_keyword") or classification.get("type") or "local")
        e["classification_type"] = safe_db_text(classification.get("type") or "health")
        e["classification_confidence"] = float(classification.get("confidence") or 0)
        e["classification_reason"] = safe_db_text(classification.get("reason") or "")
        e["ai_used"] = bool(ai_used)
        filtered.append(e)

    return filtered


def cleanup_unlinked_prescription_treatment_events(conn):
    """Remove pendências antigas criadas por versões anteriores sem base no calendário."""
    try:
        conn.execute(
            """
            DELETE FROM treatment_events
            WHERE status='pending'
              AND IFNULL(prescription_id,0) > 0
              AND IFNULL(linked_calendar_event_id,'') = ''
              AND IFNULL(scheduled_at,'') = ''
            """
        )
    except Exception:
        pass


def reconcile_treatments_with_calendar(conn):
    """Depois de sincronizar o ICS, vincula tratamentos ativos aos eventos reais do calendário."""
    cleanup_unlinked_prescription_treatment_events(conn)

    treatments = conn.execute(
        """
        SELECT t.*, p.name profile_name
        FROM treatments t
        JOIN profiles p ON p.id=t.profile_id
        WHERE t.active=1
          AND IFNULL(t.current_prescription_id,0) > 0
          AND IFNULL(t.requires_prescription,0)=1
        ORDER BY t.id DESC
        LIMIT 80
        """
    ).fetchall()

    linked = 0
    for treatment in treatments:
        existing = conn.execute(
            """
            SELECT id FROM treatment_events
            WHERE treatment_id=?
              AND status='pending'
              AND IFNULL(linked_calendar_event_id,'') <> ''
            LIMIT 1
            """,
            (treatment["id"],),
        ).fetchone()
        if existing:
            continue

        rx = conn.execute(
            "SELECT id, issue_date, file_name FROM prescriptions WHERE id=?",
            (int(treatment["current_prescription_id"] or 0),),
        ).fetchone()
        if not rx:
            continue

        event_id = upsert_pending_treatment_event(
            conn,
            treatment["id"],
            rx["id"],
            rx["issue_date"] or now_iso()[:10],
            rx["file_name"] or "",
        )
        if event_id:
            linked += 1

    return linked


def sync_calendar_ics():
    with db() as _cleanup_conn:
        cleanup_calendar_ai_warning_spam(_cleanup_conn)
    if not current_calendar_ics_url():
        return {"enabled": False, "synced": 0}
    try:
        r = requests.get(current_calendar_ics_url(), timeout=20)
        r.raise_for_status()
        now_value = now_iso()
        with db() as conn:
            health_context = build_calendar_health_context(conn)
            events = parse_ics_events(r.text, health_context)

            for e in events:
                conn.execute(
                    """
                    INSERT INTO calendar_events(
                        id, title, starts_at, ends_at, location, description, source, matched_keyword,
                        classification_type, classification_confidence, classification_reason,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'ics', ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      title=excluded.title,
                      starts_at=excluded.starts_at,
                      ends_at=excluded.ends_at,
                      location=excluded.location,
                      description=excluded.description,
                      matched_keyword=excluded.matched_keyword,
                      classification_type=excluded.classification_type,
                      classification_confidence=excluded.classification_confidence,
                      classification_reason=excluded.classification_reason,
                      updated_at=excluded.updated_at
                    """,
                    (
                        e.get("id"),
                        e.get("title", ""),
                        e.get("starts_at", ""),
                        e.get("ends_at", ""),
                        e.get("location", ""),
                        e.get("description", ""),
                        e.get("matched_keyword", ""),
                        e.get("classification_type", ""),
                        float(e.get("classification_confidence") or 0),
                        e.get("classification_reason", ""),
                        now_value,
                        now_value,
                    ),
                )

            linked_treatment_events = reconcile_treatments_with_calendar(conn)

        return {
            "enabled": True,
            "synced": len(events),
            "linked_treatment_events": linked_treatment_events,
            "ai_enabled": False,
            "classifier": "local_context",
            "message": "Sincronização concluída sem chamada em lote ao Ollama."
        }
    except Exception as e:
        log_event("warning", "calendar", "Falha ao sincronizar calendário ICS.", str(e))
        return {"enabled": True, "synced": 0, "error": str(e)}




def normalize_med_name_for_match(name):
    return re.sub(r"[^a-z0-9]+", "", text_norm(name))


def find_inventory_for_treatment(conn, treatment):
    profile_id = treatment["profile_id"]
    treatment_id = treatment["treatment_id"] if "treatment_id" in treatment.keys() else treatment["id"]
    name_value = treatment["treatment_name"] if "treatment_name" in treatment.keys() else treatment["name"]
    target = normalize_med_name_for_match(name_value)

    exact = conn.execute(
        """
        SELECT * FROM medication_inventory
        WHERE profile_id=? AND active=1 AND treatment_id=?
        ORDER BY id DESC LIMIT 1
        """,
        (profile_id, treatment_id),
    ).fetchone()
    if exact:
        return exact

    candidates = conn.execute(
        "SELECT * FROM medication_inventory WHERE profile_id=? AND active=1 ORDER BY id DESC",
        (profile_id,),
    ).fetchall()
    for item in candidates:
        candidate = normalize_med_name_for_match(item["medication_name"])
        if candidate and (candidate in target or target in candidate):
            return item
    return None


def consume_inventory_for_treatment(conn, event_row, quantity=1):
    inventory = find_inventory_for_treatment(conn, event_row)
    if not inventory:
        return None

    current = float(inventory["units_on_hand"] or 0)
    consume_qty = float(inventory["dose_quantity"] or quantity or 1)
    new_total = max(current - consume_qty, 0)
    conn.execute(
        "UPDATE medication_inventory SET units_on_hand=?, updated_at=? WHERE id=?",
        (new_total, now_iso(), inventory["id"]),
    )
    conn.execute(
        """
        INSERT INTO inventory_movements(inventory_id, profile_id, movement_type, quantity, units_after, related_type, related_id, notes, created_at)
        VALUES (?, ?, 'consume', ?, ?, 'treatment_event', ?, ?, ?)
        """,
        (inventory["id"], inventory["profile_id"], consume_qty, new_total, event_row["id"], "Consumo automático por evento de tratamento", now_iso()),
    )
    return {"inventory_id": inventory["id"], "units_on_hand": new_total, "low_stock_threshold": inventory["low_stock_threshold"]}


def process_inventory_low_stock():
    with db() as conn:
        rs = conn.execute(
            """
            SELECT i.*, p.name profile_name
            FROM medication_inventory i
            JOIN profiles p ON p.id=i.profile_id
            WHERE i.active=1
              AND i.units_on_hand <= i.low_stock_threshold
              AND (IFNULL(i.last_low_stock_notified_at,'')='' OR IFNULL(i.updated_at,'') > IFNULL(i.last_low_stock_notified_at,''))
            ORDER BY i.units_on_hand ASC
            LIMIT 20
            """
        ).fetchall()

        for item in rs:
            payload = {
                "source": "medvault",
                "type": "inventory_low_stock",
                "inventory_id": item["id"],
                "profile": item["profile_name"],
                "medication": item["medication_name"],
                "units_on_hand": item["units_on_hand"],
                "unit_label": item["unit_label"],
                "low_stock_threshold": item["low_stock_threshold"],
                "message": f"Estoque baixo: {item['medication_name']} ({item['units_on_hand']} {item['unit_label']})",
                "medvault_url": current_self_base_url().replace(":8088", ":8090"),
            }
            if current_ha_webhook_url():
                try:
                    requests.post(current_ha_webhook_url(), json=payload, timeout=10)
                    conn.execute("UPDATE medication_inventory SET last_low_stock_notified_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), item["id"]))
                    log_event("info", "ha", "Aviso de estoque baixo enviado ao Home Assistant.", json.dumps(payload, ensure_ascii=False), "inventory", item["id"])
                except Exception as ex:
                    log_event("error", "ha", "Falha ao enviar aviso de estoque baixo ao HA.", str(ex), "inventory", item["id"])


def routine_daily_times(routine_preset, preferred_time=""):
    routine = safe_db_text(routine_preset or "").strip()
    preferred = safe_db_text(preferred_time or "").strip()

    def clean_time(value, fallback="08:00"):
        if re.match(r"^\d{2}:\d{2}$", value or ""):
            return value
        return fallback

    if routine == "q6h":
        start = clean_time(preferred, "06:00")
        step = 6
    elif routine == "q8h":
        start = clean_time(preferred, "06:00")
        step = 8
    elif routine == "q12h":
        start = clean_time(preferred, "08:00")
        step = 12
    elif routine == "daily":
        return [clean_time(preferred, "09:00")]
    else:
        return [clean_time(preferred, "09:00")] if preferred else []

    h, m = [int(x) for x in start.split(":")]
    values = []
    current = h * 60 + m
    while current < 24 * 60:
        values.append(f"{current // 60:02d}:{current % 60:02d}")
        current += step * 60
    return values[:6]


def treatment_schedule_candidates(treatment, days_ahead=21):
    now = datetime.now()
    routine = safe_db_text(treatment["routine_preset"] or "").strip() if "routine_preset" in treatment.keys() else ""
    preferred = safe_db_text(treatment["preferred_time"] or "").strip() if "preferred_time" in treatment.keys() else ""
    interval = int(treatment["interval_days"] or 0)
    daily_times = []
    if "daily_times" in treatment.keys() and safe_db_text(treatment["daily_times"] or "").strip():
        daily_times = [x.strip() for x in safe_db_text(treatment["daily_times"]).split(",") if re.match(r"^\d{2}:\d{2}$", x.strip())]
    if not daily_times:
        daily_times = routine_daily_times(routine, preferred)

    candidates = []
    if daily_times and routine in {"daily", "q6h", "q8h", "q12h"}:
        for d in range(days_ahead + 1):
            day = now.date() + timedelta(days=d)
            for t in daily_times:
                hh, mm = [int(x) for x in t.split(":")]
                dt = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
                if dt >= now - timedelta(minutes=2):
                    candidates.append(dt)
        return sorted(candidates)

    if interval > 0:
        base_time = daily_times[0] if daily_times else (preferred if re.match(r"^\d{2}:\d{2}$", preferred or "") else "09:00")
        hh, mm = [int(x) for x in base_time.split(":")]
        for d in range(days_ahead + 1):
            if d % interval == 0:
                dt = datetime.combine(now.date() + timedelta(days=d), datetime.min.time()).replace(hour=hh, minute=mm)
                if dt >= now - timedelta(minutes=2):
                    candidates.append(dt)
        return sorted(candidates)

    return []


def ensure_treatment_future_events(conn, treatment_id, min_pending=3):
    treatment = conn.execute(
        "SELECT t.*, p.name profile_name FROM treatments t JOIN profiles p ON p.id=t.profile_id WHERE t.id=?",
        (treatment_id,),
    ).fetchone()
    if not treatment or not int(treatment["active"] or 0):
        return 0

    if int(treatment["requires_prescription"] or 0):
        return 0

    pending_count = conn.execute(
        "SELECT COUNT(*) c FROM treatment_events WHERE treatment_id=? AND status='pending'",
        (treatment_id,),
    ).fetchone()["c"]
    if pending_count >= min_pending:
        return 0

    candidates = treatment_schedule_candidates(treatment)
    if not candidates:
        return 0

    action_label = "Aplicar" if treatment["kind"] == "injection" or is_injection_medication(treatment["name"]) else "Tomar"
    side = next_side_for_treatment(conn, treatment)
    created = 0

    for dt in candidates:
        if pending_count + created >= min_pending:
            break
        scheduled_at = dt.isoformat(timespec="minutes")
        exists = conn.execute(
            """
            SELECT id FROM treatment_events
            WHERE treatment_id=? AND status='pending'
              AND (scheduled_at=? OR (scheduled_for=? AND IFNULL(scheduled_at,'')=''))
            LIMIT 1
            """,
            (treatment_id, scheduled_at, dt.date().isoformat()),
        ).fetchone()
        if exists:
            continue

        conn.execute(
            """
            INSERT INTO treatment_events(
                treatment_id, profile_id, prescription_id, scheduled_for, status, action_label, created_at,
                scheduled_at, linked_calendar_event_id, administration_side, reminder_due_at, prescription_file_name
            )
            VALUES (?, ?, NULL, ?, 'pending', ?, ?, ?, '', ?, ?, '')
            """,
            (
                treatment_id,
                treatment["profile_id"],
                dt.date().isoformat(),
                action_label,
                now_iso(),
                scheduled_at,
                side,
                scheduled_at,
            ),
        )
        created += 1

    return created


def ensure_inventory_treatment(conn, item):
    interval = int(item["interval_days"] or 0)
    if interval <= 0:
        interval = infer_interval_days(item["default_frequency"] or "")

    routine = safe_db_text(item["routine_preset"] or "").strip() if "routine_preset" in item.keys() else ""
    preferred = safe_db_text(item["preferred_time"] or "").strip() if "preferred_time" in item.keys() else ""

    daily_times = ",".join(routine_daily_times(routine, preferred))
    if interval <= 0 and not daily_times:
        return 0

    existing = None
    if int(item["treatment_id"] or 0):
        existing = conn.execute("SELECT * FROM treatments WHERE id=?", (item["treatment_id"],)).fetchone()

    defaults = infer_treatment_rule_defaults(item["medication_name"])
    kind = "injection" if is_injection_medication(item["medication_name"]) else "medication"
    default_action = "applied" if is_injection_medication(item["medication_name"]) else "taken"

    if existing:
        treatment_id = existing["id"]
        conn.execute(
            """
            UPDATE treatments
            SET frequency_text=?, interval_days=?, requires_prescription=?, routine_preset=?, preferred_time=?,
                daily_times=?, inventory_id=?, active=1, updated_at=?
            WHERE id=?
            """,
            (
                item["default_frequency"], interval, int(item["requires_prescription"] or 0),
                routine, preferred, daily_times, item["id"], now_iso(), treatment_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO treatments(
                profile_id, name, kind, dosage, frequency_text, interval_days, requires_prescription,
                default_action, active, created_at, supply_total, supply_used, side_mode, preferred_start_side,
                side_anchor_treatment, routine_preset, preferred_time, daily_times, inventory_id
            )
            VALUES (?, ?, ?, '', ?, ?, ?, ?, 1, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["profile_id"],
                item["medication_name"],
                kind,
                item["default_frequency"],
                interval,
                int(item["requires_prescription"] or 0),
                default_action,
                now_iso(),
                defaults["side_mode"],
                defaults["preferred_start_side"],
                defaults["side_anchor_treatment"],
                routine,
                preferred,
                daily_times,
                item["id"],
            ),
        )
        treatment_id = cur.lastrowid
        conn.execute("UPDATE medication_inventory SET treatment_id=?, updated_at=? WHERE id=?", (treatment_id, now_iso(), item["id"]))

    ensure_treatment_future_events(conn, treatment_id, min_pending=4 if routine in {"q6h", "q8h", "q12h"} else 2)
    return treatment_id


def process_due_exam_result_reminders():
    now_date = datetime.now().date().isoformat()
    with db() as conn:
        rs = conn.execute(
            """
            SELECT o.*, p.name profile_name
            FROM exam_orders o
            JOIN profiles p ON p.id=o.profile_id
            WHERE o.status='performed'
              AND IFNULL(o.result_expected_at,'') <> ''
              AND o.result_expected_at <= ?
              AND IFNULL(o.result_reminded_at,'') = ''
            ORDER BY o.result_expected_at ASC
            LIMIT 20
            """,
            (now_date,),
        ).fetchall()

        for o in rs:
            payload = {
                "source": "medvault",
                "type": "exam_result_due",
                "exam_order_id": o["id"],
                "profile": o["profile_name"],
                "title": o["title"],
                "result_expected_at": o["result_expected_at"],
                "message": "Resultado de exame previsto. Cadastre o resultado no MedVault se já estiver disponível.",
                "callback_url": f"{current_self_base_url()}/api/exam-orders/{o['id']}/result-received",
            }

            if current_ha_webhook_url():
                try:
                    requests.post(current_ha_webhook_url(), json=payload, timeout=10)
                    conn.execute("UPDATE exam_orders SET result_reminded_at=? WHERE id=?", (now_iso(), o["id"]))
                    log_event("info", "ha", "Lembrete de resultado de exame enviado ao Home Assistant.", json.dumps(payload, ensure_ascii=False), "exam_order", o["id"])
                except Exception as ex:
                    log_event("error", "ha", "Falha ao enviar lembrete de resultado ao HA.", str(ex), "exam_order", o["id"])
            else:
                conn.execute("UPDATE exam_orders SET result_reminded_at=? WHERE id=?", (now_iso(), o["id"]))
                log_event("warning", "exam", "Resultado de exame previsto, mas HA não está configurado.", json.dumps(payload, ensure_ascii=False), "exam_order", o["id"])



def process_due_treatment_events():
    now = datetime.now()
    with db() as conn:
        rs = conn.execute(
            """
            SELECT e.*, t.name treatment_name, t.default_action, p.name profile_name
            FROM treatment_events e
            JOIN treatments t ON t.id=e.treatment_id
            JOIN profiles p ON p.id=e.profile_id
            WHERE e.status='pending'
              AND IFNULL(e.ha_notified_at,'')=''
              AND e.scheduled_for <= ?
            ORDER BY e.scheduled_for ASC
            LIMIT 20
            """,
            ((now + timedelta(days=1)).date().isoformat(),),
        ).fetchall()

        for e in rs:
            payload = {
                "source": "medvault",
                "type": "treatment_event",
                "event_id": e["id"],
                "profile": e["profile_name"],
                "treatment": e["treatment_name"],
                "scheduled_for": e["scheduled_for"],
                "scheduled_at": e["scheduled_at"],
                "administration_side": e["administration_side"],
                "prescription_file_name": e["prescription_file_name"],
                "prescription_url": f"{current_self_base_url()}/uploads/{e['prescription_file_name']}" if e["prescription_file_name"] else "",
                "actions": ["taken", "applied", "scheduled", "postponed", "skipped"],
                "callback_url": f"{current_self_base_url()}/api/treatment-events/{e['id']}/action",
            }
            if current_ha_webhook_url():
                try:
                    requests.post(current_ha_webhook_url(), json=payload, timeout=10)
                    conn.execute("UPDATE treatment_events SET ha_notified_at=? WHERE id=?", (now_iso(), e["id"]))
                    log_event("info", "ha", "Notificação enviada ao Home Assistant.", json.dumps(payload, ensure_ascii=False), "treatment_event", e["id"])
                except Exception as ex:
                    log_event("error", "ha", "Falha ao enviar webhook ao HA.", str(ex), "treatment_event", e["id"])



def process_post_treatment_followups():
    now_dt = datetime.now()
    with db() as conn:
        rs = conn.execute(
            """
            SELECT e.*, t.name treatment_name, p.name profile_name
            FROM treatment_events e
            JOIN treatments t ON t.id=e.treatment_id
            JOIN profiles p ON p.id=e.profile_id
            WHERE e.status='pending'
              AND IFNULL(e.scheduled_at,'')<>''
              AND IFNULL(e.reminder_sent_at,'')=''
              AND IFNULL(e.reminder_due_at,'')<>''
              AND e.reminder_due_at <= ?
            ORDER BY e.reminder_due_at ASC
            LIMIT 20
            """,
            (now_dt.isoformat(timespec='minutes'),),
        ).fetchall()

        for e in rs:
            payload = {
                "source": "medvault",
                "type": "treatment_followup",
                "event_id": e["id"],
                "profile": e["profile_name"],
                "treatment": e["treatment_name"],
                "scheduled_for": e["scheduled_for"],
                "scheduled_at": e["scheduled_at"],
                "administration_side": e["administration_side"],
                "question": "A aplicação foi realizada?",
                "actions": ["applied", "postponed", "missed"],
                "callback_url": f"{current_self_base_url()}/api/treatment-events/{e['id']}/action",
            }
            if current_ha_webhook_url():
                try:
                    requests.post(current_ha_webhook_url(), json=payload, timeout=10)
                    conn.execute("UPDATE treatment_events SET reminder_sent_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), e["id"]))
                    log_event("info", "ha", "Follow-up pós-aplicação enviado ao Home Assistant.", json.dumps(payload, ensure_ascii=False), "treatment_event", e["id"])
                except Exception as ex:
                    log_event("error", "ha", "Falha ao enviar follow-up ao HA.", str(ex), "treatment_event", e["id"])


def cleanup_expired_tokens_and_jobs():
    try:
        with db() as conn:
            conn.execute("DELETE FROM share_tokens WHERE expires_at < ?", (now_iso(),))
            # mantém jobs recentes por diagnóstico, mas remove jobs antigos para não crescer indefinidamente
            cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
            conn.execute("DELETE FROM ingest_jobs WHERE created_at < ?", (cutoff,))
    except Exception as e:
        log_event("warning", "cleanup", "Falha na limpeza automática.", str(e))


def process_inventory_routine_schedules():
    with db() as conn:
        rs = conn.execute(
            """
            SELECT id FROM treatments
            WHERE active=1 AND IFNULL(requires_prescription,0)=0
              AND (IFNULL(routine_preset,'')<>'' OR IFNULL(daily_times,'')<>'' OR IFNULL(interval_days,0)>0)
            LIMIT 100
            """
        ).fetchall()
        for r in rs:
            ensure_treatment_future_events(conn, r["id"], min_pending=4)


def scheduled_side_effects_job():
    ensure_core_schema()
    try:
        sync_calendar_ics()
    except Exception as e:
        log_event("warning", "calendar", "Falha no job agendado de calendário.", str(e))

    try:
        process_inventory_routine_schedules()
    except Exception as e:
        log_event("warning", "inventory", "Falha no job agendado de rotinas de estoque.", str(e))

    try:
        process_due_treatment_events()
    except Exception as e:
        log_event("warning", "treatment", "Falha no job agendado de lembretes.", str(e))

    try:
        process_due_exam_result_reminders()
    except Exception as e:
        log_event("warning", "exam", "Falha no job agendado de exames.", str(e))

    cleanup_expired_tokens_and_jobs()


@app.on_event("startup")
def startup():
    global scheduler
    init_db()
    scheduled_side_effects_job()
    if scheduler is None:
        scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
        scheduler.add_job(scheduled_side_effects_job, "interval", minutes=5, id="medvault_side_effects", replace_existing=True, max_instances=1, coalesce=True)
        scheduler.start()


@app.on_event("shutdown")
def shutdown():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None





@app.get("/api/self-test")
def self_test():
    expected = [
        "/api/status",
        "/api/bootstrap",
        "/api/settings",
        "/api/calendar/sync",
        "/api/ingest/upload-job",
        "/api/system/reset",
    ]
    route_paths = {getattr(route, "path", "") for route in app.routes}
    return {
        "ok": all(path in route_paths for path in expected),
        "version": APP_VERSION,
        "missing": [path for path in expected if path not in route_paths],
        "routes_count": len(route_paths),
    }


@app.get("/api/debug/routes")
def debug_routes():
    data = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not (path.startswith("/api") or path == "/health"):
            continue
        data.append({
            "path": path,
            "name": getattr(route, "name", ""),
            "methods": sorted(list(getattr(route, "methods", []) or [])),
        })
    return {
        "version": APP_VERSION,
        "routes": sorted(data, key=lambda x: x["path"]),
    }


@app.post("/api/system/reset")
def reset_system(payload: dict):
    confirm = safe_db_text(payload.get("confirm", "")).strip()
    if confirm != "RESETAR":
        raise HTTPException(400, "Confirmação inválida. Digite RESETAR para apagar a base.")

    global scheduler

    scheduler_was_running = scheduler is not None
    if scheduler:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        scheduler = None

    try:
        for folder in [UPLOAD_DIR, EXPORT_DIR]:
            folder.mkdir(parents=True, exist_ok=True)
            for item in list(folder.iterdir()):
                try:
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except FileNotFoundError:
                    pass

        try:
            if DB_PATH.exists():
                with sqlite3.connect(DB_PATH) as raw:
                    raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    raw.execute("PRAGMA journal_mode=DELETE")
                    raw.commit()
        except Exception:
            pass

        for db_file in [DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")]:
            try:
                if db_file.exists():
                    db_file.unlink()
            except FileNotFoundError:
                pass
            except Exception as e:
                raise HTTPException(500, f"Falha ao apagar {db_file.name}: {e}")

        init_db()

        with db() as conn:
            counters = {
                "profiles": conn.execute("SELECT COUNT(*) c FROM profiles").fetchone()["c"],
                "source_documents": conn.execute("SELECT COUNT(*) c FROM source_documents").fetchone()["c"],
                "prescriptions": conn.execute("SELECT COUNT(*) c FROM prescriptions").fetchone()["c"],
                "treatments": conn.execute("SELECT COUNT(*) c FROM treatments").fetchone()["c"],
                "treatment_events": conn.execute("SELECT COUNT(*) c FROM treatment_events").fetchone()["c"],
                "exam_orders": conn.execute("SELECT COUNT(*) c FROM exam_orders").fetchone()["c"],
                "calendar_events": conn.execute("SELECT COUNT(*) c FROM calendar_events").fetchone()["c"],
                "inventory": conn.execute("SELECT COUNT(*) c FROM medication_inventory").fetchone()["c"],
            }
            log_event("warning", "system", "Base de dados resetada completamente pela interface.", json.dumps(counters, ensure_ascii=False))

        if scheduler_was_running:
            scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
            scheduler.add_job(scheduled_side_effects_job, "interval", minutes=5, id="medvault_side_effects", replace_existing=True, max_instances=1, coalesce=True)
            scheduler.start()

        return {"ok": True, "message": "Base resetada completamente.", "counters": counters}

    except HTTPException:
        raise
    except Exception as e:
        try:
            init_db()
        except Exception:
            pass

        if scheduler_was_running and scheduler is None:
            try:
                scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
                scheduler.add_job(scheduled_side_effects_job, "interval", minutes=5, id="medvault_side_effects", replace_existing=True, max_instances=1, coalesce=True)
                scheduler.start()
            except Exception:
                pass

        raise HTTPException(500, f"Falha ao resetar base: {e}")


@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


def cleanup_calendar_ai_warning_spam(conn):
    try:
        conn.execute(
            "DELETE FROM app_logs WHERE area='calendar' AND message='Falha na classificação IA de evento do calendário.'"
        )
    except Exception:
        pass


@app.get("/api/status")
def status():
    ensure_core_schema()
    with db() as _cleanup_conn:
        cleanup_calendar_ai_warning_spam(_cleanup_conn)
        cleanup_unlinked_prescription_treatment_events(_cleanup_conn)
    calendar_sync = {"enabled": bool(current_calendar_ics_url()), "synced": 0, "mode": "scheduled"}
    with db() as conn:
        next_event = row(conn.execute(
            """
            SELECT e.*, t.name treatment_name, p.name profile_name
            FROM treatment_events e
            JOIN treatments t ON t.id=e.treatment_id
            JOIN profiles p ON p.id=e.profile_id
            WHERE e.status='pending'
            ORDER BY e.scheduled_for ASC
            LIMIT 1
            """
        ).fetchone())
        return {
            "status": "ok",
            "version": APP_VERSION,
            "next_event": next_event,
            "pending_treatment_events": conn.execute("SELECT COUNT(*) c FROM treatment_events WHERE status='pending'").fetchone()["c"],
            "active_treatments": conn.execute("SELECT COUNT(*) c FROM treatments WHERE active=1").fetchone()["c"],
            "active_prescriptions": conn.execute("SELECT COUNT(*) c FROM prescriptions WHERE status='active'").fetchone()["c"],
            "pending_exam_orders": conn.execute("SELECT COUNT(*) c FROM exam_orders WHERE status IN ('pending','scheduled')").fetchone()["c"],
            "performed_exam_orders_waiting_result": conn.execute("SELECT COUNT(*) c FROM exam_orders WHERE status='performed'").fetchone()["c"],
            "exam_results_due": conn.execute("SELECT COUNT(*) c FROM exam_orders WHERE status='performed' AND IFNULL(result_expected_at,'') <> '' AND result_expected_at <= ?", (datetime.now().date().isoformat(),)).fetchone()["c"],
            "inbox_needs_review": conn.execute("SELECT COUNT(*) c FROM inbox_items WHERE status='needs_review'").fetchone()["c"],
            "warnings": conn.execute("SELECT COUNT(*) c FROM app_logs WHERE level IN ('error','warning')").fetchone()["c"],
            "ha_enabled": bool(current_ha_webhook_url()),
            "calendar_enabled": bool(current_calendar_ics_url()),
            "calendar_sync": calendar_sync,
            "upcoming_consultations": conn.execute("SELECT COUNT(*) c FROM calendar_events WHERE starts_at >= ?", (datetime.now().isoformat(timespec="minutes"),)).fetchone()["c"],
            "low_stock_items": conn.execute("SELECT COUNT(*) c FROM medication_inventory WHERE active=1 AND units_on_hand <= low_stock_threshold").fetchone()["c"],
            "ollama_model": current_ollama_model(),
        }


@app.get("/api/bootstrap")
def bootstrap():
    ensure_core_schema()
    with db() as conn:
        return {
            "profiles": rows(conn.execute("SELECT * FROM profiles ORDER BY name").fetchall()),
            "today": rows(conn.execute("""
                SELECT e.*, t.name treatment_name, t.kind treatment_kind, p.name profile_name,
                       t.supply_total, t.supply_used, t.rule_notes, r.status prescription_status
                FROM treatment_events e
                JOIN treatments t ON t.id=e.treatment_id
                JOIN profiles p ON p.id=e.profile_id
                LEFT JOIN prescriptions r ON r.id=e.prescription_id
                WHERE e.status='pending'
                ORDER BY CASE WHEN IFNULL(e.scheduled_at,'')<>'' THEN e.scheduled_at ELSE e.scheduled_for END ASC
                LIMIT 20
            """).fetchall()),
            "treatments": rows(conn.execute("""
                SELECT t.*, p.name profile_name, r.file_name prescription_file_name, r.status prescription_status, r.issue_date prescription_issue_date
                FROM treatments t
                JOIN profiles p ON p.id=t.profile_id
                LEFT JOIN prescriptions r ON r.id=t.current_prescription_id
                ORDER BY t.active DESC, t.name
            """).fetchall()),
            "prescriptions": rows(conn.execute("""
                SELECT r.*, p.name profile_name
                FROM prescriptions r JOIN profiles p ON p.id=r.profile_id
                ORDER BY r.issue_date DESC, r.id DESC
                LIMIT 200
            """).fetchall()),
            "prescription_items": rows(conn.execute("""
                SELECT i.*, r.title prescription_title
                FROM prescription_items i JOIN prescriptions r ON r.id=i.prescription_id
                ORDER BY i.status, i.medication_name
            """).fetchall()),
            "exam_orders": rows(conn.execute("""
                SELECT o.*, p.name profile_name
                FROM exam_orders o JOIN profiles p ON p.id=o.profile_id
                ORDER BY o.status, o.issue_date DESC
            """).fetchall()),
            "exam_order_items": rows(conn.execute("""
                SELECT i.*, o.title order_title
                FROM exam_order_items i JOIN exam_orders o ON o.id=i.exam_order_id
                ORDER BY i.status, i.normalized_name
            """).fetchall()),
            "exam_markers": rows(conn.execute("""
                SELECT m.*, p.name profile_name
                FROM exam_markers m JOIN profiles p ON p.id=m.profile_id
                ORDER BY m.normalized_name, m.result_date
            """).fetchall()),
            "inbox": rows(conn.execute("""
                SELECT i.*,
                       p.name profile_name,
                       s.title source_title,
                       s.file_name source_file_name,
                       s.original_name source_original_name,
                       s.source_type,
                       s.document_date,
                       s.created_at source_created_at
                FROM inbox_items i
                LEFT JOIN profiles p ON p.id=i.profile_id
                LEFT JOIN source_documents s ON s.id=i.source_document_id
                ORDER BY i.id DESC
                LIMIT 100
            """).fetchall()),
            "calendar_events": rows(conn.execute("""
                SELECT * FROM calendar_events
                WHERE starts_at >= ?
                ORDER BY starts_at ASC
                LIMIT 20
            """, (datetime.now().isoformat(timespec="minutes"),)).fetchall()),
            "inventory": rows(conn.execute("""
                SELECT i.*, p.name profile_name, t.name treatment_name
                FROM medication_inventory i
                JOIN profiles p ON p.id=i.profile_id
                LEFT JOIN treatments t ON t.id=i.treatment_id
                ORDER BY i.active DESC, i.units_on_hand ASC, i.medication_name
            """).fetchall()),
            "inventory_purchases": rows(conn.execute("""
                SELECT p.*, i.medication_name
                FROM inventory_purchases p
                JOIN medication_inventory i ON i.id=p.inventory_id
                ORDER BY p.purchase_date DESC, p.id DESC
                LIMIT 200
            """).fetchall()),
            "logs": rows(conn.execute("SELECT * FROM app_logs ORDER BY id DESC LIMIT 80").fetchall()),
        }


@app.post("/api/profiles")
def create_profile(payload: dict):
    name = safe_db_text(payload.get("name") or "").strip()
    phone_suffix = safe_db_text(payload.get("phone_suffix") or "").strip()
    notes = safe_db_text(payload.get("notes") or "").strip()

    if not name:
        raise HTTPException(400, "Nome obrigatório.")

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO profiles(name, phone_suffix, notes, created_at) VALUES (?, ?, ?, ?)",
            (name, phone_suffix, notes, now_iso()),
        )
        return row(conn.execute("SELECT * FROM profiles WHERE id=?", (cur.lastrowid,)).fetchone())


@app.put("/api/profiles/{profile_id}")
def update_profile(profile_id: int, payload: dict):
    name = safe_db_text(payload.get("name") or "").strip()
    phone_suffix = safe_db_text(payload.get("phone_suffix") or "").strip()
    notes = safe_db_text(payload.get("notes") or "").strip()

    if not name:
        raise HTTPException(400, "Nome obrigatório.")

    with db() as conn:
        require_profile(conn, profile_id)
        conn.execute(
            "UPDATE profiles SET name=?, phone_suffix=?, notes=?, updated_at=? WHERE id=?",
            (name, phone_suffix, notes, now_iso(), profile_id),
        )
        return row(conn.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone())


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int):
    with db() as conn:
        links = 0
        for table in ["source_documents", "prescriptions", "exam_orders", "treatments"]:
            links += conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE profile_id=?", (profile_id,)).fetchone()["c"]
        if links:
            raise HTTPException(400, "Perfil possui histórico vinculado e não pode ser excluído.")
        conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
    return {"ok": True}



def normalize_person_name(value):
    value = (value or "").lower()
    value = re.sub(r"[^a-záàâãéêíóôõúçñ ]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def detect_patient_name_from_text(text):
    raw = text or ""

    # Memed às vezes coloca o paciente em linha solta, sem "Nome:".
    # Exemplo real: "PAULO ROBERTO TOHY MONTEIRO" seguido de CPF.
    patterns = [
        r"IDENTIFICAÇÃO DO PACIENTE\s+Nome:\s*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇÑ ]{5,})",
        r"Paciente:\s*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇÑ ]{5,})",
        r"Nome:\s*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇÑ ]{5,})\s+CPF",
        r"ASSINATURA\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÇÑ ]{5,})\s+CPF",
        r"\n\s*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇÑ]{2,}(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇÑ]{2,}){2,})\s*\n\s*CPF:",
        r"\n\s*([A-ZÁÀÂÃÉÊÍÓÔÕÚÇÑ]{2,}(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇÑ]{2,}){2,})\s+CPF:",
    ]
    for pat in patterns:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip()
            name = re.split(r"\s+CPF:|\s+Sexo:|\s+Data|\s+Endereço:", name, flags=re.IGNORECASE)[0].strip()
            # evita capturar emitente/médico como paciente
            if not re.search(r"RAFAEL|BEATRIZ|CRM|MÉDICO|MEDICO|EMITENTE", name, re.IGNORECASE):
                return name
    return ""


def match_profile_by_patient_name(conn, patient_name):
    target = normalize_person_name(patient_name)
    if not target:
        return None

    profiles = conn.execute("SELECT * FROM profiles ORDER BY length(name) DESC").fetchall()

    # Match direto: perfil completo contido no texto extraído ou vice-versa.
    for p in profiles:
        n = normalize_person_name(p["name"])
        if n and (n in target or target in n):
            return p

    # Fallback por primeiro nome + último nome.
    target_parts = target.split()
    for p in profiles:
        n_parts = normalize_person_name(p["name"]).split()
        if len(target_parts) >= 2 and len(n_parts) >= 2 and target_parts[0] == n_parts[0] and target_parts[-1] == n_parts[-1]:
            return p

    # Fallback por todos os tokens significativos do nome do perfil presentes no texto.
    for p in profiles:
        n_parts = [x for x in normalize_person_name(p["name"]).split() if len(x) > 2]
        if len(n_parts) >= 2 and all(part in target for part in n_parts):
            return p

    return None


def match_profile_by_ocr_text(conn, ocr_text):
    normalized = normalize_person_name(ocr_text)
    if not normalized:
        return None

    profiles = conn.execute("SELECT * FROM profiles ORDER BY length(name) DESC").fetchall()
    for p in profiles:
        name = normalize_person_name(p["name"])
        if name and name in normalized:
            return p

    patient_name = detect_patient_name_from_text(ocr_text)
    return match_profile_by_patient_name(conn, patient_name)



def fallback_profile(conn):
    preferred = conn.execute("SELECT * FROM profiles WHERE lower(name) LIKE '%paulo%' ORDER BY id LIMIT 1").fetchone()
    if preferred:
        return preferred
    return conn.execute("SELECT * FROM profiles ORDER BY id LIMIT 1").fetchone()


def resolve_profile_or_fallback(conn, detected_profile, ocr_text):
    if detected_profile:
        return detected_profile, False
    matched = match_profile_by_ocr_text(conn, ocr_text)
    if matched:
        return matched, False
    fb = fallback_profile(conn)
    return fb, True


def unlock_pdf_try_profiles(path, profiles):
    # tenta sem senha primeiro
    try:
        reader = PdfReader(str(path))
        if len(reader.pages) >= 0:
            return path, None, "PDF sem senha ou legível sem desbloqueio.", None
    except Exception:
        pass

    for p in profiles:
        password = p["phone_suffix"] or ""
        if not password:
            continue
        tmp = tempfile.TemporaryDirectory(prefix="mv_unlock_auto_")
        out = Path(tmp.name) / "unlocked.pdf"
        try:
            with pikepdf.open(path, password=password) as pdf:
                pdf.save(out)
            return out, tmp, f"PDF desbloqueado com senha do perfil {p['name']}.", p
        except Exception:
            tmp.cleanup()

    return path, None, "Não foi possível desbloquear automaticamente com os perfis cadastrados.", None



def update_ingest_job(job_id, status=None, progress=None, stage=None, message=None, error=None, result=None):
    fields = []
    params = []
    if status is not None:
        fields.append("status=?")
        params.append(status)
    if progress is not None:
        fields.append("progress=?")
        params.append(int(progress))
    if stage is not None:
        fields.append("stage=?")
        params.append(safe_db_text(stage))
    if message is not None:
        fields.append("message=?")
        params.append(safe_db_text(message))
    if error is not None:
        fields.append("error=?")
        params.append(safe_db_text(error)[:8000])
    if result is not None:
        fields.append("result_json=?")
        params.append(json.dumps(result, ensure_ascii=False))
    fields.append("updated_at=?")
    params.append(now_iso())
    params.append(job_id)
    with db() as conn:
        conn.execute(f"UPDATE ingest_jobs SET {', '.join(fields)} WHERE id=?", params)


def process_ingest_job(job_id):
    tmp_unlock = None
    try:
        with db() as conn:
            job = conn.execute("SELECT * FROM ingest_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return
            all_profiles = conn.execute("SELECT * FROM profiles ORDER BY name").fetchall()
            profile = require_profile(conn, job["profile_id"]) if job["profile_id"] else None

        update_ingest_job(job_id, status="running", progress=8, stage="preparing", message="Preparando documento.")

        file_name = job["file_name"] or ""
        original_name = job["original_name"] or ""
        source_type = "upload"
        source_url = job["source_url"] or ""
        qr_text_value = job["qr_text"] or ""

        if source_url:
            update_ingest_job(job_id, progress=15, stage="download", message="Baixando PDF pelo link informado.")
            file_name = download_pdf_from_url(source_url)
            original_name = "downloaded-from-link.pdf"
            source_type = "link"
        elif qr_text_value:
            update_ingest_job(job_id, progress=15, stage="qrcode", message="Lendo URL do QRCode.")
            url = re.search(r"https?://[^\s<>\"]+", qr_text_value or "")
            if not url:
                raise ValueError("QRCode/texto não contém URL.")
            file_name = download_pdf_from_url(url.group(0))
            original_name = "downloaded-from-qrcode.pdf"
            source_type = "qrcode"

        if not file_name:
            raise ValueError("Nenhum arquivo, link ou QRCode informado.")

        path = UPLOAD_DIR / file_name
        file_hash = sha256_file(path) if path.exists() else ""

        with db() as conn:
            if file_hash:
                dup = conn.execute("SELECT id FROM source_documents WHERE file_sha256=? LIMIT 1", (file_hash,)).fetchone()
                if dup:
                    update_ingest_job(job_id, status="done", progress=100, stage="duplicate", message="Documento já havia sido importado anteriormente.", result={"duplicate": True, "source_document_id": dup["id"]})
                    return

        update_ingest_job(job_id, progress=25, stage="ocr", message="Iniciando OCR e leitura do documento.")

        detected_profile = None
        if file_name.lower().endswith(".pdf"):
            if profile:
                process_path, tmp_unlock, unlock_note = unlock_pdf_to_temp(path, profile["phone_suffix"] or "")
            else:
                process_path, tmp_unlock, unlock_note, detected_profile = unlock_pdf_try_profiles(path, all_profiles)

            update_ingest_job(job_id, progress=35, stage="qrcode", message="Verificando QRCode no PDF.")
            qr = extract_qr_from_pdf_first_page(process_path)
            if qr and not source_url and not qr_text_value:
                url = re.search(r"https?://[^\s<>\"]+", qr or "")
                if url:
                    try:
                        downloaded = download_pdf_from_url(url.group(0))
                    except Exception:
                        downloaded = ""
                    if downloaded:
                        file_name = downloaded
                        path = UPLOAD_DIR / file_name
                        process_path = path

            update_ingest_job(job_id, progress=45, stage="ocr", message="Executando OCR por página.")
            pages = ocr_pdf_by_page(process_path)

            update_ingest_job(job_id, progress=58, stage="classification", message="Classificando páginas.")
            groups = group_pages(pages)

            if not profile:
                joined_text = "\\n".join([p.get("text", "") for p in pages])
                with db() as conn:
                    profile, used_profile_fallback = resolve_profile_or_fallback(conn, detected_profile, joined_text)
                if not profile:
                    raise ValueError("Nenhum perfil cadastrado. Cadastre pelo menos um perfil em Configurações > Perfis.")
                if used_profile_fallback:
                    log_event("warning", "ingest", "Paciente não identificado automaticamente. Documento atribuído ao perfil padrão para revisão.", joined_text[:1000], "ingest_job", None)
        else:
            text_value = ocr_image(path)
            groups = [{"kind": classify_page(text_value), "pages": [1], "text": text_value}]
            if not profile:
                with db() as conn:
                    profile, used_profile_fallback = resolve_profile_or_fallback(conn, None, text_value)
                if not profile:
                    raise ValueError("Nenhum perfil cadastrado. Cadastre pelo menos um perfil em Configurações > Perfis.")
                if used_profile_fallback:
                    log_event("warning", "ingest", "Paciente não identificado automaticamente. Documento atribuído ao perfil padrão para revisão.", text_value[:1000], "ingest_job", None)

        update_ingest_job(job_id, progress=68, stage="ai", message="Extraindo dados com IA local.")

        with db() as conn:
            cur = conn.execute(
                """
                INSERT INTO source_documents(profile_id, title, file_name, original_name, source_type, source_url, file_sha256, document_date, historical_only, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processed', ?)
                """,
                (profile["id"], safe_db_text(original_name or "Documento médico"), safe_db_text(file_name), safe_db_text(original_name), safe_db_text(source_type), safe_db_text(source_url or qr_text_value), safe_db_text(file_hash), "", 0, now_iso()),
            )
            source_id = cur.lastrowid

            created = []
            total = max(1, len(groups))
            for idx, g in enumerate(groups):
                update_ingest_job(job_id, progress=70 + int((idx / total) * 20), stage="saving", message=f"Salvando dados extraídos ({idx + 1}/{total}).")
                created.append(save_group(conn, profile, source_id, file_name, g, False, ""))

            result = {"ok": True, "source_document_id": source_id, "created": created, "groups": [x["kind"] for x in groups], "profile": dict(profile)}
            log_event("info", "ingest", "Ingestão por job concluída.", json.dumps(result, ensure_ascii=False), "source_document", source_id)

        update_ingest_job(job_id, status="done", progress=100, stage="done", message="Documento processado com sucesso.", result=result)
    except Exception as e:
        log_event("error", "ingest", "Falha no job de ingestão.", str(e), "ingest_job", None)
        update_ingest_job(job_id, status="failed", progress=100, stage="failed", message="Falha ao processar documento.", error=str(e))
    finally:
        if tmp_unlock:
            tmp_unlock.cleanup()


@app.get("/api/ingest/ping")
def ingest_ping():
    return {"ok": True, "endpoint": "upload-job"}


@app.post("/api/ingest/upload-job")
async def ingest_upload_job(
    background_tasks: BackgroundTasks,
    profile_id: int = Form(0),
    source_url: str = Form(""),
    qr_text: str = Form(""),
    file: UploadFile | None = File(None),
):
    ensure_core_schema()

    file_name = ""
    original_name = ""

    if file and file.filename:
        original_name = Path(file.filename).name
        ext = Path(original_name).suffix.lower()
        file_name = f"{uuid.uuid4().hex}{ext}"
        content = await file.read()
        if len(content) > MAX_CONTENT_LENGTH_MB * 1024 * 1024:
            raise HTTPException(413, "Arquivo grande demais.")
        (UPLOAD_DIR / file_name).write_bytes(content)

    if not file_name and not source_url and not qr_text:
        raise HTTPException(400, "Envie arquivo, link ou QRCode.")

    job_id = uuid.uuid4().hex
    with db() as conn:
        conn.execute(
            """
            INSERT INTO ingest_jobs(id, status, progress, stage, message, file_name, original_name, source_url, qr_text, profile_id, created_at)
            VALUES (?, 'queued', 0, 'queued', 'Aguardando processamento.', ?, ?, ?, ?, ?, ?)
            """,
            (job_id, safe_db_text(file_name), safe_db_text(original_name), safe_db_text(source_url), safe_db_text(qr_text), int(profile_id or 0), now_iso()),
        )

    background_tasks.add_task(process_ingest_job, job_id)
    return {"ok": True, "job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_ingest_job(job_id: str):
    ensure_core_schema()
    with db() as conn:
        job = conn.execute("SELECT * FROM ingest_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "Job não encontrado.")
        item = dict(job)
        if item.get("result_json"):
            try:
                item["result"] = json.loads(item["result_json"])
            except Exception:
                item["result"] = None
        return item


@app.post("/api/ingest/upload")
async def ingest_upload(
    profile_id: int = Form(0),
    historical_only: int = Form(0),
    title: str = Form(""),
    document_date: str = Form(""),
    source_url: str = Form(""),
    qr_text: str = Form(""),
    file: UploadFile | None = File(None),
):
    with db() as conn:
        all_profiles = conn.execute("SELECT * FROM profiles ORDER BY name").fetchall()
        profile = require_profile(conn, profile_id) if profile_id else None

    ensure_core_schema()

    file_name = ""
    original_name = ""
    source_type = "upload"

    if source_url:
        file_name = download_pdf_from_url(source_url)
        original_name = "downloaded-from-link.pdf"
        source_type = "link"
    elif qr_text:
        url = re.search(r"https?://[^\s<>\"]+", qr_text or "")
        if not url:
            raise HTTPException(400, "QRCode/texto não contém URL.")
        file_name = download_pdf_from_url(url.group(0))
        original_name = "downloaded-from-qrcode.pdf"
        source_type = "qrcode"
    elif file and file.filename:
        original_name = Path(file.filename).name
        ext = Path(original_name).suffix.lower()
        file_name = f"{uuid.uuid4().hex}{ext}"
        content = await file.read()
        if len(content) > MAX_CONTENT_LENGTH_MB * 1024 * 1024:
            raise HTTPException(413, "Arquivo grande demais.")
        (UPLOAD_DIR / file_name).write_bytes(content)
    else:
        raise HTTPException(400, "Envie arquivo, link ou QRCode.")

    path = UPLOAD_DIR / file_name

    tmp_unlock = None
    try:
        detected_profile = None
        if file_name.lower().endswith(".pdf"):
            if profile:
                process_path, tmp_unlock, unlock_note = unlock_pdf_to_temp(path, profile["phone_suffix"] or "")
            else:
                process_path, tmp_unlock, unlock_note, detected_profile = unlock_pdf_try_profiles(path, all_profiles)
            qr = extract_qr_from_pdf_first_page(process_path)
            if qr and not source_url and not qr_text:
                url = re.search(r"https?://[^\s<>\"]+", qr or "")
                if url:
                    downloaded = ""
                    try:
                        downloaded = download_pdf_from_url(url.group(0))
                    except Exception:
                        downloaded = ""
                    if downloaded:
                        file_name = downloaded
                        path = UPLOAD_DIR / file_name
                        process_path = path

            pages = ocr_pdf_by_page(process_path)
            groups = group_pages(pages)
            if not profile:
                joined_text = "\n".join([p.get("text", "") for p in pages])
                with db() as conn:
                    profile, used_profile_fallback = resolve_profile_or_fallback(conn, detected_profile, joined_text)
                if not profile:
                    raise HTTPException(400, "Nenhum perfil cadastrado. Cadastre pelo menos um perfil em Configurações > Perfis.")
                if used_profile_fallback:
                    log_event("warning", "ingest", "Paciente não identificado automaticamente. Documento atribuído ao perfil padrão para revisão.", joined_text[:1000])
        else:
            text = ocr_image(path)
            groups = [{"kind": classify_page(text), "pages": [1], "text": text}]
            if not profile:
                patient_name = detect_patient_name_from_text(text)
                with db() as conn:
                    profile = match_profile_by_patient_name(conn, patient_name)
                if not profile:
                    raise HTTPException(400, "Não consegui identificar automaticamente o paciente. Selecione o perfil manualmente.")

        # V6.3.6 direct ingest real file_hash
        file_hash = ""
        try:
            if file_name:
                file_hash = sha256_file(UPLOAD_DIR / file_name)
        except Exception:
            file_hash = ""

        # V6.3.6 direct ingest duplicate check
        if file_hash:
            with db() as conn:
                dup = conn.execute("SELECT id FROM source_documents WHERE file_sha256=? LIMIT 1", (file_hash,)).fetchone()
                if dup:
                    return {"ok": True, "duplicate": True, "source_document_id": dup["id"], "message": "Documento já havia sido importado anteriormente."}

        with db() as conn:
            cur = conn.execute(
                """
                INSERT INTO source_documents(profile_id, title, file_name, original_name, source_type, source_url, file_sha256, document_date, historical_only, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processed', ?)
                """,
                (profile["id"], safe_db_text(title or original_name), safe_db_text(file_name), safe_db_text(original_name), safe_db_text(source_type), safe_db_text(source_url or qr_text), safe_db_text(file_hash), safe_db_text(document_date), int(historical_only), now_iso()),
            )
            source_id = cur.lastrowid

            created = []
            for g in groups:
                created.append(save_group(conn, profile, source_id, file_name, g, bool(historical_only), document_date))

            log_event("info", "ingest", "Ingestão concluída.", json.dumps({"created": created, "groups": [x["kind"] for x in groups]}, ensure_ascii=False), "source_document", source_id)
            return {"ok": True, "source_document_id": source_id, "created": created, "groups": [x["kind"] for x in groups]}
    finally:
        if tmp_unlock:
            tmp_unlock.cleanup()


@app.post("/api/ingest/n8n")
async def ingest_n8n(
    token: str = Form(""),
    profile_id: int = Form(0),
    historical_only: int = Form(0),
    title: str = Form(""),
    document_date: str = Form(""),
    source_url: str = Form(""),
    qr_text: str = Form(""),
    file: UploadFile | None = File(None),
):
    if current_n8n_token() and token != current_n8n_token():
        raise HTTPException(401, "Token inválido.")
    return await ingest_upload(profile_id, historical_only, title, document_date, source_url, qr_text, file)




@app.post("/api/inventory/purchase-preview")
async def inventory_purchase_preview(profile_id: int = Form(0), file: UploadFile = File(...)):
    try:
        if not profile_id:
            raise HTTPException(400, "Perfil obrigatório.")

        original_name = safe_filename(file.filename or "compra")
        suffix = Path(original_name).suffix.lower()
        if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            raise HTTPException(400, "Envie PDF ou imagem da nota/pedido.")

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stored_name = f"purchase_{uuid.uuid4().hex}{suffix or '.bin'}"
        path = UPLOAD_DIR / stored_name
        content = await file.read()
        if len(content) > MAX_CONTENT_LENGTH_MB * 1024 * 1024:
            raise HTTPException(413, "Arquivo muito grande.")
        path.write_bytes(content)

        with db() as conn:
            require_profile(conn, profile_id)

        text = extract_text_from_purchase_file(path)
        extracted = analyze_purchase_with_ollama(text)

        return {
            "profile_id": profile_id,
            "file_name": stored_name,
            "original_name": original_name,
            "vendor": extracted.get("vendor", ""),
            "purchase_date": extracted.get("purchase_date") or today_date(),
            "items": extracted.get("items", []),
            "raw_text": extracted.get("raw_text", "")[:5000],
            "source": extracted.get("source", "unknown"),
        }
    except HTTPException:
        raise
    except Exception as e:
        log_event("error", "inventory", "Falha ao pré-processar nota/print.", str(e))
        raise HTTPException(500, f"Falha ao ler nota/print: {e}")


@app.post("/api/inventory/purchase-confirm")
def inventory_purchase_confirm(payload: dict):
    profile_id = safe_int(payload.get("profile_id"), 0)
    if not profile_id:
        raise HTTPException(400, "Perfil obrigatório.")

    vendor = safe_db_text(payload.get("vendor") or "")
    purchase_date = safe_db_text(payload.get("purchase_date") or today_date())
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "Nenhum item para importar.")

    results = []
    with db() as conn:
        require_profile(conn, profile_id)
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("import") is False:
                continue
            results.append(upsert_inventory_from_purchase(conn, profile_id, item, purchase_date, vendor))

    return {"ok": True, "results": results}


@app.post("/api/inventory")
def create_inventory_item(payload: dict):
    profile_id = safe_int(payload.get("profile_id"), 0)
    medication_name = safe_db_text(payload.get("medication_name") or "").strip()
    if not profile_id:
        raise HTTPException(400, "Perfil obrigatório.")
    if not medication_name:
        raise HTTPException(400, "Medicamento obrigatório.")

    quantity = float(payload.get("quantity") or payload.get("units_on_hand") or 0)
    total_price = float(payload.get("total_price") or 0)
    unit_price = float(payload.get("unit_price") or 0)
    if not unit_price and total_price and quantity:
        unit_price = total_price / quantity

    with db() as conn:
        require_profile(conn, profile_id)
        cur = conn.execute(
            """
            INSERT INTO medication_inventory(profile_id, medication_name, unit_label, units_on_hand, low_stock_threshold, dose_quantity, requires_prescription, prescription_id, default_frequency, interval_days, routine_preset, preferred_time, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                medication_name,
                safe_db_text(payload.get("unit_label") or "unidade"),
                quantity,
                float(payload.get("low_stock_threshold") or 1),
                float(payload.get("dose_quantity") or 1),
                int(bool(payload.get("requires_prescription"))),
                safe_int(payload.get("prescription_id"), 0),
                safe_db_text(payload.get("default_frequency") or ""),
                safe_int(payload.get("interval_days"), 0),
                safe_db_text(payload.get("routine_preset") or ""),
                safe_db_text(payload.get("preferred_time") or ""),
                safe_db_text(payload.get("notes") or ""),
                now_iso(),
            ),
        )
        inventory_id = cur.lastrowid

        if quantity > 0:
            conn.execute(
                """
                INSERT INTO inventory_movements(inventory_id, profile_id, movement_type, quantity, units_after, notes, created_at)
                VALUES (?, ?, 'purchase', ?, ?, ?, ?)
                """,
                (inventory_id, profile_id, quantity, quantity, "Estoque inicial", now_iso()),
            )
            conn.execute(
                """
                INSERT INTO inventory_purchases(inventory_id, profile_id, quantity, total_price, unit_price, purchase_date, vendor, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inventory_id,
                    profile_id,
                    quantity,
                    total_price,
                    unit_price,
                    safe_db_text(payload.get("purchase_date") or today_date()),
                    safe_db_text(payload.get("vendor") or ""),
                    safe_db_text(payload.get("purchase_notes") or ""),
                    now_iso(),
                ),
            )

        item = conn.execute("SELECT * FROM medication_inventory WHERE id=?", (inventory_id,)).fetchone()
        if payload.get("create_reminder") or payload.get("default_frequency") or payload.get("interval_days"):
            ensure_inventory_treatment(conn, item)

        return row(conn.execute("SELECT * FROM medication_inventory WHERE id=?", (inventory_id,)).fetchone())


@app.post("/api/inventory/{inventory_id}/purchase")
def add_inventory_purchase(inventory_id: int, payload: dict):
    quantity = float(payload.get("quantity") or 0)
    if quantity <= 0:
        raise HTTPException(400, "Quantidade inválida.")

    total_price = float(payload.get("total_price") or 0)
    unit_price = float(payload.get("unit_price") or 0)
    if not unit_price and total_price:
        unit_price = total_price / quantity

    with db() as conn:
        item = conn.execute("SELECT * FROM medication_inventory WHERE id=?", (inventory_id,)).fetchone()
        if not item:
            raise HTTPException(404, "Item não encontrado.")

        new_total = float(item["units_on_hand"] or 0) + quantity
        conn.execute(
            "UPDATE medication_inventory SET units_on_hand=?, last_low_stock_notified_at='', updated_at=? WHERE id=?",
            (new_total, now_iso(), inventory_id),
        )
        conn.execute(
            """
            INSERT INTO inventory_purchases(inventory_id, profile_id, quantity, total_price, unit_price, purchase_date, vendor, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inventory_id,
                item["profile_id"],
                quantity,
                total_price,
                unit_price,
                safe_db_text(payload.get("purchase_date") or today_date()),
                safe_db_text(payload.get("vendor") or ""),
                safe_db_text(payload.get("notes") or ""),
                now_iso(),
            ),
        )
        conn.execute(
            """
            INSERT INTO inventory_movements(inventory_id, profile_id, movement_type, quantity, units_after, notes, created_at)
            VALUES (?, ?, 'purchase', ?, ?, ?, ?)
            """,
            (inventory_id, item["profile_id"], quantity, new_total, "Compra/reposição manual", now_iso()),
        )
        return row(conn.execute("SELECT * FROM medication_inventory WHERE id=?", (inventory_id,)).fetchone())


@app.put("/api/inventory/{inventory_id}")
def update_inventory_item(inventory_id: int, payload: dict):
    with db() as conn:
        item = conn.execute("SELECT * FROM medication_inventory WHERE id=?", (inventory_id,)).fetchone()
        if not item:
            raise HTTPException(404, "Item não encontrado.")
        conn.execute(
            """
            UPDATE medication_inventory
            SET medication_name=?, unit_label=?, low_stock_threshold=?, requires_prescription=?, default_frequency=?, interval_days=?, notes=?, active=?, updated_at=?
            WHERE id=?
            """,
            (
                safe_db_text(payload.get("medication_name") or item["medication_name"]),
                safe_db_text(payload.get("unit_label") or item["unit_label"]),
                float(payload.get("low_stock_threshold") if payload.get("low_stock_threshold") not in (None, "") else item["low_stock_threshold"]),
                int(bool(payload.get("requires_prescription"))) if "requires_prescription" in payload else int(item["requires_prescription"] or 0),
                safe_db_text(payload.get("default_frequency") if payload.get("default_frequency") is not None else item["default_frequency"]),
                safe_int(payload.get("interval_days"), int(item["interval_days"] or 0)),
                safe_db_text(payload.get("notes") if payload.get("notes") is not None else item["notes"]),
                int(payload.get("active", item["active"])),
                now_iso(),
                inventory_id,
            ),
        )
        item = conn.execute("SELECT * FROM medication_inventory WHERE id=?", (inventory_id,)).fetchone()
        if payload.get("create_reminder") or item["default_frequency"] or item["interval_days"]:
            ensure_inventory_treatment(conn, item)
        return row(conn.execute("SELECT * FROM medication_inventory WHERE id=?", (inventory_id,)).fetchone())


@app.delete("/api/inventory/{inventory_id}")
def delete_inventory_item(inventory_id: int):
    with db() as conn:
        item = conn.execute("SELECT * FROM medication_inventory WHERE id=?", (inventory_id,)).fetchone()
        if not item:
            raise HTTPException(404, "Item não encontrado.")
        conn.execute("DELETE FROM medication_inventory WHERE id=?", (inventory_id,))
        return {"ok": True}


@app.get("/api/inventory/price-chart")
def inventory_price_chart(inventory_id: int):
    with db() as conn:
        return {"items": rows(conn.execute(
            """
            SELECT purchase_date, quantity, total_price, unit_price, vendor
            FROM inventory_purchases
            WHERE inventory_id=?
            ORDER BY purchase_date ASC, id ASC
            """,
            (inventory_id,),
        ).fetchall())}



@app.post("/api/treatment-events/{event_id}/undo")
def undo_treatment_event(event_id: int):
    with db() as conn:
        e = conn.execute(
            """
            SELECT e.*, t.supply_total, t.supply_used, t.current_prescription_id
            FROM treatment_events e
            JOIN treatments t ON t.id=e.treatment_id
            WHERE e.id=?
            """,
            (event_id,),
        ).fetchone()
        if not e:
            raise HTTPException(404, "Evento não encontrado.")

        previous_status = e["status"] or "pending"
        if previous_status == "pending":
            return {"ok": True, "status": "pending"}

        conn.execute(
            """
            UPDATE treatment_events
            SET status='pending', completed_at='', updated_at=?, reminder_sent_at='', ha_notified_at=''
            WHERE id=?
            """,
            (now_iso(), event_id),
        )

        if previous_status in ("applied", "taken"):
            inventory = find_inventory_for_treatment(conn, e)
            if inventory:
                new_total = float(inventory["units_on_hand"] or 0) + 1
                conn.execute(
                    "UPDATE medication_inventory SET units_on_hand=?, updated_at=? WHERE id=?",
                    (new_total, now_iso(), inventory["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO inventory_movements(inventory_id, profile_id, movement_type, quantity, units_after, related_type, related_id, notes, created_at)
                    VALUES (?, ?, 'undo', ?, ?, 'treatment_event', ?, ?, ?)
                    """,
                    (inventory["id"], inventory["profile_id"], 1, new_total, event_id, "Desfeito pela interface", now_iso()),
                )

            supply_used = max(int(e["supply_used"] or 0) - 1, 0)
            conn.execute(
                "UPDATE treatments SET supply_used=?, updated_at=? WHERE id=?",
                (supply_used, now_iso(), e["treatment_id"]),
            )
            if int(e["current_prescription_id"] or 0) > 0:
                conn.execute(
                    "UPDATE prescriptions SET status='active', updated_at=? WHERE id=?",
                    (now_iso(), e["current_prescription_id"]),
                )

        return {"ok": True, "status": "pending", "undone_from": previous_status}


@app.post("/api/treatment-events/{event_id}/action")
def treatment_event_action(event_id: int, payload: dict):
    action = payload.get("action") or payload.get("status") or "completed"
    allowed = {"taken", "applied", "scheduled", "rescheduled", "postponed", "skipped", "missed", "cancelled"}
    if action not in allowed:
        raise HTTPException(400, "Ação inválida.")

    with db() as conn:
        e = conn.execute("""
            SELECT e.*, t.interval_days, t.default_action, t.supply_total, t.supply_used, t.current_prescription_id, t.requires_prescription,
                   t.name treatment_name, t.profile_id profile_id
            FROM treatment_events e JOIN treatments t ON t.id=e.treatment_id
            WHERE e.id=?
        """, (event_id,)).fetchone()
        if not e:
            raise HTTPException(404, "Evento não encontrado.")

        if action == "scheduled":
            conn.execute(
                "UPDATE treatment_events SET notes=?, updated_at=? WHERE id=?",
                (safe_db_text(payload.get("notes") or "Agendamento confirmado"), now_iso(), event_id),
            )
            return {"ok": True, "status": "pending"}

        if action == "rescheduled":
            try:
                sync_calendar_ics()
            except Exception as ex:
                log_event("warning", "calendar", "Falha ao sincronizar calendário antes de remarcar.", str(ex), "treatment_event", event_id)

            treatment = conn.execute(
                "SELECT t.*, p.name profile_name FROM treatments t JOIN profiles p ON p.id=t.profile_id WHERE t.id=?",
                (e["treatment_id"],),
            ).fetchone()
            rx = conn.execute("SELECT file_name FROM prescriptions WHERE id=?", (e["prescription_id"],)).fetchone() if e["prescription_id"] else None
            plan = plan_next_treatment_event(conn, treatment, e["prescription_id"], today_date(), rx["file_name"] if rx else e["prescription_file_name"], exclude_event_id=event_id)
            if not plan:
                return {"ok": False, "status": "pending", "message": "Nenhum novo agendamento encontrado no calendário."}
            conn.execute(
                """
                UPDATE treatment_events
                SET scheduled_for=?, scheduled_at=?, linked_calendar_event_id=?, administration_side=?, reminder_due_at=?,
                    reminder_sent_at='', ha_notified_at='', notes=?, updated_at=?
                WHERE id=?
                """,
                (
                    plan["scheduled_for"], plan["scheduled_at"], plan["linked_calendar_event_id"], plan["administration_side"],
                    plan["reminder_due_at"], "Remarcado a partir do calendário", now_iso(), event_id,
                ),
            )
            return {"ok": True, "status": "pending", "rescheduled": True, "scheduled_for": plan["scheduled_for"], "scheduled_at": plan["scheduled_at"]}

        if action == "postponed":
            base_dt = parse_datetime_value(e["scheduled_at"]) or datetime.combine(parse_date(e["scheduled_for"]) or datetime.now().date(), datetime.min.time())
            new_dt = base_dt + timedelta(days=1)
            conn.execute(
                "UPDATE treatment_events SET scheduled_for=?, scheduled_at=?, reminder_due_at=?, reminder_sent_at='', ha_notified_at='', notes=?, updated_at=? WHERE id=?",
                (new_dt.date().isoformat(), new_dt.isoformat(timespec='minutes') if e["scheduled_at"] else "", (new_dt + timedelta(minutes=30)).isoformat(timespec='minutes') if e["scheduled_at"] else "", "Adiado 1 dia", now_iso(), event_id),
            )
            return {"ok": True, "status": "postponed", "new_date": new_dt.date().isoformat()}

        completed_at = now_iso()
        conn.execute("UPDATE treatment_events SET status=?, completed_at=?, updated_at=? WHERE id=?", (action, completed_at, now_iso(), event_id))

        if action in {"applied", "taken"}:
            supply_total = int(e["supply_total"] or 0)
            supply_used = int(e["supply_used"] or 0)
            if supply_total > 0:
                supply_used += 1
                conn.execute("UPDATE treatments SET supply_used=?, updated_at=? WHERE id=?", (supply_used, now_iso(), e["treatment_id"]))
                remaining = supply_total - supply_used
                if int(e["current_prescription_id"] or 0) > 0:
                    new_status = "exhausted" if remaining <= 0 else "active"
                    conn.execute("UPDATE prescriptions SET status=?, updated_at=? WHERE id=?", (new_status, now_iso(), e["current_prescription_id"]))
                if remaining <= 0:
                    consume_inventory_for_treatment(conn, e, 1)
                    return {"ok": True, "status": action, "prescription_status": "exhausted"}

            consume_inventory_for_treatment(conn, e, 1)

            ensure_treatment_future_events(conn, e["treatment_id"], min_pending=4)

        return {"ok": True, "status": action}


@app.put("/api/treatments/{treatment_id}")
def update_treatment(treatment_id: int, payload: dict):
    with db() as conn:
        t = conn.execute("SELECT * FROM treatments WHERE id=?", (treatment_id,)).fetchone()
        if not t:
            raise HTTPException(404, "Tratamento não encontrado.")
        conn.execute(
            """
            UPDATE treatments
            SET rule_notes=?,
                preferred_start_side=COALESCE(NULLIF(?, ''), preferred_start_side),
                side_mode=COALESCE(NULLIF(?, ''), side_mode),
                updated_at=?
            WHERE id=?
            """,
            (safe_db_text(payload.get("rule_notes") or ""), safe_db_text(payload.get("preferred_start_side") or ""), safe_db_text(payload.get("side_mode") or ""), now_iso(), treatment_id),
        )
        current_rx = int(payload.get("current_prescription_id") or t["current_prescription_id"] or 0)
        rx = conn.execute("SELECT file_name FROM prescriptions WHERE id=?", (current_rx,)).fetchone() if current_rx else None
        upsert_pending_treatment_event(conn, treatment_id, current_rx, today_date(), rx["file_name"] if rx else "")
    return {"ok": True}


@app.get("/api/search")
def search(q: str):
    with db() as conn:
        try:
            return {"items": rows(conn.execute(
                """
                SELECT entity_type, entity_id, snippet(health_fts, 4, '<mark>', '</mark>', '…', 20) snippet
                FROM health_fts
                WHERE health_fts MATCH ?
                LIMIT 50
                """,
                (q,),
            ).fetchall())}
        except Exception as e:
            return {"items": [], "error": str(e)}


@app.get("/api/exam-chart")
def exam_chart(marker: str, profile_id: int | None = None):
    normalized = normalize_exam_name(marker)
    sql = "SELECT * FROM exam_markers WHERE normalized_name=?"
    params = [normalized]
    if profile_id:
        sql += " AND profile_id=?"
        params.append(profile_id)
    sql += " ORDER BY result_date ASC"
    with db() as conn:
        return {"marker": normalized, "items": rows(conn.execute(sql, params).fetchall())}


@app.post("/api/share")
def create_share(payload: dict):
    file_name = payload.get("file_name")
    ttl = safe_int(payload.get("ttl_minutes"), 60)
    if not file_name or not (UPLOAD_DIR / file_name).exists():
        raise HTTPException(404, "Arquivo não encontrado.")
    token = uuid.uuid4().hex
    expires = (datetime.now() + timedelta(minutes=ttl)).isoformat(timespec="seconds")
    with db() as conn:
        conn.execute("INSERT INTO share_tokens(token, file_name, expires_at, created_at) VALUES (?, ?, ?, ?)", (token, file_name, expires, now_iso()))
    return {"token": token, "url": f"{current_share_base_url()}/share/{token}" if current_share_base_url() else f"/share/{token}", "expires_at": expires}


@app.get("/share/{token}")
def share(token: str):
    with db() as conn:
        r = conn.execute("SELECT * FROM share_tokens WHERE token=? AND expires_at>=?", (token, now_iso())).fetchone()
    if not r:
        raise HTTPException(404, "Link inválido ou expirado.")
    return FileResponse(UPLOAD_DIR / r["file_name"])




@app.post("/api/calendar/sync")
def calendar_sync_manual(payload: dict | None = None):
    ensure_core_schema()
    apply_settings_payload(payload, keys={"calendar_ics_url"})
    result = sync_calendar_ics()
    # Replaneja pendências abertas para usar eventos reais recém-sincronizados.
    try:
        with db() as conn:
            pending_treatments = conn.execute(
                """
                SELECT DISTINCT t.id, t.current_prescription_id, r.issue_date, r.file_name
                FROM treatments t
                LEFT JOIN prescriptions r ON r.id=t.current_prescription_id
                WHERE t.active=1
                """
            ).fetchall()
            for t in pending_treatments:
                upsert_pending_treatment_event(conn, t["id"], int(t["current_prescription_id"] or 0), t["issue_date"] or today_date(), t["file_name"] or "")
    except Exception as e:
        log_event("warning", "calendar", "Calendário sincronizado, mas replanejamento falhou.", str(e))
    return result


@app.get("/api/calendar/events")
def calendar_events():
    sync_calendar_ics()
    with db() as conn:
        return {"items": rows(conn.execute("""
            SELECT * FROM calendar_events
            WHERE starts_at >= ?
            ORDER BY starts_at ASC
            LIMIT 50
        """, (datetime.now().isoformat(timespec="minutes"),)).fetchall())}





@app.post("/api/exam-orders/{order_id}/schedule")
def schedule_exam_order(order_id: int, payload: dict):
    scheduled_at = payload.get("scheduled_at") or ""
    location = payload.get("scheduled_location") or ""
    calendar_title = payload.get("scheduled_calendar_title") or ""

    if not scheduled_at:
        raise HTTPException(400, "Data do agendamento é obrigatória.")

    with db() as conn:
        order = conn.execute("SELECT * FROM exam_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "Pedido de exame não encontrado.")

        conn.execute(
            """
            UPDATE exam_orders
            SET status='scheduled',
                scheduled_at=?,
                scheduled_location=?,
                scheduled_calendar_title=?,
                updated_at=?
            WHERE id=?
            """,
            (scheduled_at, location, calendar_title, now_iso(), order_id),
        )
        conn.execute("UPDATE exam_order_items SET status='scheduled' WHERE exam_order_id=?", (order_id,))
        log_event("info", "exam", "Pedido de exame vinculado a data de agendamento.", json.dumps(payload, ensure_ascii=False), "exam_order", order_id)

    return {"ok": True}


@app.post("/api/exam-orders/{order_id}/performed")
def mark_exam_order_performed(order_id: int, payload: dict):
    performed_at = payload.get("performed_at") or today_date()
    result_expected_at = payload.get("result_expected_at") or ""
    notes = payload.get("notes") or ""

    with db() as conn:
        order = conn.execute("SELECT * FROM exam_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "Pedido de exame não encontrado.")

        conn.execute(
            """
            UPDATE exam_orders
            SET status='performed',
                performed_at=?,
                result_expected_at=?,
                result_reminded_at='',
                result_notes=?,
                updated_at=?
            WHERE id=?
            """,
            (performed_at, result_expected_at, notes, now_iso(), order_id),
        )

        conn.execute("UPDATE exam_order_items SET status='performed' WHERE exam_order_id=?", (order_id,))
        log_event("info", "exam", "Pedido de exame marcado como realizado.", json.dumps({"performed_at": performed_at, "result_expected_at": result_expected_at}, ensure_ascii=False), "exam_order", order_id)

    return {"ok": True}


@app.post("/api/exam-orders/{order_id}/result-expected")
def set_exam_result_expected(order_id: int, payload: dict):
    result_expected_at = payload.get("result_expected_at") or ""
    notes = payload.get("notes") or ""

    with db() as conn:
        order = conn.execute("SELECT * FROM exam_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "Pedido de exame não encontrado.")

        conn.execute(
            """
            UPDATE exam_orders
            SET result_expected_at=?,
                result_reminded_at='',
                result_notes=?,
                updated_at=?
            WHERE id=?
            """,
            (result_expected_at, notes, now_iso(), order_id),
        )

    return {"ok": True}


@app.post("/api/exam-orders/{order_id}/result-received")
def mark_exam_result_received(order_id: int, payload: dict | None = None):
    with db() as conn:
        order = conn.execute("SELECT * FROM exam_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "Pedido de exame não encontrado.")

        conn.execute(
            """
            UPDATE exam_orders
            SET status='result_pending_upload',
                result_reminded_at=?,
                updated_at=?
            WHERE id=?
            """,
            (now_iso(), now_iso(), order_id),
        )
        conn.execute("UPDATE exam_order_items SET status='result_pending_upload' WHERE exam_order_id=?", (order_id,))
        log_event("info", "exam", "Resultado informado como disponível. Aguardando upload do laudo.", "", "exam_order", order_id)

    return {"ok": True}






def delete_fts_entries(conn, entity_type, entity_ids):
    if not entity_ids:
        return
    if not isinstance(entity_ids, (list, tuple, set)):
        entity_ids = [entity_ids]
    for eid in entity_ids:
        try:
            conn.execute("DELETE FROM health_fts WHERE entity_type=? AND entity_id=?", (entity_type, eid))
        except Exception as e:
            log_event("warning", "fts", f"Falha ao limpar FTS para {entity_type}:{eid}.", str(e))


def safe_unlink_upload(file_name):
    if not file_name:
        return
    try:
        p = UPLOAD_DIR / Path(file_name).name
        if p.exists() and p.is_file():
            p.unlink()
    except Exception as e:
        log_event("warning", "file", f"Falha ao apagar arquivo {file_name}.", str(e))




def cleanup_source_derived_for_reprocess(conn, source_id: int):
    prescription_ids = [r["id"] for r in conn.execute("SELECT id FROM prescriptions WHERE source_document_id=?", (source_id,)).fetchall()]
    exam_order_ids = [r["id"] for r in conn.execute("SELECT id FROM exam_orders WHERE source_document_id=?", (source_id,)).fetchall()]
    exam_result_ids = [r["id"] for r in conn.execute("SELECT id FROM exam_results WHERE source_document_id=?", (source_id,)).fetchall()]

    delete_fts_entries(conn, "prescription", prescription_ids)
    delete_fts_entries(conn, "exam_order", exam_order_ids)
    delete_fts_entries(conn, "exam_result", exam_result_ids)

    if prescription_ids:
        placeholders = ",".join(["?"] * len(prescription_ids))
        try:
            conn.execute(f"DELETE FROM treatment_events WHERE prescription_id IN ({placeholders})", prescription_ids)
        except Exception:
            pass

    conn.execute("DELETE FROM prescriptions WHERE source_document_id=?", (source_id,))
    conn.execute("DELETE FROM exam_orders WHERE source_document_id=?", (source_id,))
    conn.execute("DELETE FROM exam_results WHERE source_document_id=?", (source_id,))
    conn.execute("DELETE FROM inbox_items WHERE source_document_id=?", (source_id,))



@app.post("/api/inbox/{inbox_id}/resolve")
def resolve_inbox_item(inbox_id: int, payload: dict | None = None):
    """Marca um item da central de revisão como resolvido/revisado."""
    status = safe_db_text((payload or {}).get("status") or "reviewed").strip() or "reviewed"
    if status not in {"reviewed", "ignored", "resolved"}:
        status = "reviewed"

    with db() as conn:
        item = conn.execute("SELECT * FROM inbox_items WHERE id=?", (inbox_id,)).fetchone()
        if not item:
            raise HTTPException(404, "Item de revisão não encontrado.")
        conn.execute(
            "UPDATE inbox_items SET status=?, reviewed_at=? WHERE id=?",
            (status, now_iso(), inbox_id),
        )
        return row(conn.execute("SELECT * FROM inbox_items WHERE id=?", (inbox_id,)).fetchone())


@app.post("/api/source-documents/{source_id}/reprocess")
def reprocess_source_document(source_id: int, background_tasks: BackgroundTasks):
    with db() as conn:
        src = conn.execute("SELECT * FROM source_documents WHERE id=?", (source_id,)).fetchone()
        if not src:
            raise HTTPException(404, "Documento não encontrado.")
        file_name = src["file_name"]
        if not file_name or not (UPLOAD_DIR / file_name).exists():
            raise HTTPException(404, "Arquivo original não encontrado.")

        cleanup_source_derived_for_reprocess(conn, source_id)
        conn.execute("UPDATE source_documents SET file_sha256='', status='reprocessing', updated_at=? WHERE id=?", (now_iso(), source_id))

        job_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO ingest_jobs(id, status, progress, stage, message, file_name, original_name, source_url, qr_text, profile_id, created_at)
            VALUES (?, 'queued', 0, 'queued', 'Aguardando reprocessamento.', ?, ?, '', '', ?, ?)
            """,
            (job_id, file_name, src["original_name"] or file_name, src["profile_id"], now_iso()),
        )
    background_tasks.add_task(process_ingest_job, job_id)
    return {"ok": True, "job_id": job_id}


@app.delete("/api/source-documents/{source_id}")
def delete_source_document(source_id: int):
    """
    Exclusão total para arquivo importado por engano:
    remove documento fonte, derivados, itens, eventos vinculados por receita e arquivos PDFs.
    """
    with db() as conn:
        src = conn.execute("SELECT * FROM source_documents WHERE id=?", (source_id,)).fetchone()
        if not src:
            raise HTTPException(404, "Documento fonte não encontrado.")

        files_to_remove = set()
        if src["file_name"]:
            files_to_remove.add(src["file_name"])

        prescription_ids = [r["id"] for r in conn.execute("SELECT id, file_name FROM prescriptions WHERE source_document_id=?", (source_id,)).fetchall()]
        for r in conn.execute("SELECT file_name FROM prescriptions WHERE source_document_id=?", (source_id,)).fetchall():
            if r["file_name"]:
                files_to_remove.add(r["file_name"])

        for r in conn.execute("SELECT file_name FROM exam_orders WHERE source_document_id=?", (source_id,)).fetchall():
            if r["file_name"]:
                files_to_remove.add(r["file_name"])

        # FTS cleanup before deleting derived entities
        prescription_ids_for_fts = [r["id"] for r in conn.execute("SELECT id FROM prescriptions WHERE source_document_id=?", (source_id,)).fetchall()]
        exam_order_ids_for_fts = [r["id"] for r in conn.execute("SELECT id FROM exam_orders WHERE source_document_id=?", (source_id,)).fetchall()]
        exam_result_ids_for_fts = [r["id"] for r in conn.execute("SELECT id FROM exam_results WHERE source_document_id=?", (source_id,)).fetchall()]
        delete_fts_entries(conn, "prescription", prescription_ids_for_fts)
        delete_fts_entries(conn, "exam_order", exam_order_ids_for_fts)
        delete_fts_entries(conn, "exam_result", exam_result_ids_for_fts)

        # Remove eventos de tratamento criados a partir de receitas desse documento.
        if prescription_ids:
            placeholders = ",".join(["?"] * len(prescription_ids))
            conn.execute(f"DELETE FROM treatment_events WHERE prescription_id IN ({placeholders})", prescription_ids)

        # Remove tratamentos que ficaram sem eventos e foram criados automaticamente por itens desse documento.
        # Mantemos tratamentos que possam ter histórico de outras receitas/eventos.
        treatment_ids = [r["treatment_id"] for r in conn.execute("SELECT DISTINCT treatment_id FROM treatment_events WHERE prescription_id IN (SELECT id FROM prescriptions WHERE source_document_id=?)", (source_id,)).fetchall()]
        for tid in treatment_ids:
            count = conn.execute("SELECT COUNT(*) c FROM treatment_events WHERE treatment_id=?", (tid,)).fetchone()["c"]
            if count == 0:
                conn.execute("DELETE FROM treatments WHERE id=?", (tid,))

        conn.execute("DELETE FROM prescriptions WHERE source_document_id=?", (source_id,))
        conn.execute("DELETE FROM exam_orders WHERE source_document_id=?", (source_id,))
        conn.execute("DELETE FROM exam_results WHERE source_document_id=?", (source_id,))
        conn.execute("DELETE FROM inbox_items WHERE source_document_id=?", (source_id,))
        conn.execute("DELETE FROM source_documents WHERE id=?", (source_id,))

        log_event("info", "document", "Documento importado excluído completamente.", f"source_id={source_id}; arquivos={list(files_to_remove)}", "source_document", source_id)

    for f in files_to_remove:
        safe_unlink_upload(f)

    return {"ok": True, "deleted_source_document": source_id, "deleted_files": list(files_to_remove)}




@app.delete("/api/exam-orders/{order_id}/full-import")
def delete_exam_order_full_import(order_id: int):
    with db() as conn:
        order = conn.execute("SELECT * FROM exam_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "Pedido de exame não encontrado.")
        source_id = order["source_document_id"]
        if not source_id:
            raise HTTPException(400, "Este pedido não possui documento original vinculado. Use exclusão simples.")

    return delete_source_document(source_id)



@app.post("/api/exam-orders/{order_id}/reset-pending")
def reset_exam_order_pending(order_id: int):
    with db() as conn:
        order = conn.execute("SELECT * FROM exam_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "Pedido de exame não encontrado.")

        conn.execute(
            """
            UPDATE exam_orders
            SET status='pending',
                scheduled_at='',
                scheduled_location='',
                scheduled_calendar_title='',
                performed_at='',
                result_expected_at='',
                result_reminded_at='',
                result_notes='',
                updated_at=?
            WHERE id=?
            """,
            (now_iso(), order_id),
        )
        conn.execute("UPDATE exam_order_items SET status='pending' WHERE exam_order_id=?", (order_id,))
        log_event("info", "exam", "Pedido de exame marcado como pendente novamente.", "", "exam_order", order_id)

    return {"ok": True}


@app.delete("/api/exam-orders/{order_id}")
def delete_exam_order(order_id: int):
    with db() as conn:
        order = conn.execute("SELECT * FROM exam_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "Pedido de exame não encontrado.")

        file_name = order["file_name"] if "file_name" in order.keys() else ""
        conn.execute("DELETE FROM exam_orders WHERE id=?", (order_id,))
        log_event("info", "exam", "Pedido de exame excluído.", f"Arquivo vinculado mantido: {file_name}", "exam_order", order_id)

    return {"ok": True}



@app.get("/api/settings")
def get_settings():
    keys = [
        ("ollama_base_url", 0),
        ("ollama_model", 0),
        ("calendar_ics_url", 1),
        ("ha_webhook_url", 1),
        ("n8n_ingest_token", 1),
        ("share_base_url", 0),
        ("self_base_url", 0),
    ]
    result = {}
    with db() as conn:
        for key, secret in keys:
            r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            value = r["value"] if r else ""
            result[key] = {
                "configured": bool(value),
                "value": "" if secret and value else value,
                "masked": masked_value(value) if secret and value else value,
                "is_secret": bool(secret),
            }
    result["api_base"] = current_self_base_url()
    result["n8n_endpoint"] = f"{current_self_base_url()}/api/ingest/n8n"
    return result


@app.put("/api/settings")
def update_settings(payload: dict):
    apply_settings_payload(payload)
    return {"ok": True, "settings": get_settings()}


@app.delete("/api/settings/{key}")
def delete_setting(key: str):
    allowed = {"ollama_base_url", "ollama_model", "calendar_ics_url", "ha_webhook_url", "n8n_ingest_token", "share_base_url"}
    if key not in allowed:
        raise HTTPException(400, "Configuração inválida.")
    with db() as conn:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
    return {"ok": True}


@app.post("/api/settings/test/{target}")
def test_setting(target: str, payload: dict | None = None):
    ensure_core_schema()

    if target == "ollama":
        apply_settings_payload(payload, keys={"ollama_base_url", "ollama_model"})
        url = current_ollama_url()
        if not url:
            raise HTTPException(400, "Ollama URL não configurada.")
        try:
            r = requests.get(f"{url}/api/tags", timeout=10)
            r.raise_for_status()
            return {"ok": True, "models": r.json().get("models", [])}
        except Exception as e:
            raise HTTPException(500, f"Falha ao testar Ollama: {e}")

    if target == "calendar":
        apply_settings_payload(payload, keys={"calendar_ics_url"})
        return calendar_sync_manual({})

    if target == "ha":
        apply_settings_payload(payload, keys={"ha_webhook_url"})
        url = current_ha_webhook_url()
        if not url:
            raise HTTPException(400, "Webhook HA não configurado.")
        test_payload = {"source": "medvault", "type": "test", "message": "Teste de integração MedVault"}
        try:
            r = requests.post(url, json=test_payload, timeout=10)
            return {"ok": r.status_code < 400, "status_code": r.status_code}
        except Exception as e:
            raise HTTPException(500, f"Falha ao testar Home Assistant: {e}")

    raise HTTPException(400, "Teste inválido.")

@app.get("/api/export")
def export_data():
    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as z:
        if DB_PATH.exists():
            z.write(DB_PATH, "medvault.sqlite3")
        for f in UPLOAD_DIR.glob("*"):
            if f.is_file() and not f.name.endswith(".txt") and "_unlocked" not in f.name:
                z.write(f, f"uploads/{f.name}")
    buff.seek(0)
    return Response(buff.read(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="medvault-export-{datetime.now().strftime("%Y%m%d-%H%M%S")}.zip"'})


@app.get("/uploads/{name}")
def get_upload(name: str):
    safe_name = Path(name).name
    p = UPLOAD_DIR / safe_name
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(p)


init_db()