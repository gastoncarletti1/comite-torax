from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    make_response,
    send_from_directory,
    send_file,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from markupsafe import Markup
from sqlalchemy import text, or_, and_

import smtplib
import ssl
from email.message import EmailMessage
import os
import datetime
import hashlib
import shutil
import json
import csv
from io import StringIO, BytesIO
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CATALOG_FILE = os.path.join(BASE_DIR, "catalogs.json")
AUDIT_LOG = os.path.join(BASE_DIR, "audit.log")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
ALLOWED_STUDY_EXTENSIONS = {"pdf"}
ALLOWED_PATIENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}

# Versión de la app (para mostrar badge visual en UI).
# Usa APP_VERSION si está definida; si no, intenta RENDER_GIT_COMMIT (Render la expone en runtime).
_raw_version = os.environ.get("APP_VERSION") or os.environ.get("RENDER_GIT_COMMIT") or ""
APP_VERSION = (_raw_version[:7] if _raw_version else "")


# ------------------------------
# EMAIL / NOTIFICACIONES
# ------------------------------

def _mail_enabled() -> bool:
    return os.environ.get("MAIL_ENABLED", "false").lower() in ("true", "1", "yes", "on")


def send_email(to_addresses, subject: str, body: str) -> bool:
    """Envía un correo simple de texto plano. Retorna True si se envió."""
    if not _mail_enabled():
        return False

    server = os.environ.get("MAIL_SERVER")
    username = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")
    port = int(os.environ.get("MAIL_PORT", "587"))
    use_tls = os.environ.get("MAIL_USE_TLS", "true").lower() in ("true", "1", "yes", "on")
    use_ssl = os.environ.get("MAIL_USE_SSL", "false").lower() in ("true", "1", "yes", "on")
    sender = os.environ.get("MAIL_FROM") or username

    if not server or not username or not password or not sender:
        print("[WARN] Email no configurado (MAIL_SERVER/USERNAME/PASSWORD/FROM faltan).")
        return False

    if isinstance(to_addresses, str):
        recipients = [to_addresses]
    else:
        recipients = [addr for addr in (to_addresses or []) if addr]

    if not recipients:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject or ""
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body or "")

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(server, port, timeout=10) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port, timeout=10) as smtp:
                if use_tls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                smtp.login(username, password)
                smtp.send_message(msg)
        return True
    except Exception as exc:
        print(f"[WARN] No se pudo enviar email: {exc}")
        return False

# -------------------------------------------------
# Notificaciones internas
# -------------------------------------------------


def _collect_emails(*lists):
    emails = []
    for lst in lists:
        if not lst:
            continue
        if isinstance(lst, str):
            parts = [lst]
        else:
            parts = lst
        for item in parts:
            if not item:
                continue
            cleaned = str(item).strip()
            if cleaned and cleaned not in emails:
                emails.append(cleaned)
    return emails


def notify_control_reminder(cr: "ControlReminder", patient: "Patient"):
    try:
        to_emails = []
        if patient and patient.email:
            to_emails.append(patient.email)
        extra_list = []
        if cr.extra_emails:
            extra_list = [e.strip() for e in cr.extra_emails.split(",") if e.strip()]
        creator_email = cr.created_by.email if cr.created_by and cr.created_by.email else None
        to_emails = _collect_emails(to_emails, extra_list, creator_email)
        if not to_emails:
            return

        patient_name = patient.full_name if patient else "Paciente"
        subject = f"Control médico - {patient_name}"
        lines = [
            f"Se solicitó un control para el paciente: {patient_name}",
            f"Fecha de control: {cr.control_date or 'sin fecha'}",
        ]
        if cr.consultation_id:
            lines.append(f"Consulta ID: {cr.consultation_id}")
        body = "\n".join(lines)
        send_email(to_emails, subject, body)
    except Exception as exc:
        print(f"[WARN] No se pudo notificar control: {exc}")


def notify_control_creation(cr: "ControlReminder", patient: "Patient"):
    """Notificación cuando se CREA el control"""
    try:
        to_emails = []
        if patient and patient.email:
            to_emails.append(patient.email)
        extra_list = []
        if cr.extra_emails:
            extra_list = [e.strip() for e in cr.extra_emails.split(",") if e.strip()]
        creator_email = cr.created_by.email if cr.created_by and cr.created_by.email else None
        to_emails = _collect_emails(to_emails, extra_list, creator_email)
        if not to_emails:
            return

        patient_name = patient.full_name if patient else "Paciente"
        subject = f"Control registrado - {patient_name}"
        lines = [
            f"Se ha creado un control con neumonología para el día {cr.control_date or 'sin asignar'}.",
        ]
        body = "\n".join(lines)
        send_email(to_emails, subject, body)
    except Exception as exc:
        print(f"[WARN] No se pudo notificar creación de control: {exc}")


def notify_control_reminder(cr: "ControlReminder", patient: "Patient"):
    """Notificación el DÍA del control (recordatorio)"""
    try:
        to_emails = []
        if patient and patient.email:
            to_emails.append(patient.email)
        extra_list = []
        if cr.extra_emails:
            extra_list = [e.strip() for e in cr.extra_emails.split(",") if e.strip()]
        creator_email = cr.created_by.email if cr.created_by and cr.created_by.email else None
        to_emails = _collect_emails(to_emails, extra_list, creator_email)
        if not to_emails:
            return

        patient_name = patient.full_name if patient else "Paciente"
        subject = f"Recordatorio - Control médico {cr.control_date or 'sin fecha'}"
        lines = [
            f"RECORDATORIO DE CONTROL CON NEUMONOLOGÍA PARA EL DÍA {cr.control_date or 'SIN FECHA'}, POR FAVOR PONERSE EN CONTACTO CON SU MÉDICO.",
        ]
        body = "\n".join(lines)
        send_email(to_emails, subject, body)
    except Exception as exc:
        print(f"[WARN] No se pudo notificar recordatorio de control: {exc}")


def notify_screening_creation(fu: "ScreeningFollowup"):
    """Notificación cuando se CREA el screening followup"""
    try:
        sc = fu.screening
        patient = sc.patient if sc else None
        to_emails = []
        if patient and patient.email:
            to_emails.append(patient.email)
        extra_email = sc.extra_email if sc else None
        creator_email = fu.created_by.email if fu.created_by and fu.created_by.email else None
        to_emails = _collect_emails(to_emails, extra_email, creator_email)
        if not to_emails:
            return

        patient_name = patient.full_name if patient else "Paciente"
        subject = f"Control de screening registrado - {patient_name}"
        lines = [
            f"Se ha creado un control con neumonología para el día {fu.study_date or 'sin asignar'}.",
        ]
        body = "\n".join(lines)
        send_email(to_emails, subject, body)
    except Exception as exc:
        print(f"[WARN] No se pudo notificar creación screening: {exc}")


def notify_screening_followup(fu: "ScreeningFollowup"):
    """Notificación el DÍA del screening (recordatorio)"""
    try:
        sc = fu.screening
        patient = sc.patient if sc else None
        to_emails = []
        if patient and patient.email:
            to_emails.append(patient.email)
        extra_email = sc.extra_email if sc else None
        creator_email = fu.created_by.email if fu.created_by and fu.created_by.email else None
        to_emails = _collect_emails(to_emails, extra_email, creator_email)
        if not to_emails:
            return

        patient_name = patient.full_name if patient else "Paciente"
        subject = f"Recordatorio - Estudio de screening {fu.study_date or 'sin fecha'}"
        lines = [
            f"RECORDATORIO DE CONTROL CON NEUMONOLOGÍA PARA EL DÍA {fu.study_date or 'SIN FECHA'}, POR FAVOR PONERSE EN CONTACTO CON SU MÉDICO.",
        ]
        body = "\n".join(lines)
        send_email(to_emails, subject, body)
    except Exception as exc:
        print(f"[WARN] No se pudo notificar recordatorio screening: {exc}")

DEFAULT_CATALOGS = {
    "centers": [
        "Sanatorio Cruz Azul",
        "Clinica de Especialidades",
        "Hospital Regional Pasteur",
        "Clinica San Martin",
        "Sanatorio de la Canada",
        "Roentgen",
    ],
    "respiratory_conditions": [
        {"value": "asma", "label": "Asma"},
        {"value": "epoc", "label": "EPOC"},
        {"value": "tb_previa", "label": "TB previa"},
        {"value": "neumonias_repeticion", "label": "Neumonias de repeticion"},
        {"value": "neumotorax", "label": "Neumotorax"},
        {"value": "sahos", "label": "SAHOS"},
        {"value": "hta", "label": "Hipertension arterial"},
        {"value": "coronaria", "label": "Cardiopatia coronaria"},
        {"value": "icc", "label": "Insuficiencia cardiaca"},
        {"value": "diabetes", "label": "Diabetes"},
        {"value": "erge", "label": "ERGE o hernia hiatal"},
    ],
    "autoimmune_conditions": [
        {"value": "artritis_reumatoidea", "label": "Artritis reumatoidea"},
        {"value": "sjogren", "label": "Sjogren"},
        {"value": "esclerodermia", "label": "Esclerodermia"},
        {"value": "dermatomiositis_polimiositis", "label": "Dermato/Polimiositis"},
        {"value": "les", "label": "LES"},
        {"value": "hipogammaglobulinemia", "label": "Hipogammaglobulinemia"},
    ],
    "systemic_symptoms": [
        {"value": "poliartralgias", "label": "Poliartralgias"},
        {"value": "artritis", "label": "Artritis"},
        {"value": "edema_manos", "label": "Edema en manos"},
        {"value": "rigidez_matinal", "label": "Rigidez matinal >30 min"},
        {"value": "fotosensibilidad", "label": "Fotosensibilidad"},
        {"value": "aranas_vasculares", "label": "Aranas vasculares en manos"},
        {"value": "telangiectasias", "label": "Telangiectasias"},
        {"value": "xerostomia", "label": "Xerostomia"},
        {"value": "xeroftalmia", "label": "Xeroftalmia"},
        {"value": "ulceras_orales", "label": "Ulceras orales"},
        {"value": "alopecia", "label": "Alopecia"},
        {"value": "debilidad_muscular", "label": "Debilidad muscular"},
        {"value": "fenomeno_raynaud", "label": "Fenomeno de Raynaud"},
        {"value": "mano_mecanico", "label": "Mano de mecanico"},
        {"value": "gottron", "label": "Papulas de Gottron"},
        {"value": "esclerosis_limitada", "label": "Esclerosis limitada"},
        {"value": "esclerosis_difusa", "label": "Esclerosis difusa"},
        {"value": "perdida_peso", "label": "Perdida de peso"},
    ],
    "occupational_exposures": [
        {"value": "humos", "label": "Humos"},
        {"value": "vapores", "label": "Vapores"},
        {"value": "polvo", "label": "Polvo"},
        {"value": "quimicos", "label": "Quimicos"},
    ],
    "occupational_jobs": [
        {"value": "enarenador", "label": "Enarenador"},
        {"value": "construccion", "label": "Construccion"},
        {"value": "plomeria", "label": "Plomeria"},
        {"value": "mantenimiento", "label": "Mantenimiento"},
        {"value": "carreteras", "label": "Carreteras"},
        {"value": "aislacion", "label": "Aislacion"},
        {"value": "demolicion", "label": "Demolicion"},
        {"value": "pulido", "label": "Pulido"},
        {"value": "fundicion", "label": "Fundicion"},
        {"value": "ceramica", "label": "Ceramica"},
        {"value": "metalurgica", "label": "Metalurgica"},
        {"value": "soldador", "label": "Soldador"},
        {"value": "baterias", "label": "Baterias"},
        {"value": "textil", "label": "Textil"},
        {"value": "algodon", "label": "Algodon"},
        {"value": "carpinteria_madera", "label": "Carpinteria de madera"},
        {"value": "carpinteria_metalica", "label": "Carpinteria metalica"},
        {"value": "plasticos", "label": "Plasticos"},
        {"value": "pintura", "label": "Pintura"},
        {"value": "goma_espuma", "label": "Goma espuma"},
        {"value": "isocianatos", "label": "Isocianatos"},
        {"value": "solventes", "label": "Solventes"},
        {"value": "quesos", "label": "Quesos"},
        {"value": "malta_cebada", "label": "Malta/Cebada"},
        {"value": "talco", "label": "Talco"},
        {"value": "granos", "label": "Granos"},
        {"value": "aves", "label": "Aves"},
        {"value": "animales_corral", "label": "Animales de corral"},
        {"value": "aluminio", "label": "Aluminio"},
        {"value": "limpieza_casas", "label": "Limpieza de casas"},
        {"value": "papel", "label": "Papel"},
        {"value": "cemento", "label": "Cemento"},
        {"value": "jardineria_compost", "label": "Jardineria/compost"},
        {"value": "hongos_champignones", "label": "Hongos o champignones"},
        {"value": "corcho", "label": "Corcho"},
        {"value": "peleteria", "label": "Peleteria"},
    ],
    "domestic_exposures": [
        {"value": "aves_mascotas", "label": "Aves o mascotas"},
        {"value": "palomas", "label": "Palomas"},
        {"value": "plumas", "label": "Plumas (almohada o edredon)"},
        {"value": "ac_central", "label": "Aire/ac central o humidificador"},
        {"value": "casa_antigua", "label": "Casa antigua"},
        {"value": "dano_humedad", "label": "Dano por humedad"},
        {"value": "lavaplatos", "label": "Lavaplatos con perdidas"},
        {"value": "jacuzzi", "label": "Jacuzzi o hidromasaje"},
        {"value": "hongos_roperos", "label": "Hongos en roperos"},
        {"value": "vecinos_aves", "label": "Vecinos con aves"},
    ],
    "illicit_drugs": [
        {"value": "marihuana", "label": "Fumo marihuana"},
        {"value": "cocaina_paco", "label": "Cocaina/Paco"},
        {"value": "endovenosa", "label": "Drogas endovenosas"},
    ],
    "pneumotoxic_drugs": [
        {"value": "azatioprina", "label": "Azatioprina"},
        {"value": "mtx", "label": "Metotrexato"},
        {"value": "sales_oro", "label": "Sales de oro"},
        {"value": "ciclofosfamida", "label": "Ciclofosfamida"},
        {"value": "bleomicina", "label": "Bleomicina"},
        {"value": "amiodarona", "label": "Amiodarona"},
        {"value": "hidralazina", "label": "Hidralazina"},
        {"value": "nitrofurantoina", "label": "Nitrofurantoina"},
    ],
}


def load_catalogs():
    data = json.loads(json.dumps(DEFAULT_CATALOGS))
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
                for key, default in DEFAULT_CATALOGS.items():
                    if isinstance(user_data.get(key), list):
                        data[key] = user_data[key]
        except Exception as exc:
            print(f"[WARN] No se pudo cargar catalogs.json: {exc}")
    return data


CATALOGS = load_catalogs()
# Ensure default upload dir exists; note that tests may override app.config['UPLOAD_DIR'] later
os.makedirs(UPLOAD_DIR, exist_ok=True)


def log_action(action: str, details: dict | None = None, user=None) -> None:
    entry = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "action": action,
        "details": details or {},
    }
    actor = None
    if user:
        actor = {"id": user.id, "name": user.full_name}
    elif current_user and current_user.is_authenticated:
        actor = {"id": current_user.id, "name": current_user.full_name}
    if actor:
        entry["actor"] = actor
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception as exc:
        print(f"[WARN] No se pudo escribir audit log: {exc}")


def password_is_strong(password: str) -> bool:
    if not password or len(password) < 10:
        return False
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(not c.isalnum() for c in password)
    return has_upper and has_lower and has_digit and has_symbol


# -------------------------------------------------
# CONFIGURACION BASICA DE LA APP
# -------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "cambia-esta-clave-por-una-larga-y-segura"
)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # Forzar recarga de templates
app.jinja_env.auto_reload = True
app.jinja_env.cache = {}


def get_upload_dir():
    """Devuelve el directorio de uploads, permitiendo override mediante app.config['UPLOAD_DIR']."""
    return app.config.get("UPLOAD_DIR", UPLOAD_DIR)


UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET")
_GCS_BUCKET = None


def _use_gcs() -> bool:
    return bool(UPLOAD_BUCKET)


def _get_gcs_bucket():
    global _GCS_BUCKET
    if _GCS_BUCKET is None:
        from google.cloud import storage
        client = storage.Client()
        _GCS_BUCKET = client.bucket(UPLOAD_BUCKET)
    return _GCS_BUCKET


def upload_exists(filename: str) -> bool:
    if not filename:
        return False
    if _use_gcs():
        blob = _get_gcs_bucket().blob(filename)
        return blob.exists()
    return os.path.exists(os.path.join(get_upload_dir(), filename))


def save_upload(file_storage, filename: str) -> None:
    if _use_gcs():
        blob = _get_gcs_bucket().blob(filename)
        if hasattr(file_storage, "stream"):
            file_storage.stream.seek(0)
            blob.upload_from_file(
                file_storage.stream,
                content_type=getattr(file_storage, "mimetype", None),
            )
        else:
            blob.upload_from_string(
                file_storage, content_type="application/octet-stream"
            )
        return
    os.makedirs(get_upload_dir(), exist_ok=True)
    file_storage.save(os.path.join(get_upload_dir(), filename))


def delete_upload(filename: str) -> None:
    if not filename:
        return
    if _use_gcs():
        blob = _get_gcs_bucket().blob(filename)
        try:
            blob.delete()
        except Exception:
            pass
        return
    path = os.path.join(get_upload_dir(), filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def send_upload_file(filename: str, as_attachment: bool = True):
    if _use_gcs():
        blob = _get_gcs_bucket().blob(filename)
        if not blob.exists():
            return None
        data = blob.download_as_bytes()
        content_type = blob.content_type or "application/octet-stream"
        return send_file(
            BytesIO(data),
            mimetype=content_type,
            as_attachment=as_attachment,
            download_name=filename,
        )
    return send_from_directory(get_upload_dir(), filename, as_attachment=as_attachment)
default_sqlite = "sqlite:///" + os.path.join(BASE_DIR, "instance", "comite.db")
# TEMPORAL: PostgreSQL en Render está caída, usar SQLite
# app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", default_sqlite)
cloudsql_conn = os.environ.get("CLOUD_SQL_CONNECTION_NAME")
db_user = os.environ.get("DB_USER")
db_pass = os.environ.get("DB_PASS")
db_name = os.environ.get("DB_NAME")

if cloudsql_conn and db_user and db_pass and db_name:
    from google.cloud.sql.connector import Connector

    _cloudsql_connector = Connector()

    def _getconn():
        return _cloudsql_connector.connect(
            cloudsql_conn,
            "pg8000",
            user=db_user,
            password=db_pass,
            db=db_name,
        )

    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+pg8000://"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"creator": _getconn}
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", default_sqlite
    )
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)
csrf = CSRFProtect(app)


# ----- SCHEDULER PARA RECORDATORIOS AUTOMÁTICOS -----
scheduler = BackgroundScheduler()

def send_daily_reminders():
    """Envía recordatorios de controles y screening cuya fecha es HOY"""
    with app.app_context():
        today = datetime.date.today()
        
        # Buscar ControlReminders con fecha = hoy
        try:
            from models import ControlReminder, Patient
            controls_today = ControlReminder.query.filter(
                ControlReminder.control_date == today
            ).all()
            for cr in controls_today:
                patient = cr.patient
                if patient:
                    notify_control_reminder(cr, patient)
            print(f"[INFO] Se enviaron {len(controls_today)} recordatorios de controles para hoy")
        except Exception as e:
            print(f"[WARN] Error enviando recordatorios de controles: {e}")
        
        # Buscar ScreeningFollowups con fecha = hoy
        try:
            from models import ScreeningFollowup
            screenings_today = ScreeningFollowup.query.filter(
                ScreeningFollowup.study_date == today
            ).all()
            for fu in screenings_today:
                notify_screening_followup(fu)
            print(f"[INFO] Se enviaron {len(screenings_today)} recordatorios de screening para hoy")
        except Exception as e:
            print(f"[WARN] Error enviando recordatorios de screening: {e}")

# Registrar la tarea para ejecutarse a las 8:00 AM todos los días
scheduler.add_job(
    send_daily_reminders,
    trigger=CronTrigger(hour=8, minute=0),
    id='daily_reminders',
    name='Recordatorios diarios de controles',
    replace_existing=True
)


MMRC_OPTIONS = [0, 1, 2, 3, 4]
CENTER_PORTAL_LINKS_RAW = {
    "Roentgen": "https://estudios.roentgen.com.ar:4432/request-report/",
    "Sanatorio Cruz Azul": "https://cruzazul.informemedico.com.ar/mis_estudios/",
    "Sanatorio de la Canada": "https://pacientes.sdlc.com.ar/",
    "Clinica San Martin": "https://clinicasanmartin.com.ar/estudios/",
}
CENTER_PORTAL_LINKS = {name.lower(): url for name, url in CENTER_PORTAL_LINKS_RAW.items()}
STUDY_TYPE_OPTIONS = [
    "TC torax",
    "TC torax + abdomen/pelvis",
    "RM torax",
    "PET-CT",
    "Rx torax",
    "Ecografia",
    "Espirometria",
    "Ecocardiograma",
    "Ecodoppler Angiopower",
    "DLCO",
    "Test de la marcha 6m",
    "Biopsia",
    "Otro",
]
DOMESTIC_LABELS = {
    "aves_mascotas": "Aves de ornato/mascotas",
    "palomas": "Palomas",
    "plumas": "Edredon/almohada de plumas",
    "ac_central": "Aire acondicionado central / humidificador",
    "casa_antigua": "Casa antigua",
    "dano_humedad": "Dano por humedad en paredes/techo",
    "lavaplatos": "Lavaplatos pierde agua",
    "jacuzzi": "Jacuzzi / hidromasaje",
    "hongos_roperos": "Hongos en roperos",
    "vecinos_aves": "Vecinos con aves",
}
LABORAL_LABELS = {
    "granos": "Henos/Granos/Paja",
    "malta_cebada": "Trabajador de malta/cervecero",
    "hongos_champignones": "Criadero de hongos/champignones",
    "carpinteria_madera": "Maderas/Aserrin/Carpintero",
    "jardineria_compost": "Trabajos de jardineria/compost",
    "animales_corral": "Criadero de animales (caballos/vacas)",
    "quesos": "Quesos/embutidos",
    "corcho": "Industria del corcho",
    "peleteria": "Peletero/trabajo con pieles",
    "goma_espuma": "Espumas de poliuretano",
    "pintura": "Pinturas (spray)",
    "plasticos": "Plastico/pegamentos/isocianatos",
}
IMMUNO_LAB_CORE_OPTIONS = [
    ("fan_hep2_1", "FAN Hep 2 (1ra muestra)"),
    ("fan_hep2_2", "FAN Hep 2 (2da muestra)"),
    ("fr_1", "Factor reumatoide cuantitativo (1ra muestra)"),
    ("fr_2", "Factor reumatoide cuantitativo (2da muestra)"),
    ("anti_ccp", "Anti CCP"),
    ("anti_ro_total", "Anti Ro total"),
    ("anti_ro_52", "Anti Ro 52 kD"),
    ("anti_ro_60", "Anti Ro 60 kD"),
    ("anti_la", "Anti La"),
    ("anti_rnp", "Anti RNP"),
    ("anti_scl70", "Anti Scl 70"),
    ("anti_centromero", "Anti centromero"),
    ("anti_jo1", "Anti Jo 1"),
    ("anca", "ANCA (sin especificar)"),
    ("anca_c", "ANCA C"),
    ("anca_p", "ANCA P"),
    ("pcr", "PCR cualitativa"),
    ("pcr_cuant", "PCR cuantitativa"),
    ("vsg", "VSG"),
    ("cpk", "CPK"),
    ("aldolasa", "Aldolasa"),
]

# Panel ampliado (reumatología). Agregar ítems acá.
IMMUNO_LAB_RHEUM_OPTIONS: list[tuple[str, str]] = [
    ("anti_pl7", "Anti-PL-7"),
    ("anti_pl12", "Anti-PL-12"),
    ("anti_ej", "Anti-EJ"),
    ("anti_oj", "Anti-OJ"),
    ("anti_srp", "Anti-SRP"),
    ("anti_mi2", "Anti-Mi-2"),
    ("anti_mda5", "Anti-MDA5"),
    ("anti_tif1g", "Anti-TIF1γ"),
    ("anti_nxp2", "Anti-NXP2"),
    ("anti_rna_pol_iii", "Anti-RNA polimerasa III"),
    ("anti_pmscl", "Anti-PM/Scl"),
    ("anti_u3_rnp_fibrilarina", "Anti-U3 RNP (fibrilarina)"),
    ("anti_ku", "Anti-Ku"),
    ("anti_th_to", "Anti-Th/To"),
    ("c3", "Complemento C3"),
    ("c4", "Complemento C4"),
    ("c1q", "Complemento C1q"),
    ("ch50", "Complemento CH50"),
    ("antifosfolipidos", "Antifosfolípidos"),
    ("anti_pr3", "Anti PR3"),
    ("anti_mpo", "Anti MPO"),
]

# Compatibilidad: lista total
IMMUNO_LAB_OPTIONS = IMMUNO_LAB_CORE_OPTIONS + IMMUNO_LAB_RHEUM_OPTIONS
IMMUNO_LAB_DICT = {value: label for value, label in IMMUNO_LAB_OPTIONS}


# -------------------------------------------------
# HELPER: filtro nl2br para mostrar saltos de linea
# -------------------------------------------------


def nl2br(value):
    """Convierte saltos de linea en <br> y marca el resultado como seguro HTML."""
    if not value:
        return ""
    return Markup("<br>".join(str(value).splitlines()))


# Registrar el filtro para usarlo en los templates
app.jinja_env.filters["nl2br"] = nl2br


def _get_catalog_pairs(key: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for item in CATALOGS.get(key, []):
        if isinstance(item, dict):
            value = item.get("value")
            label = item.get("label", value)
            if value:
                result.append((value, label or value))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append((item[0], item[1]))
        elif isinstance(item, str):
            result.append((item, item))
    return result


def _get_catalog_values(key: str) -> list[str]:
    values = []
    for item in CATALOGS.get(key, []):
        if isinstance(item, dict):
            if item.get("value"):
                values.append(item["value"])
        elif isinstance(item, str):
            values.append(item)
        elif isinstance(item, (list, tuple)) and item:
            values.append(item[0])
    return values


def _serialize_list(values):
    if not values:
        return None
    cleaned = [v for v in values if v]
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=True)


def _deserialize_list(value):
    if not value:
        return []
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return [item for item in str(value).split(",") if item]


def _deserialize_kv(value) -> dict[str, str]:
    """Convierte una lista serializada tipo ['k:v'] en dict {k: v}."""
    result: dict[str, str] = {}
    for item in _deserialize_list(value):
        if not item:
            continue
        if ":" in item:
            key, val = item.split(":", 1)
            key = (key or "").strip()
            val = (val or "").strip()
            if key:
                result[key] = val
    return result


def _serialize_kv(values: dict[str, str] | None) -> str | None:
    if not values:
        return None
    items = [f"{k}:{v}" for k, v in values.items() if k and v is not None and str(v).strip()]
    return _serialize_list(items)


def _checkbox_to_bool(value: str | None) -> bool | None:
    if value is None:
        return False
    return value.lower() in {"on", "true", "1", "yes"}


def get_pending_reviews_count_for_user(user) -> int:
    """Cantidad de revisiones pendientes visibles para el usuario."""
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    uid = str(user.id)
    pending = ReviewRequest.query.filter(ReviewRequest.status == "pending").all()
    count = 0
    for rr in pending:
        recipients = _deserialize_list(rr.recipients)
        if uid in recipients or rr.created_by_id == user.id:
            count += 1
    return count


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _compute_bmi(weight_kg: float | None, height_cm: float | None) -> float | None:
    if not weight_kg or not height_cm:
        return None
    height_m = height_cm / 100
    if height_m <= 0:
        return None
    return round(weight_kg / (height_m**2), 2)


def _compute_age_from_birthdate(birth_date_str: str | None) -> int | None:
    if not birth_date_str:
        return None
    try:
        birth_date = datetime.datetime.strptime(birth_date_str, "%Y-%m-%d").date()
        today = datetime.date.today()
        age = today.year - birth_date.year
        if (today.month, today.day) < (birth_date.month, birth_date.day):
            age -= 1
        return age if age >= 0 else None
    except ValueError:
        return None


def patient_form_options():
    return {
        "respiratory_options": _get_catalog_pairs("respiratory_conditions"),
        "autoimmune_options": _get_catalog_pairs("autoimmune_conditions"),
        "symptom_options": _get_catalog_pairs("systemic_symptoms"),
        "occupational_exposure_options": _get_catalog_pairs("occupational_exposures"),
        "occupational_job_options": _get_catalog_pairs("occupational_jobs"),
        "domestic_exposure_options": _get_catalog_pairs("domestic_exposures"),
        "illicit_drug_options": _get_catalog_pairs("illicit_drugs"),
        "pneumotoxic_drug_options": _get_catalog_pairs("pneumotoxic_drugs"),
        "center_options": CATALOGS.get("centers", []),
        "mmrc_options": MMRC_OPTIONS,
        "review_recipients": User.query.filter(
            User.status == "approved"
        ).order_by(User.full_name.asc()).all(),
        "immuno_rows": [
            IMMUNO_LAB_OPTIONS[i : i + 2]
            for i in range(0, len(IMMUNO_LAB_OPTIONS), 2)
        ],
    }


def get_review_recipient_options(exclude_user=None):
    query = User.query.filter(User.status == "approved")
    if exclude_user:
        query = query.filter(User.id != exclude_user.id)
    return query.order_by(User.full_name.asc()).all()


PATIENT_EXTRA_COLUMNS = {
    "consent_given": "BOOLEAN",
    "consent_date": "TEXT",
    "created_at": "TEXT",
    "updated_at": "TEXT",
    "updated_by_id": "INTEGER",
    "email": "TEXT",
    "smoking_current": "BOOLEAN",
    "smoking_previous": "BOOLEAN",
    "smoking_start_age": "INTEGER",
    "smoking_end_age": "INTEGER",
    "smoking_cigarettes_per_day": "INTEGER",
    "smoking_years": "FLOAT",
    "smoking_pack_years": "FLOAT",
    "respiratory_conditions": "TEXT",
    "autoimmune_conditions": "TEXT",
    "autoimmune_other": "TEXT",
    "systemic_symptoms": "TEXT",
    "occupational_exposure_types": "TEXT",
    "occupational_accident": "BOOLEAN",
    "occupational_accident_when": "TEXT",
    "occupational_leave_due_to_breathing": "BOOLEAN",
    "occupational_jobs": "TEXT",
    "occupational_years": "TEXT",
    "domestic_exposures": "TEXT",
    "domestic_exposures_details": "TEXT",
    "drug_use": "TEXT",
    "current_medications": "TEXT",
    "previous_medications": "TEXT",
    "pneumotoxic_drugs": "TEXT",
    "family_history_father": "TEXT",
    "family_history_mother": "TEXT",
    "family_history_siblings": "TEXT",
    "family_history_children": "TEXT",
    "family_genogram_pdf": "TEXT",
    "symptom_cough": "BOOLEAN",
    "symptom_mmrc": "INTEGER",
    "symptom_duration_months": "INTEGER",
    "weight_kg": "FLOAT",
    "height_cm": "FLOAT",
    "bmi": "FLOAT",
    "physical_crepitaciones_velcro": "BOOLEAN",
    "physical_crepitaciones": "BOOLEAN",
    "physical_roncus": "BOOLEAN",
    "physical_wheezing": "BOOLEAN",
    "physical_clubbing": "BOOLEAN",
    "physical_pulmonary_hypertension_signs": "BOOLEAN",
    "diagnoses": "TEXT",
    "notes_personal": "TEXT",
    "notes_smoking": "TEXT",
    "notes_autoimmune": "TEXT",
    "notes_systemic": "TEXT",
    "notes_exposures": "TEXT",
    "notes_family_history": "TEXT",
    "notes_respiratory_exam": "TEXT",
}


def _is_sqlite_engine() -> bool:
    try:
        return db.engine.url.drivername.startswith("sqlite")
    except Exception:
        return False





def ensure_consultation_extra_columns():
    if not _is_sqlite_engine():
        return
    try:
        with db.engine.begin() as connection:
            existing = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(consultations)"))
            }
            if "lab_general" not in existing:
                connection.execute(
                    text("ALTER TABLE consultations ADD COLUMN lab_general TEXT")
                )
            if "lab_immunology" not in existing:
                connection.execute(
                    text("ALTER TABLE consultations ADD COLUMN lab_immunology TEXT")
                )
            if "lab_immunology_values" not in existing:
                connection.execute(
                    text("ALTER TABLE consultations ADD COLUMN lab_immunology_values TEXT")
                )
            if "lab_immunology_notes" not in existing:
                connection.execute(
                    text("ALTER TABLE consultations ADD COLUMN lab_immunology_notes TEXT")
                )
    except Exception as exc:
        print(f"[WARN] No se pudo verificar columnas extra de consultas: {exc}")


def populate_patient_from_form(patient, form_data, creator=None):
    now = datetime.datetime.utcnow()
    patient.full_name = (form_data.get("full_name") or "").strip()
    patient.dni = (form_data.get("dni") or "").strip() or None
    patient.age = _to_int(form_data.get("age"))
    patient.sex = (form_data.get("sex") or "").strip() or None
    patient.center = (form_data.get("center") or "").strip() or None
    patient.email = (form_data.get("email") or "").strip() or None
    patient.birth_date = (form_data.get("birth_date") or "").strip() or None
    if patient.age is None and patient.birth_date:
        patient.age = _compute_age_from_birthdate(patient.birth_date)
    patient.phone = (form_data.get("phone") or "").strip() or None
    patient.address = (form_data.get("address") or "").strip() or None
    patient.city = (form_data.get("city") or "").strip() or None
    patient.health_insurance = (
        (form_data.get("health_insurance") or "").strip() or None
    )
    patient.health_insurance_number = (
        (form_data.get("health_insurance_number") or "").strip() or None
    )
    patient.first_consultation_date = (
        (form_data.get("first_consultation_date") or "").strip() or None
    )
    patient.consent_given = _checkbox_to_bool(form_data.get("consent_given"))
    if patient.consent_given:
        date_value = (form_data.get("consent_date") or "").strip()
        if not date_value:
            date_value = datetime.date.today().isoformat()
        patient.consent_date = date_value
    else:
        patient.consent_date = None
    patient.antecedentes = (form_data.get("antecedentes") or "").strip() or None
    patient.diagnoses = (form_data.get("diagnoses") or "").strip() or None
    patient.notes_personal = (form_data.get("notes_personal") or "").strip() or None

    # Smoking
    smoking_never = _checkbox_to_bool(form_data.get("smoking_never"))
    patient.smoking_never = smoking_never
    patient.smoking_current = _checkbox_to_bool(form_data.get("smoking_current"))
    patient.smoking_previous = _checkbox_to_bool(form_data.get("smoking_previous"))
    patient.smoking_start_age = _to_int(form_data.get("smoking_start_age"))
    patient.smoking_end_age = _to_int(form_data.get("smoking_end_age"))
    patient.smoking_cigarettes_per_day = _to_int(
        form_data.get("smoking_cigarettes_per_day")
    )
    patient.smoking_years = _to_float(form_data.get("smoking_years"))
    pack_years_input = _to_float(form_data.get("smoking_pack_years"))
    if smoking_never:
        patient.smoking_current = False
        patient.smoking_previous = False
        patient.smoking_pack_years = 0
        patient.smoking_years = None
        patient.smoking_cigarettes_per_day = None
    else:
        if patient.smoking_cigarettes_per_day and patient.smoking_years:
            cigs = float(patient.smoking_cigarettes_per_day)
            patient.smoking_pack_years = round((cigs / 20.0) * patient.smoking_years, 2)
        else:
            patient.smoking_pack_years = pack_years_input
    patient.notes_smoking = (form_data.get("notes_smoking") or "").strip() or None

    # Lists
    patient.respiratory_conditions = _serialize_list(
        form_data.getlist("respiratory_conditions")
    )
    patient.autoimmune_conditions = _serialize_list(
        form_data.getlist("autoimmune_conditions")
    )
    patient.notes_autoimmune = (form_data.get("notes_autoimmune") or "").strip() or None
    patient.autoimmune_other = (
        (form_data.get("autoimmune_other") or "").strip() or None
    )
    patient.systemic_symptoms = _serialize_list(
        form_data.getlist("systemic_symptoms")
    )
    patient.notes_systemic = (form_data.get("notes_systemic") or "").strip() or None
    patient.occupational_exposure_types = _serialize_list(
        form_data.getlist("occupational_exposure_types")
    )
    patient.occupational_jobs = _serialize_list(
        form_data.getlist("occupational_jobs")
    )
    patient.domestic_exposures = _serialize_list(
        form_data.getlist("domestic_exposures")
    )
    patient.drug_use = _serialize_list(form_data.getlist("drug_use"))
    patient.pneumotoxic_drugs = _serialize_list(
        form_data.getlist("pneumotoxic_drugs")
    )

    patient.occupational_years = (
        (form_data.get("occupational_years") or "").strip() or None
    )
    patient.occupational_accident = _checkbox_to_bool(
        form_data.get("occupational_accident")
    )
    patient.occupational_accident_when = (
        (form_data.get("occupational_accident_when") or "").strip() or None
    )
    patient.occupational_leave_due_to_breathing = _checkbox_to_bool(
        form_data.get("occupational_leave_due_to_breathing")
    )
    patient.domestic_exposures_details = (
        (form_data.get("domestic_exposures_details") or "").strip() or None
    )
    patient.notes_exposures = (form_data.get("notes_exposures") or "").strip() or None
    patient.current_medications = (
        (form_data.get("current_medications") or "").strip() or None
    )
    patient.previous_medications = (
        (form_data.get("previous_medications") or "").strip() or None
    )

    patient.family_history_father = (
        (form_data.get("family_history_father") or "").strip() or None
    )
    patient.family_history_mother = (
        (form_data.get("family_history_mother") or "").strip() or None
    )
    patient.family_history_siblings = (
        (form_data.get("family_history_siblings") or "").strip() or None
    )
    patient.family_history_children = (
        (form_data.get("family_history_children") or "").strip() or None
    )
    patient.notes_family_history = (form_data.get("notes_family_history") or "").strip() or None

    patient.symptom_cough = _checkbox_to_bool(form_data.get("symptom_cough"))
    patient.symptom_mmrc = _to_int(form_data.get("symptom_mmrc"))
    patient.symptom_duration_months = _to_int(
        form_data.get("symptom_duration_months")
    )

    patient.weight_kg = _to_float(form_data.get("weight_kg"))
    patient.height_cm = _to_float(form_data.get("height_cm"))
    patient.bmi = _compute_bmi(patient.weight_kg, patient.height_cm)
    patient.physical_crepitaciones_velcro = _checkbox_to_bool(
        form_data.get("physical_crepitaciones_velcro")
    )
    patient.physical_crepitaciones = _checkbox_to_bool(
        form_data.get("physical_crepitaciones")
    )
    patient.physical_roncus = _checkbox_to_bool(form_data.get("physical_roncus"))
    patient.physical_wheezing = _checkbox_to_bool(
        form_data.get("physical_wheezing")
    )
    patient.physical_clubbing = _checkbox_to_bool(
        form_data.get("physical_clubbing")
    )
    patient.physical_pulmonary_hypertension_signs = _checkbox_to_bool(
        form_data.get("physical_pulmonary_hypertension_signs")
    )
    patient.notes_respiratory_exam = (form_data.get("notes_respiratory_exam") or "").strip() or None

    if not patient.created_at:
        patient.created_at = now
    patient.updated_at = now
    if creator:
        if not patient.created_by:
            patient.created_by = creator
        patient.updated_by = creator


def _parse_recipient_ids(form_field_values):
    recipients = []
    for value in form_field_values:
        try:
            num = int(value)
            if num not in recipients:
                recipients.append(num)
        except (TypeError, ValueError):
            continue
    return recipients


def _get_review_recipient_emails(recipient_ids):
    if not recipient_ids:
        return []
    users = User.query.filter(User.id.in_(recipient_ids)).all()
    return [u.email for u in users if u and u.email]


def _build_review_link():
    try:
        return url_for("reviews_inbox", _external=True)
    except Exception:
        # Si no hay contexto de request o SERVER_NAME, devolvemos cadena vacía.
        return ""


def notify_review_request(rr: "ReviewRequest"):
    """Notifica por email a los destinatarios cuando se crea una revisión."""
    try:
        recipient_ids = [int(x) for x in _deserialize_list(rr.recipients)]
    except Exception:
        recipient_ids = []
    emails = _get_review_recipient_emails(recipient_ids)
    if not emails:
        return

    patient_name = rr.patient.full_name if rr.patient else "Paciente"
    patient_dni = rr.patient.dni if rr.patient and rr.patient.dni else ""
    patient_info = f"{patient_name} - DNI {patient_dni}" if patient_dni else patient_name
    requester = rr.created_by.full_name if rr.created_by else "Otro médico"
    link = _build_review_link()
    lines = [
        f"El doctor {requester} ha creado una revisión de caso para el paciente {patient_info}",
    ]
    if rr.message:
        lines.append("")
        lines.append("Mensaje:")
        lines.append(rr.message)
    if link:
        lines.append("")
        lines.append(f"Revisá la solicitud acá: {link}")
    send_email(emails, f"Nueva revisión de caso - {patient_name}", "\n".join(lines))


def notify_review_comment(review: "ReviewRequest", comment: "ReviewComment"):
    """Notifica por email a destinatarios y creador cuando se agrega un comentario."""
    try:
        recipient_ids = [int(x) for x in _deserialize_list(review.recipients)]
    except Exception:
        recipient_ids = []
    if review.created_by_id:
        recipient_ids.append(review.created_by_id)
    recipient_ids = list({rid for rid in recipient_ids if isinstance(rid, int)})

    emails = _get_review_recipient_emails(recipient_ids)
    if not emails:
        return

    patient_name = review.patient.full_name if review.patient else "Paciente"
    patient_dni = review.patient.dni if review.patient and review.patient.dni else ""
    patient_info = f"{patient_name} - DNI {patient_dni}" if patient_dni else patient_name
    author = comment.author.full_name if comment.author else "Un colega"
    link = _build_review_link()
    lines = [
        f"El doctor {author} ha contestado su revisión de caso del paciente {patient_info}",
        f"Comentario: {comment.message}",
    ]
    if link:
        lines.append("")
        lines.append(f"Ver revisión: {link}")
    send_email(emails, f"Respuesta en revisión de caso - {patient_name}", "\n".join(lines))


def create_review_request(patient, created_by, recipient_ids, message, consultation=None, study=None):
    if not recipient_ids:
        return None
    rr = ReviewRequest(
        patient=patient,
        consultation=consultation,
        study=study,
        created_by=created_by,
        recipients=_serialize_list([str(rid) for rid in recipient_ids]) or "[]",
        message=(message or "").strip() or None,
    )
    db.session.add(rr)
    db.session.flush()
    log_action(
        "review_request_create",
        {
            "patient_id": patient.id,
            "recipients": recipient_ids,
            "review_id": rr.id,
        },
    )
    notify_review_request(rr)
    return rr


def add_review_comment(review, user, message):
    text = (message or "").strip()
    if not text:
        return None
    comment = ReviewComment(review=review, author=user, message=text)
    db.session.add(comment)
    db.session.flush()
    log_action(
        "review_comment_add",
        {
            "review_id": review.id,
            "user_id": user.id,
        },
    )
    notify_review_comment(review, comment)
    return comment


def allowed_study_file(filename):
    if not filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_STUDY_EXTENSIONS


def allowed_patient_file(filename):
    if not filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in ALLOWED_PATIENT_EXTENSIONS


def ensure_study_extra_columns():
    """Asegura columnas extra en studies tanto en SQLite como en Postgres."""
    try:
        if _is_sqlite_engine():
            with db.engine.begin() as connection:
                existing = {row[1] for row in connection.execute(text("PRAGMA table_info(studies)"))}
                if "report_file" not in existing:
                    connection.execute(text("ALTER TABLE studies ADD COLUMN report_file TEXT"))
                if "access_code" not in existing:
                    connection.execute(text("ALTER TABLE studies ADD COLUMN access_code TEXT"))
                if "portal_link" not in existing:
                    connection.execute(text("ALTER TABLE studies ADD COLUMN portal_link TEXT"))
        else:
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE studies ADD COLUMN IF NOT EXISTS report_file TEXT"))
                connection.execute(text("ALTER TABLE studies ADD COLUMN IF NOT EXISTS access_code TEXT"))
                connection.execute(text("ALTER TABLE studies ADD COLUMN IF NOT EXISTS portal_link TEXT"))
    except Exception as exc:
        print(f"[WARN] No se pudo verificar columnas extra de estudios: {exc}")


def ensure_patient_extra_columns():
    """Asegura columnas extra en patients (smoking_never)."""
    try:
        if _is_sqlite_engine():
            with db.engine.begin() as connection:
                existing = {row[1] for row in connection.execute(text("PRAGMA table_info(patients)"))}
                if "smoking_never" not in existing:
                    connection.execute(text("ALTER TABLE patients ADD COLUMN smoking_never BOOLEAN"))
        else:
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE patients ADD COLUMN IF NOT EXISTS smoking_never BOOLEAN"))
    except Exception as exc:
        print(f"[WARN] No se pudo verificar columnas extra de pacientes: {exc}")


def ensure_medical_resource_columns():
    """Asegura columnas extra en medical_resources (sqlite/postgres)."""
    try:
        if _is_sqlite_engine():
            with db.engine.begin() as connection:
                existing = {row[1] for row in connection.execute(text("PRAGMA table_info(medical_resources)"))}
                if "notes" not in existing:
                    connection.execute(text("ALTER TABLE medical_resources ADD COLUMN notes TEXT"))
        else:
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE medical_resources ADD COLUMN IF NOT EXISTS notes TEXT"))
    except Exception as exc:
        print(f"[WARN] No se pudo verificar columnas de medical_resources: {exc}")


def ensure_screening_extra_columns():
    """Asegura columnas extra en screenings/followups/control_reminders (sqlite/postgres)."""
    try:
        if _is_sqlite_engine():
            with db.engine.begin() as connection:
                existing = {row[1] for row in connection.execute(text("PRAGMA table_info(screenings)"))}
                if "study_file" not in existing:
                    connection.execute(text("ALTER TABLE screenings ADD COLUMN study_file TEXT"))
                if "screening_lung" not in existing:
                    connection.execute(text("ALTER TABLE screenings ADD COLUMN screening_lung BOOLEAN"))
                if "followup_nodule" not in existing:
                    connection.execute(text("ALTER TABLE screenings ADD COLUMN followup_nodule BOOLEAN"))
                if "ecog_status" not in existing:
                    connection.execute(text("ALTER TABLE screenings ADD COLUMN ecog_status TEXT"))
                if "extra_email" not in existing:
                    connection.execute(text("ALTER TABLE screenings ADD COLUMN extra_email TEXT"))
            with db.engine.begin() as connection:
                existing_fu = {row[1] for row in connection.execute(text("PRAGMA table_info(screening_followups)"))}
                if "created_by_id" not in existing_fu:
                    connection.execute(text("ALTER TABLE screening_followups ADD COLUMN created_by_id INTEGER"))
                if "completed" not in existing_fu:
                    connection.execute(text("ALTER TABLE screening_followups ADD COLUMN completed BOOLEAN DEFAULT 0"))
                if "completed_at" not in existing_fu:
                    connection.execute(text("ALTER TABLE screening_followups ADD COLUMN completed_at TEXT"))
            with db.engine.begin() as connection:
                existing_cr = {row[1] for row in connection.execute(text("PRAGMA table_info(control_reminders)"))}
                if "control_date" not in existing_cr:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS control_reminders (
                                id INTEGER PRIMARY KEY,
                                patient_id INTEGER NOT NULL,
                                consultation_id INTEGER,
                                control_date TEXT,
                                extra_emails TEXT,
                                created_at TEXT,
                                created_by_id INTEGER
                            )
                            """
                        )
                    )
                else:
                    if "created_by_id" not in existing_cr:
                        connection.execute(text("ALTER TABLE control_reminders ADD COLUMN created_by_id INTEGER"))
                    if "extra_emails" not in existing_cr:
                        connection.execute(text("ALTER TABLE control_reminders ADD COLUMN extra_emails TEXT"))
                    if "completed" not in existing_cr:
                        connection.execute(text("ALTER TABLE control_reminders ADD COLUMN completed BOOLEAN DEFAULT 0"))
                    if "completed_at" not in existing_cr:
                        connection.execute(text("ALTER TABLE control_reminders ADD COLUMN completed_at TEXT"))
        else:
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE screenings ADD COLUMN IF NOT EXISTS study_file TEXT"))
                connection.execute(text("ALTER TABLE screenings ADD COLUMN IF NOT EXISTS screening_lung BOOLEAN"))
                connection.execute(text("ALTER TABLE screenings ADD COLUMN IF NOT EXISTS followup_nodule BOOLEAN"))
                connection.execute(text("ALTER TABLE screenings ADD COLUMN IF NOT EXISTS ecog_status TEXT"))
                connection.execute(text("ALTER TABLE screenings ADD COLUMN IF NOT EXISTS extra_email TEXT"))
                connection.execute(text("ALTER TABLE screening_followups ADD COLUMN IF NOT EXISTS created_by_id INTEGER"))
                connection.execute(text("ALTER TABLE screening_followups ADD COLUMN IF NOT EXISTS completed BOOLEAN DEFAULT 0"))
                connection.execute(text("ALTER TABLE screening_followups ADD COLUMN IF NOT EXISTS completed_at TEXT"))
                connection.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS control_reminders (
                            id SERIAL PRIMARY KEY,
                            patient_id INTEGER NOT NULL,
                            consultation_id INTEGER,
                            control_date TEXT,
                            extra_emails TEXT,
                            created_at TEXT,
                            created_by_id INTEGER,
                            status TEXT,
                            completed BOOLEAN DEFAULT 0,
                            completed_at TEXT
                        )
                        """
                    )
                )
                # columnas extra si ya existía tabla
                connection.execute(text("ALTER TABLE control_reminders ADD COLUMN IF NOT EXISTS extra_emails TEXT"))
                connection.execute(text("ALTER TABLE control_reminders ADD COLUMN IF NOT EXISTS created_by_id INTEGER"))
                connection.execute(text("ALTER TABLE control_reminders ADD COLUMN IF NOT EXISTS status TEXT"))
                connection.execute(text("ALTER TABLE control_reminders ADD COLUMN IF NOT EXISTS completed BOOLEAN DEFAULT 0"))
                connection.execute(text("ALTER TABLE control_reminders ADD COLUMN IF NOT EXISTS completed_at TEXT"))
    except Exception as exc:
        print(f"[WARN] No se pudo verificar columnas de screenings: {exc}")


def build_case_defaults(patient, latest_consultation, latest_study, domestic_flags, laboral_flags):
    intro = []
    intro.append(f"Edad: {patient.age or '___'} años")
    intro.append(f"Sexo: {patient.sex or '___'}")
    motivo = ""
    if latest_consultation and latest_consultation.notes:
        motivo = latest_consultation.notes
    intro.append(f"Motivo de presentación/pregunta: {motivo or '_____________________________'}")
    antecedentes = patient.antecedentes or ""
    intro.append(f"Antecedentes relevantes: {antecedentes}")
    tabaquismo = f"Tabaquismo actual: {'SI' if patient.smoking_current else 'NO'} | Tabaquismo previo: {'SI' if patient.smoking_previous else 'NO'} | IPA: {patient.smoking_pack_years or '___'}"
    intro.append(tabaquismo)
    comorbilidades = patient.clinica_actual or ""
    intro.append(f"Otros antecedentes/comorbilidades: {comorbilidades}")
    medicacion = patient.current_medications or ""
    intro.append(f"Medicacion concomitante: {medicacion}")

    physical = []
    physical.append("Saturación de oxigeno: __________")
    physical.append(
        f"Crepitantes velcro: {'SI' if patient.physical_crepitaciones_velcro else 'NO'} | Clubbing: {'SI' if patient.physical_clubbing else 'NO'} | Signos HTP: {'SI' if patient.physical_pulmonary_hypertension_signs else 'NO'}"
    )
    sistemicos = patient.systemic_symptoms
    if sistemicos:
        physical.append("Signos clínicos autoinmunes: " + ", ".join(_deserialize_list(sistemicos)))
    else:
        physical.append("Signos clínicos autoinmunes: _____________________________")

    resp_tests = "Completar FVC, FEV1, TLC, RV, DLCO, DLCO/VA."
    immunology_parts = []
    if latest_consultation:
        if latest_consultation.lab_general:
            immunology_parts.append(latest_consultation.lab_general)
        lab_sel = _deserialize_list(latest_consultation.lab_immunology)
        if lab_sel:
            labels = [IMMUNO_LAB_DICT.get(code, code) for code in lab_sel]
            immunology_parts.append("Autoinmunidad: " + ", ".join(labels))
    if not immunology_parts:
        immunology_parts.append("Registrar FAN, FR, Anti CCP, ANCA, PCR, VSG, CPK, Aldolasa.")

    exposures_text = []
    home_pos = [label for label, flag in domestic_flags if flag]
    work_pos = [label for label, flag in laboral_flags if flag]
    if home_pos:
        exposures_text.append("Hogar: " + ", ".join(home_pos))
    if work_pos:
        exposures_text.append("Laboral: " + ", ".join(work_pos))
    if patient.domestic_exposures_details:
        exposures_text.append("Detalle adicional: " + patient.domestic_exposures_details)
    if not exposures_text:
        exposures_text.append("Sin exposiciones de riesgo declaradas.")

    imaging_parts = []
    if latest_study:
        imaging_parts.append(
            f"Último estudio: {latest_study.study_type or '---'} - {latest_study.date or '---'} - {latest_study.center or '---'}"
        )
        if latest_study.description:
            imaging_parts.append(latest_study.description)
        portal_url = CENTER_PORTAL_LINKS.get((latest_study.center or "").lower())
        if portal_url:
            imaging_parts.append(f"Portal: {portal_url}")
        if latest_study.access_code:
            imaging_parts.append(f"Número de acceso: {latest_study.access_code}")
    else:
        imaging_parts.append("Sin estudios registrados.")

    return {
        "intro": "\n".join(intro),
        "physical_exam": "\n".join(physical),
        "respiratory_tests": resp_tests,
        "immunology": "\n".join(immunology_parts),
        "exposures": "\n".join(exposures_text),
        "imaging": "\n".join(imaging_parts),
        "notes": "",
    }


@app.template_filter("as_list")
def as_list_filter(value):
    return _deserialize_list(value)


@app.template_filter("yesno")
def yesno_filter(value):
    return "SI" if value else "NO"


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": lambda: generate_csrf()}


@app.context_processor
def inject_app_version():
    """Inyecta la versión de la app para mostrar badges/etiquetas de despliegue."""
    return {"app_version": APP_VERSION}


# -------------------------------------------------
# MODELOS
# -------------------------------------------------


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    specialty = db.Column(db.String(150), nullable=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    # 'admin' o 'medico'
    role = db.Column(db.String(20), default="medico")

    # 'pending', 'approved', 'rejected'
    status = db.Column(db.String(20), default="pending")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Patient(db.Model):
    __tablename__ = "patients"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)

    # Datos personales clasicos + nuevos
    dni = db.Column(db.String(20), unique=True, nullable=True)
    email = db.Column(db.String(255), nullable=True)
    age = db.Column(db.Integer, nullable=True)             # se puede seguir usando si queres
    sex = db.Column(db.String(10), nullable=True)          # 'M', 'F', etc.

    # NUEVOS CAMPOS PARA LA FICHA COMPLETA
    birth_date = db.Column(db.String(10), nullable=True)   # Fecha de nacimiento (YYYY-MM-DD)
    phone = db.Column(db.String(50), nullable=True)        # Telefono
    address = db.Column(db.String(250), nullable=True)     # Domicilio
    city = db.Column(db.String(150), nullable=True)        # Ciudad / Localidad
    health_insurance = db.Column(db.String(150), nullable=True)         # Obra social
    health_insurance_number = db.Column(db.String(100), nullable=True)  # Nro de afiliado / socio
    first_consultation_date = db.Column(db.String(10), nullable=True)   # Primera consulta EPID (YYYY-MM-DD)
    consent_given = db.Column(db.Boolean, default=False)
    consent_date = db.Column(db.String(10), nullable=True)
    notes_personal = db.Column(db.Text, nullable=True)

    # Lugar donde se atiende (lo que ya tenias como center)
    center = db.Column(db.String(150), nullable=True)

    # Tabaquismo
    smoking_never = db.Column(db.Boolean, nullable=True)
    smoking_current = db.Column(db.Boolean, nullable=True)
    smoking_previous = db.Column(db.Boolean, nullable=True)
    smoking_start_age = db.Column(db.Integer, nullable=True)
    smoking_end_age = db.Column(db.Integer, nullable=True)
    smoking_cigarettes_per_day = db.Column(db.Integer, nullable=True)
    smoking_years = db.Column(db.Float, nullable=True)
    smoking_pack_years = db.Column(db.Float, nullable=True)

    # Listas y antecedentes estructurados
    respiratory_conditions = db.Column(db.Text, nullable=True)
    autoimmune_conditions = db.Column(db.Text, nullable=True)
    autoimmune_other = db.Column(db.Text, nullable=True)
    systemic_symptoms = db.Column(db.Text, nullable=True)
    occupational_exposure_types = db.Column(db.Text, nullable=True)
    occupational_accident = db.Column(db.Boolean, nullable=True)
    occupational_accident_when = db.Column(db.String(100), nullable=True)
    occupational_leave_due_to_breathing = db.Column(db.Boolean, nullable=True)
    occupational_jobs = db.Column(db.Text, nullable=True)
    occupational_years = db.Column(db.String(100), nullable=True)
    domestic_exposures = db.Column(db.Text, nullable=True)
    domestic_exposures_details = db.Column(db.Text, nullable=True)
    drug_use = db.Column(db.Text, nullable=True)
    current_medications = db.Column(db.Text, nullable=True)
    previous_medications = db.Column(db.Text, nullable=True)
    pneumotoxic_drugs = db.Column(db.Text, nullable=True)

    # Historia familiar
    family_history_father = db.Column(db.String(250), nullable=True)
    family_history_mother = db.Column(db.String(250), nullable=True)
    family_history_siblings = db.Column(db.String(250), nullable=True)
    family_history_children = db.Column(db.String(250), nullable=True)
    family_genogram_pdf = db.Column(db.String(300), nullable=True)
    notes_family_history = db.Column(db.Text, nullable=True)

    # Sintomas respiratorios y examen fisico
    symptom_cough = db.Column(db.Boolean, nullable=True)
    symptom_mmrc = db.Column(db.Integer, nullable=True)
    symptom_duration_months = db.Column(db.Integer, nullable=True)
    weight_kg = db.Column(db.Float, nullable=True)
    height_cm = db.Column(db.Float, nullable=True)
    bmi = db.Column(db.Float, nullable=True)
    physical_crepitaciones_velcro = db.Column(db.Boolean, nullable=True)
    physical_crepitaciones = db.Column(db.Boolean, nullable=True)
    physical_roncus = db.Column(db.Boolean, nullable=True)
    physical_wheezing = db.Column(db.Boolean, nullable=True)
    physical_clubbing = db.Column(db.Boolean, nullable=True)
    physical_pulmonary_hypertension_signs = db.Column(db.Boolean, nullable=True)
    notes_respiratory_exam = db.Column(db.Text, nullable=True)

    # Antecedentes generales
    antecedentes = db.Column(db.Text, nullable=True)
    diagnoses = db.Column(db.Text, nullable=True)  # Diagnosticos establecidos / probables
    notes_smoking = db.Column(db.Text, nullable=True)
    notes_autoimmune = db.Column(db.Text, nullable=True)
    notes_systemic = db.Column(db.Text, nullable=True)
    notes_exposures = db.Column(db.Text, nullable=True)

    # Campos viejos (para compatibilidad con la base existente)
    clinica_actual = db.Column(db.Text, nullable=True)
    estudios_realizados = db.Column(db.Text, nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.relationship(
        "User", foreign_keys=[created_by_id], backref="patients_created"
    )
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by = db.relationship("User", foreign_keys=[updated_by_id], backref="patients_updated")


class Consultation(db.Model):
    __tablename__ = "consultations"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    date = db.Column(db.String(20), nullable=True)  # YYYY-MM-DD
    notes = db.Column(db.Text, nullable=True)  # clinica de esa consulta
    lab_general = db.Column(db.Text, nullable=True)
    lab_immunology = db.Column(db.Text, nullable=True)
    lab_immunology_values = db.Column(db.Text, nullable=True)
    lab_immunology_notes = db.Column(db.Text, nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.relationship("User")

    patient = db.relationship("Patient", backref="consultations")


class Study(db.Model):
    __tablename__ = "studies"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    consultation_id = db.Column(db.Integer, db.ForeignKey("consultations.id"), nullable=True)

    study_type = db.Column(db.String(150), nullable=True)
    date = db.Column(db.String(20), nullable=True)  # YYYY-MM-DD
    center = db.Column(db.String(150), nullable=True)
    description = db.Column(db.Text, nullable=True)
    access_code = db.Column(db.String(100), nullable=True)
    portal_link = db.Column(db.String(500), nullable=True)
    report_file = db.Column(db.String(300), nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.relationship("User")

    patient = db.relationship("Patient", backref="studies")
    consultation = db.relationship("Consultation", backref="studies")


class ReviewRequest(db.Model):
    __tablename__ = "review_requests"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    consultation_id = db.Column(db.Integer, db.ForeignKey("consultations.id"), nullable=True)
    study_id = db.Column(db.Integer, db.ForeignKey("studies.id"), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    recipients = db.Column(db.Text, nullable=False)
    message = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)

    patient = db.relationship("Patient", backref="review_requests")
    consultation = db.relationship("Consultation", backref="review_requests")
    study = db.relationship("Study", backref="review_requests")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def recipient_ids(self):
        ids = _deserialize_list(self.recipients)
        return [int(i) for i in ids if str(i).isdigit()]


class ReviewComment(db.Model):
    __tablename__ = "review_comments"

    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey("review_requests.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    review = db.relationship("ReviewRequest", backref="comments")
    author = db.relationship("User")


class CasePresentation(db.Model):
    __tablename__ = "case_presentations"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), unique=True)
    intro = db.Column(db.Text, nullable=True)
    physical_exam = db.Column(db.Text, nullable=True)
    respiratory_tests = db.Column(db.Text, nullable=True)
    immunology = db.Column(db.Text, nullable=True)
    exposures = db.Column(db.Text, nullable=True)
    imaging = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    patient = db.relationship(
        "Patient", backref=db.backref("case_presentation", uselist=False)
    )


class Screening(db.Model):
    __tablename__ = "screenings"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)

    screening_lung = db.Column(db.Boolean, default=None)
    followup_nodule = db.Column(db.Boolean, default=None)
    ecog_status = db.Column(db.String(10), nullable=True)
    family_history = db.Column(db.Boolean, default=None)  # antecedentes familiares CA pulmon
    prior_ct = db.Column(db.Boolean, default=None)
    prior_comparison = db.Column(db.Text, nullable=True)
    study_center = db.Column(db.String(200), nullable=True)
    study_number = db.Column(db.String(100), nullable=True)
    study_date = db.Column(db.String(20), nullable=True)
    findings = db.Column(db.Text, nullable=True)  # se deja para compatibilidad, ya no en UI base
    lung_rads = db.Column(db.String(50), nullable=True)
    conclusion = db.Column(db.Text, nullable=True)
    nccn_criteria = db.Column(db.Text, nullable=True)
    next_control_date = db.Column(db.String(20), nullable=True)
    study_file = db.Column(db.String(300), nullable=True)
    extra_email = db.Column(db.String(255), nullable=True)

    patient = db.relationship("Patient", backref=db.backref("screening", uselist=False))


class ScreeningFollowup(db.Model):
    __tablename__ = "screening_followups"

    id = db.Column(db.Integer, primary_key=True)
    screening_id = db.Column(db.Integer, db.ForeignKey("screenings.id"), nullable=False)
    study_type = db.Column(db.String(150), nullable=True)
    study_center = db.Column(db.String(200), nullable=True)
    study_number = db.Column(db.String(100), nullable=True)
    study_date = db.Column(db.String(20), nullable=True)
    findings = db.Column(db.Text, nullable=True)
    lung_rads = db.Column(db.String(50), nullable=True)
    next_control_date = db.Column(db.String(20), nullable=True)
    file_name = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User")
    status = db.Column(db.String(20), default="pending")  # pending, in_progress, done
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    screening = db.relationship("Screening", backref=db.backref("followups", lazy="dynamic"))


class ControlReminder(db.Model):
    __tablename__ = "control_reminders"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)
    consultation_id = db.Column(db.Integer, db.ForeignKey("consultations.id"), nullable=True)
    control_date = db.Column(db.String(20), nullable=True)  # YYYY-MM-DD
    extra_emails = db.Column(db.Text, nullable=True)  # lista separada por coma
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User")
    status = db.Column(db.String(20), default="pending")  # pending, in_progress, done
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    patient = db.relationship("Patient")
    consultation = db.relationship("Consultation")

class MedicalResource(db.Model):
    __tablename__ = "medical_resources"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(500), nullable=True)
    file_name = db.Column(db.String(300), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User")

# -------------------------------------------------
# CONTEXTO GLOBAL (badges, etc.)
# -------------------------------------------------

@app.context_processor
@app.context_processor
def inject_pending_reviews_count():
    """Conteo de revisiones pendientes para mostrar en el men?."""
    try:
        count = get_pending_reviews_count_for_user(current_user)
    except Exception:
        count = 0
    return {"pending_reviews_count": count}

# -------------------------------------------------
# LOGIN MANAGER
# -------------------------------------------------


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# -------------------------------------------------
# BACKUP AUTOMATICO SI CAMBIO LA BASE
# -------------------------------------------------

DB_PATH = os.path.join(BASE_DIR, "instance", "comite.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
BACKUP_HASH_FILE = os.path.join(BACKUP_DIR, "last_hash.txt")


def _compute_db_hash(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_last_hash() -> str | None:
    if not os.path.exists(BACKUP_HASH_FILE):
        return None
    try:
        with open(BACKUP_HASH_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _save_last_hash(h: str) -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(BACKUP_HASH_FILE, "w", encoding="utf-8") as f:
        f.write(h)


def backup_database_if_changed():
    """Crea un backup en /backups solo si la base cambio desde el ultimo hash."""
    if not os.path.exists(DB_PATH):
        print(f"[WARN] No se encontro la base en: {DB_PATH}")
        return

    current_hash = _compute_db_hash(DB_PATH)
    if current_hash is None:
        print("[WARN] No se pudo calcular el hash de la base.")
        return

    last_hash = _load_last_hash()
    if last_hash == current_hash:
        print("[INFO] La base no cambio desde el ultimo backup. No se crea copia nueva.")
        return

    # La base cambio -> crear backup nuevo
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"comite_auto_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    shutil.copy2(DB_PATH, backup_path)
    _save_last_hash(current_hash)
    print(f"[OK] Backup automatico creado: {backup_name}")


# -------------------------------------------------
# UTILIDAD: CREAR TABLAS Y USUARIO ADMIN
# -------------------------------------------------


def create_tables_and_admin():
    db.create_all()
    ensure_patient_extra_columns()
    ensure_study_extra_columns()
    ensure_consultation_extra_columns()
    ensure_medical_resource_columns()
    ensure_screening_extra_columns()

    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(
            full_name="Administrador Comite",
            specialty="",
            email="admin@comite.com",
            username="admin",
            role="admin",
            status="approved",
        )
        admin.set_password("Admin2025!")
        db.session.add(admin)
        db.session.commit()
        print("[OK] Usuario admin creado (usuario: admin / pass: Admin2025!)")
    else:
        # Aseguramos que siga siendo admin y aprobado
        changed = False
        if admin.role != "admin":
            admin.role = "admin"
            changed = True
        if admin.status != "approved":
            admin.status = "approved"
            changed = True
        if changed:
            db.session.commit()
            print("[INFO] Usuario admin actualizado a rol=admin, status=approved.")


import threading

_DB_INIT_LOCK = threading.Lock()
_DB_INIT_DONE = False


@app.before_request
def _init_db_if_needed():
    """Inicializa tablas y admin en el primer request (thread-safe)."""
    global _DB_INIT_DONE
    if _DB_INIT_DONE:
        return
    
    with _DB_INIT_LOCK:
        if _DB_INIT_DONE:
            return
        try:
            print("[INIT] Initializing database (first request)...")
            create_tables_and_admin()
            print("[OK] Database initialization complete")
        except Exception as e:
            print(f"[WARN] Database initialization: {e}")
        finally:
            _DB_INIT_DONE = True



# -------------------------------------------------
# RUTAS
# -------------------------------------------------


@app.route("/")
@login_required
def dashboard():
    # lista simple de pacientes para el inicio
    patients = Patient.query.order_by(Patient.full_name.asc()).all()
    return render_template("dashboard.html", patients=patients)


# --------------- AUTH ----------------------------


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name")
        specialty = request.form.get("specialty")
        email = request.form.get("email")
        username = request.form.get("username")
        password = (request.form.get("password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()

        if not full_name or not email or not username or not password:
            flash("Todos los campos marcados son obligatorios.", "danger")
            return redirect(url_for("register"))

        if User.query.filter_by(username=username).first():
            flash("El nombre de usuario ya esta en uso.", "danger")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("El email ya esta registrado.", "danger")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Las contrasenas no coinciden.", "danger")
            return redirect(url_for("register"))

        if not password_is_strong(password):
            flash(
                "La contrasena debe tener al menos 10 caracteres e incluir mayusculas, minusculas, numeros y simbolos.",
                "danger",
            )
            return redirect(url_for("register"))

        user = User(
            full_name=full_name,
            specialty=specialty,
            email=email,
            username=username,
            role="medico",
            status="pending",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Registro enviado. Un administrador debe aprobar tu cuenta.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username_or_email = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter(
            (User.username == username_or_email) | (User.email == username_or_email)
        ).first()

        if not user or not user.check_password(password):
            flash("Usuario o contrasena incorrectos.", "danger")
            return redirect(url_for("login"))

        if user.status != "approved":
            flash("Tu usuario aun no esta aprobado por el administrador.", "warning")
            return redirect(url_for("login"))

        login_user(user)
        log_action("user_login", {"user_id": user.id, "method": "password"}, user=user)
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    log_action("user_logout", {"user_id": current_user.id})
    logout_user()
    return redirect(url_for("login"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def account_password():
    if request.method == "POST":
        current_password = (request.form.get("current_password") or "").strip()
        new_password = (request.form.get("new_password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()

        if not current_user.check_password(current_password):
            flash("La contrasena actual no es correcta.", "danger")
            return redirect(url_for("account_password"))

        if not password_is_strong(new_password):
            flash(
                "La nueva contrasena debe tener al menos 10 caracteres con mayusculas, minusculas, numeros y simbolos.",
                "danger",
            )
            return redirect(url_for("account_password"))

        if new_password != confirm_password:
            flash("La confirmacion no coincide.", "danger")
            return redirect(url_for("account_password"))

        current_user.set_password(new_password)
        db.session.commit()
        log_action("user_password_change", {"user_id": current_user.id})
        flash("Contrasena actualizada correctamente.", "success")
        return redirect(url_for("dashboard"))

    return render_template("account_password.html")


# --------------- ADMIN USUARIOS ------------------


@app.route("/admin/users")
@login_required
def admin_users():
    if current_user.role != "admin":
        flash("No tienes permisos para ver esta seccion.", "danger")
        return redirect(url_for("dashboard"))

    users = User.query.order_by(User.id.asc()).all()
    return render_template("admin_users.html", users=users)


@app.route("/admin/users/<int:user_id>/approve")
@login_required
def admin_user_approve(user_id):
    if current_user.role != "admin":
        flash("No tienes permisos para realizar esta accion.", "danger")
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)
    user.status = "approved"
    db.session.commit()
    log_action("user_approve", {"target_user_id": user.id})
    flash("Usuario aprobado.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reject")
@login_required
def admin_user_reject(user_id):
    if current_user.role != "admin":
        flash("No tienes permisos para realizar esta accion.", "danger")
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)
    user.status = "rejected"
    db.session.commit()
    log_action("user_reject", {"target_user_id": user.id})
    flash("Usuario rechazado.", "success")
    return redirect(url_for("admin_users"))


# --------------- PACIENTES -----------------------


def build_patient_query_from_request(allow_full_filters: bool):
    search = (request.args.get("search") or "").strip()
    center = (request.args.get("center") or "").strip()
    smoking_filters = [
        value.strip()
        for value in request.args.getlist("smoking")
        if value.strip()
    ]
    sex_filters = [
        value.strip()
        for value in request.args.getlist("sex")
        if value.strip()
    ]
    min_age = (request.args.get("min_age") or "").strip()
    max_age = (request.args.get("max_age") or "").strip()
    # New 3-level search fields
    patient_data_keyword = (request.args.get("patient_data_keyword") or "").strip()
    studies_keyword = (request.args.get("studies_keyword") or "").strip()
    consultation_keyword = (request.args.get("consultation_keyword") or "").strip()
    # Legacy field (backwards compatibility)
    respiratory_keyword = (request.args.get("respiratory_keyword") or "").strip()
    city = (request.args.get("city") or "").strip()

    query = Patient.query
    join_consultations = False
    join_studies = False
    
    if search:
        like_pattern = f"%{search}%"
        if allow_full_filters:
            query = query.filter(
                or_(
                    Patient.full_name.ilike(like_pattern),
                    Patient.dni.ilike(like_pattern),
                    Patient.city.ilike(like_pattern),
                )
            )
        else:
            query = query.filter(
                or_(
                    Patient.full_name.ilike(like_pattern),
                    Patient.dni.ilike(like_pattern),
                )
            )
    if allow_full_filters:
        if center:
            query = query.filter(Patient.center == center)
        if smoking_filters:
            smoking_exprs = []
            if "smoker" in smoking_filters:
                smoking_exprs.append(Patient.smoking_current.is_(True))
            if "former" in smoking_filters:
                smoking_exprs.append(
                    and_(
                        Patient.smoking_previous.is_(True),
                        or_(Patient.smoking_current.is_(False), Patient.smoking_current.is_(None)),
                    )
                )
            if "never" in smoking_filters:
                smoking_exprs.append(
                    and_(
                        or_(Patient.smoking_current.is_(False), Patient.smoking_current.is_(None)),
                        or_(Patient.smoking_previous.is_(False), Patient.smoking_previous.is_(None)),
                    )
                )
            if smoking_exprs:
                query = query.filter(or_(*smoking_exprs))
        if sex_filters:
            query = query.filter(Patient.sex.in_(sex_filters))
        if min_age.isdigit():
            query = query.filter(Patient.age.isnot(None), Patient.age >= int(min_age))
        if max_age.isdigit():
            query = query.filter(Patient.age.isnot(None), Patient.age <= int(max_age))
        
        # NEW: Level 1 - Datos del paciente (antecedentes, síntomas, exposición, etc.)
        if patient_data_keyword:
            like_kw = f"%{patient_data_keyword}%"
            query = query.filter(
                or_(
                    Patient.respiratory_conditions.ilike(like_kw),
                    Patient.antecedentes.ilike(like_kw),
                    Patient.diagnoses.ilike(like_kw),
                    Patient.autoimmune_conditions.ilike(like_kw),
                    Patient.systemic_symptoms.ilike(like_kw),
                    Patient.notes_respiratory_exam.ilike(like_kw),
                    Patient.occupational_exposure_types.ilike(like_kw),
                    Patient.occupational_jobs.ilike(like_kw),
                    Patient.domestic_exposures.ilike(like_kw),
                    Patient.drug_use.ilike(like_kw),
                    Patient.current_medications.ilike(like_kw),
                    Patient.notes_family_history.ilike(like_kw),
                    Patient.notes_autoimmune.ilike(like_kw),
                    Patient.notes_exposures.ilike(like_kw),
                )
            )
        
        # NEW: Level 2 - Estudios e imágenes (laboratorio, radiografía, tomografía, etc.)
        if studies_keyword:
            like_kw = f"%{studies_keyword}%"
            query = query.outerjoin(Study, Study.patient_id == Patient.id)
            join_studies = True
            query = query.filter(
                or_(
                    Study.study_type.ilike(like_kw),
                    Study.description.ilike(like_kw),
                    Study.center.ilike(like_kw),
                )
            )
        
        # NEW: Level 3 - Historial de consultas (diagnósticos, tratamientos, notas)
        if consultation_keyword:
            like_kw = f"%{consultation_keyword}%"
            query = query.outerjoin(Consultation, Consultation.patient_id == Patient.id)
            join_consultations = True
            query = query.filter(
                or_(
                    Consultation.notes.ilike(like_kw),
                    Consultation.lab_general.ilike(like_kw),
                    Consultation.lab_immunology.ilike(like_kw),
                    Consultation.lab_immunology_notes.ilike(like_kw),
                )
            )
        
        # LEGACY: respiratory_keyword (backwards compatibility)
        if respiratory_keyword:
            like_kw = f"%{respiratory_keyword}%"
            if not join_consultations:
                query = query.outerjoin(Consultation, Consultation.patient_id == Patient.id)
                join_consultations = True
            query = query.filter(
                or_(
                    Patient.respiratory_conditions.ilike(like_kw),
                    Patient.antecedentes.ilike(like_kw),
                    Consultation.notes.ilike(like_kw),
                )
            )
        
        if city:
            query = query.filter(Patient.city.ilike(city))
    else:
        center = ""
        smoking_filters = []
        sex_filters = []
        min_age = ""
        max_age = ""
        patient_data_keyword = ""
        studies_keyword = ""
        consultation_keyword = ""
        respiratory_keyword = ""
        city = ""

    if join_consultations or join_studies:
        query = query.distinct()

    patients = query.order_by(Patient.full_name.asc()).all()

    db_centers = [
        row[0]
        for row in db.session.query(Patient.center)
        .filter(Patient.center.isnot(None))
        .distinct()
    ]
    center_options = list(dict.fromkeys(CATALOGS.get("centers", []) + db_centers))

    city_options = [
        row[0]
        for row in db.session.query(Patient.city)
        .filter(Patient.city.isnot(None))
        .distinct()
        .order_by(Patient.city.asc())
    ]

    return {
        "query": query.order_by(Patient.full_name.asc()),
        "search": search,
        "center": center,
        "smoking_filters": smoking_filters,
        "sex_filters": sex_filters,
        "min_age": min_age,
        "max_age": max_age,
        "patient_data_keyword": patient_data_keyword,
        "studies_keyword": studies_keyword,
        "consultation_keyword": consultation_keyword,
        "respiratory_keyword": respiratory_keyword,
        "city_filter": city,
        "available_centers": center_options,
        "city_options": city_options,
    }


@app.route("/patients")
@login_required
def patients_list():
    is_admin = current_user.role == "admin"
    filters = build_patient_query_from_request(is_admin)
    query = filters.pop("query")
    patients = query.all()
    return render_template(
        "patients_list.html",
        patients=patients,
        is_admin=is_admin,
        **filters,
        respiratory_options=_get_catalog_pairs("respiratory_conditions"),
        sex_options=["Masculino", "Femenino", "Otro"],
    )


@app.route("/patients/export/summary")
@login_required
def patients_export_summary():
    if current_user.role != "admin":
        flash("Solo los administradores pueden exportar datos.", "danger")
        return redirect(url_for("patients_list"))

    filters = build_patient_query_from_request(True)
    query = filters["query"]
    patients = query.all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Centro",
            "Ciudad",
            "Sexo",
            "Edad",
            "Consumo actual",
            "Ex fumador",
            "IPA",
            "Patologias respiratorias",
            "Consentimiento",
            "Fecha consentimiento",
        ]
    )
    for patient in patients:
        conditions = _deserialize_list(patient.respiratory_conditions)
        writer.writerow(
            [
                patient.center or "",
                patient.city or "",
                patient.sex or "",
                patient.age or "",
                "Si" if patient.smoking_current else "No",
                "Si" if patient.smoking_previous else "No",
                patient.smoking_pack_years or "",
                "; ".join(conditions) if conditions else "",
                "Si" if patient.consent_given else "No",
                patient.consent_date or "",
            ]
        )

    csv_data = output.getvalue()
    response = make_response(csv_data)
    response.headers["Content-Type"] = "text/csv"
    response.headers["Content-Disposition"] = (
        "attachment; filename=pacientes_resumen.csv"
    )
    log_action("patients_export_summary", {"count": len(patients)})
    return response


@app.route("/patients/new", methods=["GET", "POST"])
@login_required
def patient_new():
    form_options = patient_form_options()
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        dni = (request.form.get("dni") or "").strip()
        email = (request.form.get("email") or "").strip()
        consent_flag = _checkbox_to_bool(request.form.get("consent_given"))
        consent_date = (request.form.get("consent_date") or "").strip()

        # Paciente temporal para re-renderizar el form con lo ingresado
        temp_patient = Patient(created_by=current_user)
        populate_patient_from_form(temp_patient, request.form, current_user)

        missing = []
        if not full_name:
            missing.append("Apellido y nombre")
        if not dni:
            missing.append("DNI")
        if not email:
            missing.append("Email")
        if not consent_flag:
            missing.append("Consentimiento informado")
        if consent_flag and not consent_date:
            missing.append("Fecha de consentimiento")

        if missing:
            flash("Completar campos obligatorios: " + ", ".join(missing), "danger")
            return render_template("patient_new.html", patient=temp_patient, **form_options)

        if dni:
            existing = Patient.query.filter(Patient.dni == dni).first()
            if existing:
                flash(
                    f"Ya existe un paciente con el DNI {dni}. Verifica antes de continuar.",
                    "warning",
                )
                return redirect(url_for("patient_detail", patient_id=existing.id))

        patient = Patient(created_by=current_user)
        populate_patient_from_form(patient, request.form, current_user)
        db.session.add(patient)
        db.session.commit()

        genogram_pdf = request.files.get("family_genogram_pdf")
        if genogram_pdf and genogram_pdf.filename:
            filename = secure_filename(genogram_pdf.filename)
            if allowed_patient_file(filename):
                unique_name = f"patient_{patient.id}_familiograma_{int(time.time())}.pdf"
                save_upload(genogram_pdf, unique_name)
                patient.family_genogram_pdf = unique_name
                db.session.commit()
            else:
                flash("El familiograma solo admite PDF.", "warning")

        log_action("patient_create", {"patient_id": patient.id})
        flash("Paciente agregado correctamente.", "success")
        return redirect(url_for("patients_list"))

    return render_template("patient_new.html", patient=None, **form_options)


@app.route("/patients/<int:patient_id>")
@login_required
def patient_detail(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    # Consultas ordenadas por fecha (si hay) o por id
    consultations = (
        Consultation.query.filter_by(patient_id=patient.id)
        .order_by(Consultation.date.desc().nullslast(), Consultation.id.desc())
        .all()
    )
    control_reminders = (
        ControlReminder.query.filter_by(patient_id=patient.id)
        .order_by(
            ControlReminder.completed.asc(),
            ControlReminder.control_date.desc().nullslast(),
            ControlReminder.created_at.desc(),
        )
        .all()
    )
    studies = (
        Study.query.filter_by(patient_id=patient.id)
        .order_by(Study.date.desc().nullslast(), Study.id.desc())
        .all()
    )
    
    # Agrupar estudios por consulta (si están asociados a una)
    studies_by_consultation = {}
    standalone_studies = []
    
    for study in studies:
        if study.consultation_id:
            # Estudio ligado a una consulta
            if study.consultation_id not in studies_by_consultation:
                consultation = study.consultation
                studies_by_consultation[study.consultation_id] = {
                    'consultation': consultation,
                    'studies': []
                }
            studies_by_consultation[study.consultation_id]['studies'].append(study)
        else:
            # Estudio suelto (sin consulta)
            standalone_studies.append(study)
    
    # Ordenar consultas por fecha descendente
    sorted_consultation_studies = sorted(
        studies_by_consultation.values(),
        key=lambda x: x['consultation'].date or '',
        reverse=True
    )
    pending_reviews = []
    all_reviews = []
    for review in sorted(patient.review_requests, key=lambda r: r.created_at or datetime.datetime.min, reverse=True):
        recipients = []
        ids = review.recipient_ids()
        if ids:
            recipients = (
                User.query.filter(User.id.in_(ids))
                .order_by(User.full_name.asc())
                .all()
            )
        entry = {"review": review, "recipients": recipients}
        all_reviews.append(entry)
        if review.status == "pending":
            pending_reviews.append(entry)
    return render_template(
        "patient_detail.html",
        patient=patient,
        consultations=consultations,
        studies=studies,
        sorted_consultation_studies=sorted_consultation_studies,
        standalone_studies=standalone_studies,
        pending_reviews=pending_reviews,
        all_reviews=all_reviews,
        center_links=CENTER_PORTAL_LINKS,
        immuno_map=IMMUNO_LAB_DICT,
        control_reminders=control_reminders,
        ScreeningFollowup=ScreeningFollowup,
    )


@app.route("/patients/<int:patient_id>/case-presentation", methods=["GET", "POST"])
@login_required
def patient_case_presentation(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    consultations = (
        Consultation.query.filter_by(patient_id=patient.id)
        .order_by(Consultation.date.desc().nullslast(), Consultation.id.desc())
        .all()
    )
    studies = (
        Study.query.filter_by(patient_id=patient.id)
        .order_by(Study.date.desc().nullslast(), Study.id.desc())
        .all()
    )
    latest_consultation = consultations[0] if consultations else None
    latest_study = studies[0] if studies else None

    domestic_list = set(_deserialize_list(patient.domestic_exposures))
    laboral_list = set(_deserialize_list(patient.occupational_jobs))

    domestic_flags = [
        (label, key in domestic_list) for key, label in DOMESTIC_LABELS.items()
    ]
    laboral_flags = [
        (label, key in laboral_list) for key, label in LABORAL_LABELS.items()
    ]

    presentation = CasePresentation.query.filter_by(patient_id=patient.id).first()
    if not presentation:
        defaults = build_case_defaults(
            patient, latest_consultation, latest_study, domestic_flags, laboral_flags
        )
        presentation = CasePresentation(patient=patient, **defaults)
        db.session.add(presentation)
        db.session.commit()

    if request.method == "POST":
        presentation.intro = request.form.get("intro")
        presentation.physical_exam = request.form.get("physical_exam")
        presentation.respiratory_tests = request.form.get("respiratory_tests")
        presentation.immunology = request.form.get("immunology")
        presentation.exposures = request.form.get("exposures")
        presentation.imaging = request.form.get("imaging")
        presentation.notes = request.form.get("notes")
        action = request.form.get("action")
        db.session.commit()
        log_action(
            "case_presentation_update",
            {"patient_id": patient.id, "action": action},
        )
        if action == "download_word":
            content = render_template(
                "case_presentation_doc.html",
                patient=patient,
                presentation=presentation,
            )
            response = make_response(content)
            response.headers["Content-Type"] = "application/msword"
            response.headers["Content-Disposition"] = (
                f"attachment; filename=caso_{patient.id}.doc"
            )
            return response
        else:
            flash("Presentacion actualizada.", "success")

    return render_template(
        "case_presentation.html",
        patient=patient,
        presentation=presentation,
        center_links=CENTER_PORTAL_LINKS,
    )


@app.route("/patients/<int:patient_id>/screening", methods=["GET", "POST"])
@login_required
def patient_screening(patient_id):
    patient = Patient.query.get_or_404(patient_id)

    screening = Screening.query.filter_by(patient_id=patient.id).first()
    if not screening:
        screening = Screening(patient=patient)
        db.session.add(screening)
        db.session.commit()
    followups = (
        ScreeningFollowup.query.filter_by(screening_id=screening.id)
        .order_by(ScreeningFollowup.study_date.desc().nullslast(), ScreeningFollowup.created_at.desc())
        .all()
    )

    # Elegibilidad automática básica (NCCN: edad ≥50, fumador actual o previo, IPA ≥20)
    def _compute_eligibility(p: Patient):
        reasons = []
        age = p.age
        pack_years = p.smoking_pack_years
        is_smoker = bool(p.smoking_current or p.smoking_previous)
        age_ok = age is not None and age >= 50
        ipa_ok = pack_years is not None and pack_years >= 20
        if age_ok:
            reasons.append(f"Edad {age} años (≥50)")
        else:
            reasons.append(f"Edad {age if age is not None else '---'} (se requiere ≥50)")
        if is_smoker:
            reasons.append("Antecedente de tabaquismo (actual o previo)")
        else:
            reasons.append("Sin antecedente de tabaquismo")
        if ipa_ok:
            reasons.append(f"IPA {pack_years} (≥20)")
        else:
            reasons.append(f"IPA {pack_years if pack_years is not None else '---'} (se requiere ≥20)")
        return age_ok and is_smoker and ipa_ok, reasons

    eligibility_met, eligibility_reasons = _compute_eligibility(patient)

    # Helper para mapear listas a labels
    def _labels_from_catalog(values, catalog_key):
        if not values:
            return []
        pairs = {v: lbl for v, lbl in _get_catalog_pairs(catalog_key)}
        result = []
        for val in _deserialize_list(values):
            result.append(pairs.get(val, val))
        return result

    resp_labels = _labels_from_catalog(patient.respiratory_conditions, "respiratory_conditions")
    occ_exp_labels = _labels_from_catalog(patient.occupational_exposure_types, "occupational_exposures")
    occ_jobs_labels = _labels_from_catalog(patient.occupational_jobs, "occupational_jobs")
    domestic_labels = _labels_from_catalog(patient.domestic_exposures, "domestic_exposures")

    auto_summary_lines = []
    if resp_labels:
        auto_summary_lines.append("Antecedentes respiratorios: " + ", ".join(resp_labels))
    if occ_exp_labels:
        auto_summary_lines.append("Exposición ocupacional: " + ", ".join(occ_exp_labels))
    if occ_jobs_labels:
        auto_summary_lines.append("Trabajos/ocupaciones: " + ", ".join(occ_jobs_labels))
    if domestic_labels:
        auto_summary_lines.append("Exposiciones domiciliarias: " + ", ".join(domestic_labels))

    # Prefill criterio NCCN si está vacío
    if request.method == "GET" and not screening.nccn_criteria:
        status = "Cumple criterios básicos (edad≥50, tabaquismo, IPA≥20)" if eligibility_met else "No cumple criterios básicos (edad≥50, tabaquismo, IPA≥20)"
        smoking_label = "Fumador actual" if patient.smoking_current else ("Ex fumador" if patient.smoking_previous else "No fumador")
        screening.nccn_criteria = f"{status}. {smoking_label}. IPA: {patient.smoking_pack_years or '---'}."
        if auto_summary_lines:
            screening.nccn_criteria += " " + " ".join(auto_summary_lines)

    # Prefill hallazgos si está vacío, con antecedentes relevantes
    if request.method == "GET" and not screening.findings and auto_summary_lines:
        screening.findings = " | ".join(auto_summary_lines)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_followup":
            fu_type = (request.form.get("fu_study_type") or "").strip() or None
            fu_center = (request.form.get("fu_study_center") or "").strip() or None
            fu_number = (request.form.get("fu_study_number") or "").strip() or None
            fu_date = (request.form.get("fu_study_date") or "").strip() or None
            fu_findings = (request.form.get("fu_findings") or "").strip() or None
            fu_lung_rads = (request.form.get("fu_lung_rads") or "").strip() or None
            fu_next_date = (request.form.get("fu_next_control_date") or "").strip() or None
            fu_file = request.files.get("fu_file")

            if not fu_type or not fu_date:
                flash("El tipo de estudio y la fecha son obligatorios para agregar un control.", "danger")
                return render_template(
                    "screening.html",
                    patient=patient,
                    screening=screening,
                    followups=followups,
                    center_links=CENTER_PORTAL_LINKS,
                    center_options=CATALOGS.get("centers", []),
                    eligibility_met=eligibility_met,
                    eligibility_reasons=eligibility_reasons,
                )

            file_name = None
            if fu_file and fu_file.filename:
                fname = secure_filename(fu_file.filename)
                if allowed_patient_file(fname):
                    unique_name = f"screeningfu_{screening.id}_{int(time.time())}_{fname}"
                    save_upload(fu_file, unique_name)
                    file_name = unique_name
                else:
                    flash("Solo se permiten PDF/imagenes (pdf/png/jpg/jpeg).", "danger")
                    return render_template(
                        "screening.html",
                        patient=patient,
                        screening=screening,
                        followups=followups,
                        center_links=CENTER_PORTAL_LINKS,
                        eligibility_met=eligibility_met,
                        eligibility_reasons=eligibility_reasons,
                    )

            fu = ScreeningFollowup(
                screening=screening,
                study_type=fu_type,
                study_center=fu_center,
                study_number=fu_number,
                study_date=fu_date,
                findings=fu_findings,
                lung_rads=fu_lung_rads,
                next_control_date=fu_next_date,
                file_name=file_name,
                created_by=current_user,
            )
            db.session.add(fu)
            db.session.commit()
            notify_screening_creation(fu)
            flash("Control agregado.", "success")
            return redirect(
                url_for("patient_screening", patient_id=patient.id, _anchor="followups")
            )

        screening.screening_lung = _checkbox_to_bool(request.form.get("screening_lung"))
        screening.followup_nodule = _checkbox_to_bool(request.form.get("followup_nodule"))
        screening.ecog_status = (request.form.get("ecog_status") or "").strip() or None
        screening.extra_email = (request.form.get("extra_email") or "").strip() or None
        screening.family_history = _checkbox_to_bool(request.form.get("family_history"))
        screening.prior_ct = _checkbox_to_bool(request.form.get("prior_ct"))
        screening.prior_comparison = (
            (request.form.get("prior_comparison") or "").strip() or None
        )
        screening.study_center = (request.form.get("study_center") or "").strip() or None
        screening.study_number = (request.form.get("study_number") or "").strip() or None
        screening.study_date = (request.form.get("study_date") or "").strip() or None
        screening.findings = (request.form.get("findings") or "").strip() or None
        screening.lung_rads = (request.form.get("lung_rads") or "").strip() or None
        screening.conclusion = (
            (request.form.get("conclusion") or "").strip() or None
        )
        screening.nccn_criteria = (
            (request.form.get("nccn_criteria") or "").strip() or None
        )
        screening.next_control_date = (
            (request.form.get("next_control_date") or "").strip() or None
        )

        study_file = request.files.get("study_file")
        if study_file and study_file.filename:
            filename = secure_filename(study_file.filename)
            if allowed_patient_file(filename):
                unique_name = f"screening_{patient.id}_{int(time.time())}.pdf"
                save_upload(study_file, unique_name)
                screening.study_file = unique_name
            else:
                flash("Solo se permiten PDF/imagenes (pdf/png/jpg/jpeg).", "danger")
                return render_template(
                    "screening.html",
                    patient=patient,
                    screening=screening,
                    center_links=CENTER_PORTAL_LINKS,
                    eligibility_met=eligibility_met,
                    eligibility_reasons=eligibility_reasons,
                )
        db.session.commit()
        flash("Screening actualizado.", "success")
        followups = (
            ScreeningFollowup.query.filter_by(screening_id=screening.id)
            .order_by(ScreeningFollowup.study_date.desc().nullslast(), ScreeningFollowup.created_at.desc())
            .all()
        )

    return render_template(
        "screening.html",
        patient=patient,
        screening=screening,
        followups=followups,
        center_links=CENTER_PORTAL_LINKS,
        center_options=CATALOGS.get("centers", []),
        eligibility_met=eligibility_met,
        eligibility_reasons=eligibility_reasons,
    )


@app.route("/patients/<int:patient_id>/screening/file")
@login_required
def patient_screening_file(patient_id):
    screening = Screening.query.filter_by(patient_id=patient_id).first_or_404()
    if not screening.study_file:
        flash("No hay archivo de screening cargado.", "warning")
        return redirect(url_for("patient_screening", patient_id=patient_id))
    if not upload_exists(screening.study_file):
        flash("El archivo no se encuentra disponible.", "danger")
        return redirect(url_for("patient_screening", patient_id=patient_id))
    return send_upload_file(screening.study_file, as_attachment=True)


@app.route("/screening/followup/<int:followup_id>/file")
@login_required
def screening_followup_file(followup_id):
    fu = ScreeningFollowup.query.get_or_404(followup_id)
    if not fu.file_name:
        flash("No hay archivo adjunto para este control.", "warning")
        return redirect(url_for("patient_screening", patient_id=fu.screening.patient_id))
    if not upload_exists(fu.file_name):
        flash("El archivo no se encuentra disponible.", "danger")
        return redirect(url_for("patient_screening", patient_id=fu.screening.patient_id))
    return send_upload_file(fu.file_name, as_attachment=True)


@app.route("/screening/followup/<int:followup_id>/delete", methods=["POST"])
@login_required
def screening_followup_delete(followup_id):
    fu = ScreeningFollowup.query.get_or_404(followup_id)
    patient_id = fu.screening.patient_id
    # borrar archivo si existe
    if fu.file_name:
        delete_upload(fu.file_name)
    db.session.delete(fu)
    db.session.commit()
    flash("Control eliminado.", "success")
    return redirect(url_for("patient_screening", patient_id=patient_id, _anchor="followups"))


@app.route("/screening/followup/<int:followup_id>/complete", methods=["POST"])
@login_required
def screening_followup_complete(followup_id):
    fu = ScreeningFollowup.query.get_or_404(followup_id)
    patient_id = fu.screening.patient_id
    if fu.created_by_id and fu.created_by_id != current_user.id:
        flash("No tienes permisos para cerrar este control.", "danger")
        return redirect(url_for("reviews_inbox"))
    fu.status = "done"
    fu.completed = True
    fu.completed_at = datetime.datetime.utcnow()
    db.session.commit()
    flash("Control marcado como finalizado.", "success")
    next_url = request.form.get("next") or request.args.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(url_for("reviews_inbox"))


@app.route("/screening/followup/<int:followup_id>/progress", methods=["POST"])
@login_required
def screening_followup_progress(followup_id):
    fu = ScreeningFollowup.query.get_or_404(followup_id)
    if fu.created_by_id and fu.created_by_id != current_user.id:
        flash("No tienes permisos para cambiar este control.", "danger")
        return redirect(url_for("reviews_inbox"))
    fu.status = "in_progress"
    fu.completed = False
    db.session.commit()
    flash("Control marcado en proceso.", "success")
    next_url = request.form.get("next") or request.args.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(url_for("reviews_inbox"))


@app.route("/screening/followup/<int:followup_id>/edit", methods=["GET", "POST"])
@login_required
def screening_followup_edit(followup_id):
    fu = ScreeningFollowup.query.get_or_404(followup_id)
    screening = fu.screening
    patient = screening.patient
    if request.method == "POST":
        fu.study_type = (request.form.get("study_type") or "").strip() or None
        fu.study_date = (request.form.get("study_date") or "").strip() or None
        fu.study_center = (request.form.get("study_center") or "").strip() or None
        fu.study_number = (request.form.get("study_number") or "").strip() or None
        fu.findings = (request.form.get("findings") or "").strip() or None
        fu.lung_rads = (request.form.get("lung_rads") or "").strip() or None
        fu.next_control_date = (request.form.get("next_control_date") or "").strip() or None

        file = request.files.get("file")
        if file and file.filename:
            fname = secure_filename(file.filename)
            if allowed_patient_file(fname):
                unique_name = f"screeningfu_{fu.id}_{int(time.time())}_{fname}"
                save_upload(file, unique_name)
                fu.file_name = unique_name
            else:
                flash("Solo se permiten PDF/imagenes (pdf/png/jpg/jpeg).", "danger")
                return redirect(url_for("screening_followup_edit", followup_id=fu.id))

        db.session.commit()
        flash("Control actualizado.", "success")
        return redirect(url_for("patient_screening", patient_id=patient.id, _anchor="followups"))

    return render_template(
        "screening_followup_edit.html",
        followup=fu,
        screening=screening,
        patient=patient,
    )


@app.route("/controls/<int:reminder_id>/complete", methods=["POST"])
@login_required
def control_reminder_complete(reminder_id):
    cr = ControlReminder.query.get_or_404(reminder_id)
    if cr.created_by_id and cr.created_by_id != current_user.id:
        flash("No tienes permisos para cerrar este control.", "danger")
        return redirect(url_for("reviews_inbox"))
    cr.status = "done"
    cr.completed = True
    cr.completed_at = datetime.datetime.utcnow()
    db.session.commit()
    flash("Control marcado como finalizado.", "success")
    next_url = request.form.get("next") or request.args.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(url_for("reviews_inbox"))


@app.route("/controls/<int:reminder_id>/progress", methods=["POST"])
@login_required
def control_reminder_progress(reminder_id):
    cr = ControlReminder.query.get_or_404(reminder_id)
    if cr.created_by_id and cr.created_by_id != current_user.id:
        flash("No tienes permisos para cambiar este control.", "danger")
        return redirect(url_for("reviews_inbox"))
    cr.status = "in_progress"
    cr.completed = False
    db.session.commit()
    flash("Control marcado en proceso.", "success")
    next_url = request.form.get("next") or request.args.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(url_for("reviews_inbox"))

@app.route("/patients/<int:patient_id>/print")
@login_required
def patient_print(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    consultations = (
        Consultation.query.filter_by(patient_id=patient.id)
        .order_by(Consultation.date.desc().nullslast(), Consultation.id.desc())
        .all()
    )
    studies = (
        Study.query.filter_by(patient_id=patient.id)
        .order_by(Study.date.desc().nullslast(), Study.id.desc())
        .all()
    )
    log_action("patient_print", {"patient_id": patient.id})
    return render_template(
        "patient_print.html",
        patient=patient,
        consultations=consultations,
        studies=studies,
    )


@app.route("/patients/<int:patient_id>/edit", methods=["GET", "POST"])
@login_required
def patient_edit(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    form_options = patient_form_options()

    if request.method == "POST":
        populate_patient_from_form(patient, request.form, current_user)
        genogram_pdf = request.files.get("family_genogram_pdf")
        if genogram_pdf and genogram_pdf.filename:
            filename = secure_filename(genogram_pdf.filename)
            if allowed_patient_file(filename):
                if patient.family_genogram_pdf:
                    delete_upload(patient.family_genogram_pdf)
                unique_name = f"patient_{patient.id}_familiograma_{int(time.time())}.pdf"
                save_upload(genogram_pdf, unique_name)
                patient.family_genogram_pdf = unique_name
            else:
                flash("El familiograma solo admite PDF.", "warning")

        db.session.commit()
        log_action("patient_update", {"patient_id": patient.id})
        flash("Datos del paciente actualizados.", "success")
        return redirect(url_for("patient_detail", patient_id=patient.id))

    return render_template("patient_edit.html", patient=patient, **form_options)


@app.route("/patients/<int:patient_id>/delete", methods=["POST", "GET"])
@login_required
@csrf.exempt
def patient_delete(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if current_user.role != "admin":
        flash("No tienes permisos para borrar pacientes.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient.id))

    try:
        # Controles de consulta (primero, para evitar FK sobre consultations)
        ControlReminder.query.filter_by(patient_id=patient.id).delete()

        # Revisiones y comentarios
        reviews = ReviewRequest.query.filter_by(patient_id=patient.id).all()
        if reviews:
            review_ids = [r.id for r in reviews]
            ReviewComment.query.filter(
                ReviewComment.review_id.in_(review_ids)
            ).delete(synchronize_session=False)
            ReviewRequest.query.filter(
                ReviewRequest.id.in_(review_ids)
            ).delete(synchronize_session=False)

        # Presentaci?n de caso
        CasePresentation.query.filter_by(patient_id=patient.id).delete()

        # Consultas y sus estudios
        for consultation in list(patient.consultations):
            Study.query.filter_by(consultation_id=consultation.id).delete()
            db.session.delete(consultation)

        # Estudios sueltos
        Study.query.filter_by(patient_id=patient.id, consultation_id=None).delete()

        # Screening y followups
        screenings = Screening.query.filter_by(patient_id=patient.id).all()
        for sc in screenings:
            ScreeningFollowup.query.filter_by(screening_id=sc.id).delete()
            db.session.delete(sc)

        if patient.family_genogram_pdf:
            delete_upload(patient.family_genogram_pdf)

        db.session.delete(patient)
        db.session.commit()
        log_action("patient_delete", {"patient_id": patient.id})
        flash("Paciente eliminado correctamente.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"No se pudo eliminar el paciente: {exc}", "danger")

    return redirect(url_for("patients_list"))


@app.route("/reviews")
@login_required
def reviews_inbox():
    requests = ReviewRequest.query.order_by(ReviewRequest.created_at.desc()).all()
    user_id = str(current_user.id)
    filtered = []
    for rr in requests:
        recipients = _deserialize_list(rr.recipients)
        if user_id in recipients or rr.created_by_id == current_user.id:
            filtered.append(rr)
    # Controles pendientes (consulta)
    controls = ControlReminder.query.filter(
        ControlReminder.status != "done", ControlReminder.control_date.isnot(None)
    ).all()
    pending_controls = []
    for c in controls:
        if c.created_by_id == current_user.id or (
            c.patient and c.patient.created_by_id == current_user.id
        ):
            pending_controls.append(c)

    # Controles pendientes (screening followups)
    followups = ScreeningFollowup.query.filter(
        ScreeningFollowup.status != "done", ScreeningFollowup.next_control_date.isnot(None)
    ).all()
    pending_followups = []
    for fu in followups:
        sc = fu.screening
        patient = sc.patient if sc else None
        if fu.created_by_id == current_user.id or (
            patient and patient.created_by_id == current_user.id
        ):
            pending_followups.append(fu)

    return render_template(
        "reviews.html",
        requests=filtered,
        pending_controls=pending_controls,
        pending_followups=pending_followups,
    )


@app.route("/patients/<int:patient_id>/family_genogram/file")
@login_required
def patient_family_genogram_download(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if not patient.family_genogram_pdf:
        flash("Este paciente no tiene familiograma adjunto.", "warning")
        return redirect(url_for("patient_detail", patient_id=patient.id))
    if not upload_exists(patient.family_genogram_pdf):
        flash("El archivo adjunto no se encuentra disponible.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient.id))
    return send_upload_file(patient.family_genogram_pdf, as_attachment=True)


@app.route("/reviews/<int:review_id>/resolve", methods=["POST"])
@login_required
def review_resolve(review_id):
    review = ReviewRequest.query.get_or_404(review_id)
    recipients = _deserialize_list(review.recipients)
    if str(current_user.id) not in recipients:
        flash("No tienes permisos para cerrar esta revision.", "danger")
        return redirect(url_for("reviews_inbox"))
    review.status = "resolved"
    review.resolved_at = datetime.datetime.utcnow()
    db.session.commit()
    log_action("review_request_resolved", {"review_id": review.id})
    flash("Revision marcada como resuelta.", "success")
    return redirect(url_for("reviews_inbox"))


@app.route("/reviews/<int:review_id>/progress", methods=["POST"])
@login_required
def review_progress(review_id):
    review = ReviewRequest.query.get_or_404(review_id)
    recipients = _deserialize_list(review.recipients)
    if str(current_user.id) not in recipients and review.created_by_id != current_user.id:
        flash("No tienes permisos para cambiar esta revision.", "danger")
        return redirect(url_for("reviews_inbox"))
    review.status = "in_progress"
    db.session.commit()
    flash("Revision marcada en proceso.", "success")
    return redirect(url_for("reviews_inbox"))


@app.route("/reviews/<int:review_id>/comment", methods=["POST"])
@login_required
def review_comment(review_id):
    review = ReviewRequest.query.get_or_404(review_id)
    recipients = _deserialize_list(review.recipients)
    if str(current_user.id) not in recipients and review.created_by_id != current_user.id:
        flash("No tienes permisos para comentar esta revision.", "danger")
        return redirect(url_for("reviews_inbox"))
    message = request.form.get("message")
    comment = add_review_comment(review, current_user, message)
    if comment:
        db.session.commit()
        flash("Comentario agregado.", "success")
    else:
        flash("Ingresa un comentario antes de enviar.", "warning")
    return redirect(url_for("reviews_inbox"))


@app.route("/reviews/comment/<int:comment_id>/edit", methods=["GET", "POST"])
@login_required
def review_comment_edit(comment_id):
    comment = ReviewComment.query.get_or_404(comment_id)
    review = comment.review
    allowed = comment.user_id == current_user.id or review.created_by_id == current_user.id
    if not allowed:
        flash("No tienes permisos para editar este comentario.", "danger")
        return redirect(url_for("reviews_inbox"))
    if request.method == "POST":
        new_message = request.form.get("message", "").strip()
        if new_message:
            comment.message = new_message
            db.session.commit()
            flash("Comentario actualizado.", "success")
        else:
            flash("El comentario no puede estar vacío.", "warning")
        return redirect(url_for("reviews_inbox"))
    return render_template("review_comment_edit.html", comment=comment, review=review)


@app.route("/reviews/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def review_comment_delete(comment_id):
    comment = ReviewComment.query.get_or_404(comment_id)
    review = comment.review
    allowed = comment.user_id == current_user.id or review.created_by_id == current_user.id
    if not allowed:
        flash("No tienes permisos para borrar este comentario.", "danger")
        return redirect(url_for("reviews_inbox"))
    db.session.delete(comment)
    db.session.commit()
    flash("Comentario eliminado.", "success")
    return redirect(url_for("reviews_inbox"))


# --------------- ESTUDIOS ------------------------


@app.route("/patients/<int:patient_id>/studies/new", methods=["GET", "POST"])
@login_required
def study_new(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    review_users = get_review_recipient_options(current_user)

    if request.method == "POST":
        study_type = request.form.get("study_type")
        date = request.form.get("date")
        center = request.form.get("center")
        description = request.form.get("description")
        access_code = (request.form.get("access_code") or "").strip() or None
        portal_link = (request.form.get("portal_link") or "").strip() or None
        pdf_file = request.files.get("study_file")

        study = Study(
            patient=patient,
            study_type=study_type,
            date=date,
            center=center,
            description=description,
            consultation=None,  # estudio general, no asociado a una consulta concreta
            created_by=current_user,
        )
        study.access_code = access_code
        study.portal_link = portal_link
        db.session.add(study)
        db.session.flush()

        if pdf_file and pdf_file.filename:
            filename = secure_filename(pdf_file.filename)
            if allowed_study_file(filename):
                unique_name = f"study_{study.id}_{int(time.time())}.pdf"
                save_upload(pdf_file, unique_name)
                study.report_file = unique_name
            else:
                flash("Solo se permiten archivos PDF para el reporte.", "danger")

        review_recips = _parse_recipient_ids(request.form.getlist("review_recipients"))
        review_message = request.form.get("review_message")
        if review_recips:
            create_review_request(
                patient,
                current_user,
                review_recips,
                review_message,
                study=study,
            )

        db.session.commit()
        flash("Estudio agregado correctamente.", "success")
        return redirect(url_for("patient_detail", patient_id=patient.id))

    return render_template(
        "study_new.html",
        patient=patient,
        study_type_options=STUDY_TYPE_OPTIONS,
        center_options=CATALOGS.get("centers", []),
        review_users=review_users,
        center_links=CENTER_PORTAL_LINKS,
    )


@app.route("/medical-info", methods=["GET", "POST"])
@login_required
def medical_info():
    resources = (
        MedicalResource.query.order_by(MedicalResource.title.asc()).all()
        if MedicalResource.query.count() > 0
        else []
    )

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        url_value = (request.form.get("url") or "").strip() or None
        notes_value = (request.form.get("notes") or "").strip() or None
        file = request.files.get("file")
        file_name = None

        if not title:
            flash("El título es obligatorio.", "danger")
            return render_template("medical_info.html", resources=resources)

        if file and file.filename:
            filename = secure_filename(file.filename)
            if allowed_patient_file(filename):
                unique_name = f"medres_{int(time.time())}_{filename}"
                save_upload(file, unique_name)
                file_name = unique_name
            else:
                flash("Formato no permitido. Solo PDF/PNG/JPG/JPEG.", "danger")
                return render_template("medical_info.html", resources=resources)

        res = MedicalResource(
            title=title,
            url=url_value,
            notes=notes_value,
            file_name=file_name,
            created_by=current_user,
        )
        db.session.add(res)
        db.session.commit()
        flash("Material guardado.", "success")
        resources = MedicalResource.query.order_by(MedicalResource.title.asc()).all()

    return render_template("medical_info.html", resources=resources)


@app.route("/medical-info/<int:resource_id>/download")
@login_required
def medical_info_download(resource_id):
    res = MedicalResource.query.get_or_404(resource_id)
    if not res.file_name:
        flash("Este recurso no tiene archivo adjunto.", "warning")
        return redirect(url_for("medical_info"))
    if not upload_exists(res.file_name):
        flash("El archivo adjunto no se encuentra disponible.", "danger")
        return redirect(url_for("medical_info"))
    return send_upload_file(res.file_name, as_attachment=True)

@app.route("/studies/<int:study_id>/file")
@login_required
def study_download(study_id):
    study = Study.query.get_or_404(study_id)
    if not study.report_file:
        flash("Este estudio no tiene archivo adjunto.", "warning")
        return redirect(url_for("patient_detail", patient_id=study.patient_id))
    if not upload_exists(study.report_file):
        flash("El archivo adjunto no se encuentra disponible.", "danger")
        return redirect(url_for("patient_detail", patient_id=study.patient_id))
    return send_upload_file(study.report_file, as_attachment=True)


@app.route("/studies/<int:study_id>/edit", methods=["GET", "POST"])
@login_required
def study_edit(study_id):
    study = Study.query.get_or_404(study_id)
    patient = study.patient
    review_users = get_review_recipient_options(current_user)
    multi_mode = bool(study.consultation_id)
    consultation = study.consultation if multi_mode else None

    # Otros estudios de la misma consulta (para navegar rápido entre ellos)
    sibling_studies = []
    if study.consultation_id:
        sibling_studies = (
            Study.query.filter(Study.consultation_id == study.consultation_id)
            .order_by(Study.date.desc().nullslast())
            .all()
        )

    # Permisos: 
    # - Si el estudio NO está ligado a consulta: solo el creador puede editar
    # - Si el estudio SÍ está ligado a consulta: cualquiera puede editar (solo la parte del estudio)
    if not study.consultation_id and study.created_by_id != current_user.id:
        flash("No tienes permiso para editar este estudio.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient.id))

    if request.method == "POST" and not multi_mode:
        # Acción: Eliminar estudio
        if request.form.get("action") == "delete":
            if study.report_file:
                delete_upload(study.report_file)
            db.session.delete(study)
            db.session.commit()
            flash("Estudio eliminado correctamente.", "success")
            return redirect(url_for("patient_detail", patient_id=patient.id))
        
        # Acción: Agregar estudio (crear nuevo estudio del mismo grupo)
        if request.form.get("action") == "add_study":
            # Detectar el grupo del estudio actual
            study_type_lower = (study.study_type or "").lower()
            if any(kw in study_type_lower for kw in ['espirometría', 'test de la marcha', 'dlco', 'volúmenes']):
                new_group = 'func'
            elif any(kw in study_type_lower for kw in ['tc', 'rm', 'pet', 'rx', 'ecografia', 'ecocardiograma', 'ecodoppler']):
                new_group = 'img'
            elif any(kw in study_type_lower for kw in ['fibrobroncoscopía', 'biopsia', 'bal']):
                new_group = 'inv'
            else:
                new_group = 'other'
            
            # Crear nuevo estudio en la misma consulta
            new_study = Study(
                patient=patient,
                consultation=study.consultation,
                created_by=current_user,
            )
            db.session.add(new_study)
            db.session.commit()
            flash("Nuevo estudio agregado. Completa sus datos.", "success")
            return redirect(url_for("study_edit", study_id=new_study.id))
        
        # Acción: Guardar cambios
        study.study_type = request.form.get("study_type")
        study.date = request.form.get("date")
        study.center = request.form.get("center")
        study.description = request.form.get("description")
        study.access_code = (request.form.get("access_code") or "").strip() or None
        study.portal_link = (request.form.get("portal_link") or "").strip() or None

        pdf_file = request.files.get("study_file")
        if pdf_file and pdf_file.filename:
            filename = secure_filename(pdf_file.filename)
            if allowed_study_file(filename):
                if study.report_file:
                    delete_upload(study.report_file)
                unique_name = f"study_{study.id}_{int(time.time())}.pdf"
                save_upload(pdf_file, unique_name)
                study.report_file = unique_name
            else:
                flash("Solo se permiten archivos PDF para el reporte.", "danger")

        db.session.commit()
        flash("Estudio actualizado correctamente.", "success")
        return redirect(url_for("patient_detail", patient_id=patient.id))

    if request.method == "POST" and multi_mode:
        # Modo multi-estudio (igual estructura que consultation_edit)
        consultation = study.consultation

        # Guardar estudios existentes ANTES de borrar (para preservar archivos PDF por orden)
        existing_studies = Study.query.filter_by(consultation_id=consultation.id).order_by(Study.id).all()
        existing_files_by_type = {'func': [], 'img': [], 'inv': []}
        for st in existing_studies:
            if st.report_file:
                if st.study_type in ["Espirometría", "Test de la Marcha 6m", "DLCO", "Volúmenes pulmonares"]:
                    existing_files_by_type['func'].append(st.report_file)
                elif st.study_type in ["TC Tórax", "RM Tórax", "PET-CT", "RX", "Ecografía", "Ecocardiograma", "Ecodoppler Angiopower"]:
                    existing_files_by_type['img'].append(st.report_file)
                elif st.study_type in ["Fibrobroncoscopía", "Biopsia", "BAL", "Otro"]:
                    existing_files_by_type['inv'].append(st.report_file)

        # Eliminar y recrear
        Study.query.filter_by(consultation_id=consultation.id).delete()
        db.session.flush()

        study_groups = request.form.getlist("study_groups") or []
        studies_created = []

        def _get_list(key):
            vals = request.form.getlist(key)
            if vals:
                return [v.strip() for v in vals]
            vals = request.form.getlist(f"{key}[]")
            return [v.strip() for v in vals] if vals else []

        def _get_files(key):
            files = request.files.getlist(key)
            if files:
                return files
            files = request.files.getlist(f"{key}[]")
            return files or []

        func_types = _get_list("study_type_func")
        func_dates = _get_list("study_date_func")
        func_desc = (request.form.get("study_description_func") or "").strip() or None
        func_files = _get_files("study_file_func")

        img_types = _get_list("study_type_img")
        img_dates = _get_list("study_date_img")
        img_centers = _get_list("study_center_img")
        img_accesses = _get_list("study_access_code_img")
        img_links = _get_list("study_portal_link_img")
        img_desc = (request.form.get("study_description_img") or "").strip() or None
        img_files = _get_files("study_file_img")

        inv_types = _get_list("study_type_inv")
        inv_dates = _get_list("study_date_inv")
        inv_desc = (request.form.get("study_description_inv") or "").strip() or None
        inv_files = _get_files("study_file_inv")

        def add_studies_from_lists(types, dates, shared_desc, centers=None, accesses=None, links=None):
            nonlocal studies_created
            added = 0
            max_len = max(len(types) if types else 0, len(dates) if dates else 0)
            for idx in range(max_len):
                stype = (types[idx] if idx < len(types) else "") or ""
                stype = stype.strip()
                sdate = (dates[idx] if idx < len(dates) else "") or ""
                sdate = sdate.strip()
                center = (centers[idx] if centers and idx < len(centers) else "").strip() if centers else ""
                access = (accesses[idx] if accesses and idx < len(accesses) else "").strip() if accesses else ""
                link = (links[idx] if links and idx < len(links) else "").strip() if links else ""
                if not stype and not sdate:
                    continue
                new_study = Study(
                    patient=patient,
                    consultation=consultation,
                    study_type=stype or "Estudio asociado a consulta",
                    date=sdate or consultation.date,
                    center=center or None,
                    description=shared_desc or None,
                    created_by=current_user,
                )
                new_study.access_code = access or None
                new_study.portal_link = link or None
                db.session.add(new_study)
                studies_created.append(new_study)
                added += 1
            return added

        group_indices = {}
        if "func" in study_groups:
            group_indices['func'] = (len(studies_created), add_studies_from_lists(func_types, func_dates, func_desc))
        if "img" in study_groups:
            group_indices['img'] = (len(studies_created), add_studies_from_lists(img_types, img_dates, img_desc, centers=img_centers, accesses=img_accesses, links=img_links))
        if "inv" in study_groups:
            group_indices['inv'] = (len(studies_created), add_studies_from_lists(inv_types, inv_dates, inv_desc))

        db.session.flush()

        def _save_file_for_study_filelist(filelist, start_idx, count, group_name):
            if not filelist:
                if group_name in existing_files_by_type:
                    old_files = existing_files_by_type[group_name]
                    for i in range(count):
                        idx = start_idx + i
                        if idx < len(studies_created) and i < len(old_files):
                            studies_created[idx].report_file = old_files[i]
                return
            if len(filelist) == 1 and count > 0:
                f = filelist[0]
                if f and getattr(f, "filename", ""):
                    filename = secure_filename(f.filename)
                    if allowed_study_file(filename):
                        idx = start_idx
                        if idx < len(studies_created):
                            unique_name = f"study_{studies_created[idx].id}_{int(time.time())}.pdf"
                            save_upload(f, unique_name)
                            studies_created[idx].report_file = unique_name
                    else:
                        flash("Solo se permiten archivos PDF para el reporte.", "danger")
                else:
                    if group_name in existing_files_by_type and existing_files_by_type[group_name]:
                        old_files = existing_files_by_type[group_name]
                        idx = start_idx
                        if idx < len(studies_created) and idx < len(old_files):
                            studies_created[idx].report_file = old_files[idx]
                return
            for i in range(count):
                idx = start_idx + i
                if idx >= len(studies_created):
                    break
                f = filelist[i] if i < len(filelist) else None
                if not f or not getattr(f, "filename", ""):
                    if group_name in existing_files_by_type:
                        old_files = existing_files_by_type[group_name]
                        if i < len(old_files):
                            studies_created[idx].report_file = old_files[i]
                    continue
                filename = secure_filename(f.filename)
                if allowed_study_file(filename):
                    unique_name = f"study_{studies_created[idx].id}_{int(time.time())}.pdf"
                    save_upload(f, unique_name)
                    studies_created[idx].report_file = unique_name
                else:
                    flash("Solo se permiten archivos PDF para el reporte.", "danger")

        if 'func' in group_indices:
            start, count = group_indices['func']
            _save_file_for_study_filelist(func_files, start, count, 'func')
        if 'img' in group_indices:
            start, count = group_indices['img']
            _save_file_for_study_filelist(img_files, start, count, 'img')
        if 'inv' in group_indices:
            start, count = group_indices['inv']
            _save_file_for_study_filelist(inv_files, start, count, 'inv')

        db.session.commit()
        flash("Estudios actualizados correctamente.", "success")
        return redirect(url_for("patient_detail", patient_id=patient.id))

    # GET
    func_studies = []
    img_studies = []
    inv_studies = []
    other_studies = []
    if multi_mode:
        studies = Study.query.filter_by(consultation_id=consultation.id).all()
        func_studies = [s for s in studies if s.study_type in ["Espirometría", "Test de la Marcha 6m", "DLCO", "Volúmenes pulmonares"]]
        img_studies = [s for s in studies if s.study_type in ["TC Tórax", "RM Tórax", "PET-CT", "RX", "Ecografía", "Ecocardiograma", "Ecodoppler Angiopower"]]
        inv_studies = [s for s in studies if s.study_type in ["Fibrobroncoscopía", "Biopsia", "BAL", "Otro"]]
        other_studies = [s for s in studies if s not in func_studies + img_studies + inv_studies]

    return render_template(
        "study_edit.html",
        study=study,
        patient=patient,
        study_type_options=STUDY_TYPE_OPTIONS,
        center_options=CATALOGS.get("centers", []),
        center_links=CENTER_PORTAL_LINKS,
        sibling_studies=sibling_studies,
        multi_mode=multi_mode,
        func_studies=func_studies,
        img_studies=img_studies,
        inv_studies=inv_studies,
        other_studies=other_studies,
        func_calc_url=os.environ.get("FUNC_PROGRESS_CALC_URL"),
    )


@app.route("/studies/<int:study_id>/delete", methods=["POST"])
@login_required
def study_delete(study_id):
    study = Study.query.get_or_404(study_id)
    patient_id = study.patient_id
    
    # Solo el creador puede borrar
    if study.created_by_id != current_user.id:
        flash("No tienes permiso para borrar este estudio.", "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))
    
    # Borrar archivo PDF si existe
    if study.report_file:
        delete_upload(study.report_file)
    
    db.session.delete(study)
    db.session.commit()
    flash("Estudio eliminado correctamente.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


# --------------- CONSULTAS -----------------------


@app.route("/patients/<int:patient_id>/consultations/new", methods=["GET", "POST"])
@login_required
def consultation_new(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    review_users = get_review_recipient_options(current_user)

    if request.method == "POST":
        date = request.form.get("date")
        notes = request.form.get("notes")
        lab_general = (request.form.get("lab_general") or "").strip() or None
        lab_immunology = _serialize_list(request.form.getlist("lab_immunology"))
        lab_immunology_notes = (request.form.get("lab_immunology_notes") or "").strip() or None
        lab_immunology_values = {}
        for key, _ in IMMUNO_LAB_OPTIONS:
            val = (request.form.get(f"lab_immunology_value_{key}") or "").strip()
            if val:
                lab_immunology_values[key] = val

        if not date:
            flash("La fecha de la consulta es obligatoria.", "danger")
            return redirect(url_for("consultation_new", patient_id=patient.id))

        consultation = Consultation(
            patient=patient,
            date=date,
            notes=notes,
            lab_general=lab_general,
            lab_immunology=lab_immunology,
            lab_immunology_values=_serialize_kv(lab_immunology_values),
            lab_immunology_notes=lab_immunology_notes,
            created_by=current_user,
        )
        db.session.add(consultation)
        db.session.flush()  # para tener consultation.id

        study_groups = request.form.getlist("study_groups") or []
        studies_created = []

        def add_studies_from_lists(types, dates, centers=None, accesses=None, links=None, description=None):
            nonlocal studies_created
            max_len = len(types) if types else 0
            for idx in range(max_len):
                stype = (types[idx] or "").strip()
                sdate = (dates[idx] if dates and idx < len(dates) else "").strip()
                center = (centers[idx] if centers and idx < len(centers) else "").strip() if centers else ""
                access = (accesses[idx] if accesses and idx < len(accesses) else "").strip() if accesses else ""
                link = (links[idx] if links and idx < len(links) else "").strip() if links else ""
                if not any([stype, sdate, center, access, link]):
                    continue
                study = Study(
                    patient=patient,
                    consultation=consultation,
                    study_type=stype or "Estudio asociado a consulta",
                    date=sdate or date,
                    center=center or None,
                    description=description,
                    created_by=current_user,
                )
                study.access_code = access or None
                study.portal_link = link or None
                db.session.add(study)
                studies_created.append(study)

        # Datos por grupo (UI puede aportar múltiples filas por grupo)
        def _get_list(key):
            vals = request.form.getlist(key)
            if vals:
                return [v.strip() for v in vals]
            vals = request.form.getlist(f"{key}[]")
            return [v.strip() for v in vals] if vals else []

        def _get_files(key):
            files = request.files.getlist(key)
            if files:
                return files
            files = request.files.getlist(f"{key}[]")
            return files or []

        # Funcionales: tipos + fechas son arrays, descripción es compartida (string único)
        func_types = _get_list("study_type_func")
        func_dates = _get_list("study_date_func")
        func_desc = (request.form.get("study_description_func") or "").strip() or None
        func_files = _get_files("study_file_func")

        # Imágenes: tipos, fechas, centros, accesos y links son arrays, descripción es compartida
        img_types = _get_list("study_type_img")
        img_dates = _get_list("study_date_img")
        img_centers = _get_list("study_center_img")
        img_accesses = _get_list("study_access_code_img")
        img_links = _get_list("study_portal_link_img")
        img_desc = (request.form.get("study_description_img") or "").strip() or None
        img_files = _get_files("study_file_img")

        # Invasivos: tipos + fechas son arrays, descripción es compartida (string único)
        inv_types = _get_list("study_type_inv")
        inv_dates = _get_list("study_date_inv")
        inv_desc = (request.form.get("study_description_inv") or "").strip() or None
        inv_files = _get_files("study_file_inv")

        # control compartido
        control_enabled = request.form.get("control_enabled") in ("on", "true", "1")
        control_date = (request.form.get("control_date") or "").strip() or None
        control_extra_emails = (request.form.get("control_extra_emails") or "").strip() or None

        # Agregar estudios según selección múltiple
        group_indices = {}
        def add_studies_from_lists(types, dates, shared_desc, centers=None, accesses=None, links=None):
            """
            Crea un Study por cada (tipo, fecha) pair. Todos comparten la descripción.
            """
            nonlocal studies_created
            added = 0
            max_len = max(len(types) if types else 0, len(dates) if dates else 0)
            for idx in range(max_len):
                stype = (types[idx] if idx < len(types) else "") or ""
                stype = stype.strip()
                sdate = (dates[idx] if idx < len(dates) else "") or ""
                sdate = sdate.strip()
                center = (centers[idx] if centers and idx < len(centers) else "").strip() if centers else ""
                access = (accesses[idx] if accesses and idx < len(accesses) else "").strip() if accesses else ""
                link = (links[idx] if links and idx < len(links) else "").strip() if links else ""
                # skip empty type+date pair
                if not stype and not sdate:
                    continue
                study = Study(
                    patient=patient,
                    consultation=consultation,
                    study_type=stype or "Estudio asociado a consulta",
                    date=sdate or date,
                    center=center or None,
                    description=shared_desc or None,  # use shared description for all studies in group
                    created_by=current_user,
                )
                study.access_code = access or None
                study.portal_link = link or None
                db.session.add(study)
                studies_created.append(study)
                added += 1
            return added

        if "func" in study_groups:
            group_indices['func'] = (len(studies_created), add_studies_from_lists(func_types, func_dates, func_desc))
        if "img" in study_groups:
            group_indices['img'] = (len(studies_created), add_studies_from_lists(img_types, img_dates, img_desc, centers=img_centers, accesses=img_accesses, links=img_links))
        if "inv" in study_groups:
            group_indices['inv'] = (len(studies_created), add_studies_from_lists(inv_types, inv_dates, inv_desc))

        db.session.flush()

        # Asociar archivos por estudio (uno por fila). Si se sube un solo archivo para el grupo, lo asociamos al primer estudio.
        def _save_file_for_study_filelist(filelist, start_idx, count, group_name):
            if not filelist:
                return
            # if only 1 file but multiple studies, treat as group-level file -> first study
            if len(filelist) == 1 and count > 0:
                f = filelist[0]
                if f and getattr(f, "filename", ""):
                    filename = secure_filename(f.filename)
                    if allowed_study_file(filename):
                        idx = start_idx
                        if idx < len(studies_created):
                            unique_name = f"study_{studies_created[idx].id}_{int(time.time())}.pdf"
                            save_upload(f, unique_name)
                            studies_created[idx].report_file = unique_name
                    else:
                        flash("Solo se permiten archivos PDF para el reporte.", "danger")
                return
            # otherwise map files by index
            for i in range(count):
                idx = start_idx + i
                if idx >= len(studies_created):
                    break
                f = filelist[i] if i < len(filelist) else None
                if not f or not getattr(f, "filename", ""):
                    continue
                filename = secure_filename(f.filename)
                if allowed_study_file(filename):
                    unique_name = f"study_{studies_created[idx].id}_{int(time.time())}.pdf"
                    save_upload(f, unique_name)
                    studies_created[idx].report_file = unique_name
                else:
                    flash("Solo se permiten archivos PDF para el reporte.", "danger")

        if 'func' in group_indices:
            start, count = group_indices['func']
            _save_file_for_study_filelist(func_files, start, count, 'func')
        if 'img' in group_indices:
            start, count = group_indices['img']
            _save_file_for_study_filelist(img_files, start, count, 'img')
        if 'inv' in group_indices:
            start, count = group_indices['inv']
            _save_file_for_study_filelist(inv_files, start, count, 'inv')

        # Solicitar control (si se solicitó y al menos un grupo aplicable fue seleccionado)
        cr = None
        if control_enabled and control_date and any(g in study_groups for g in ("func", "img")):
            cr = ControlReminder(
                patient=patient,
                consultation=consultation,
                control_date=control_date,
                extra_emails=control_extra_emails,
                created_by=current_user,
            )
            db.session.add(cr)

        # Solicitud de revisión (asociamos el primer estudio del grupo si existe)
        review_recips = _parse_recipient_ids(request.form.getlist("review_recipients"))
        review_message = request.form.get("review_message")
        study_for_review = studies_created[0] if studies_created else None
        if review_recips:
            create_review_request(
                patient,
                current_user,
                review_recips,
                review_message,
                consultation=consultation,
                study=study_for_review,
            )

        db.session.commit()
        if cr:
            notify_control_creation(cr, patient)
        flash("Consulta agregada correctamente.", "success")
        return redirect(url_for("patient_detail", patient_id=patient.id))

    return render_template(
        "consultation_new.html",
        patient=patient,
        review_users=review_users,
        center_links=CENTER_PORTAL_LINKS,
        immuno_options=IMMUNO_LAB_OPTIONS,
        immuno_core_options=IMMUNO_LAB_CORE_OPTIONS,
        immuno_rheum_options=IMMUNO_LAB_RHEUM_OPTIONS,
        study_type_options=STUDY_TYPE_OPTIONS,
        center_options=CATALOGS.get("centers", []),
        # optional external calculator for functional tests (configure via env FUNC_PROGRESS_CALC_URL)
        func_calc_url=os.environ.get("FUNC_PROGRESS_CALC_URL"),
    )


@app.route("/consultations/<int:consultation_id>/view")
@login_required
def consultation_view(consultation_id):
    consultation = Consultation.query.get_or_404(consultation_id)
    patient = consultation.patient
    studies = Study.query.filter_by(consultation_id=consultation_id).order_by(Study.date.desc()).all()
    return render_template(
        "consultation_view.html",
        consultation=consultation,
        patient=patient,
        studies=studies,
        immuno_map=IMMUNO_LAB_DICT,
        immuno_values=_deserialize_kv(consultation.lab_immunology_values),
    )


@app.route("/consultations/<int:consultation_id>/edit", methods=["GET", "POST"])
@login_required
def consultation_edit(consultation_id):
    consultation = Consultation.query.get_or_404(consultation_id)
    patient = consultation.patient
    review_users = get_review_recipient_options(current_user)
    
    # Solo el creador puede editar
    if consultation.created_by_id != current_user.id:
        flash("No tienes permiso para editar esta consulta.", "danger")
        return redirect(url_for("consultation_view", consultation_id=consultation.id))
    
    if request.method == "POST":
        # Actualizar datos básicos
        consultation.date = request.form.get("date") or None
        consultation.notes = request.form.get("notes") or None
        consultation.lab_general = request.form.get("lab_general") or None
        consultation.lab_immunology_notes = request.form.get("lab_immunology_notes") or None
        
        # Actualizar laboratorio de inmunología (checkboxes + valores)
        immuno_list = request.form.getlist("lab_immunology")
        consultation.lab_immunology = ",".join(immuno_list) if immuno_list else None
        
        # Capturar valores de inmunología
        immuno_values = {}
        for key in immuno_list:
            val = request.form.get(f"lab_immunology_value_{key}", "").strip()
            if val:
                immuno_values[key] = val
        consultation.lab_immunology_values = _serialize_kv(immuno_values)
        
        # Guardar estudios existentes ANTES de borrar (para preservar archivos PDF por orden)
        existing_studies = Study.query.filter_by(consultation_id=consultation_id).order_by(Study.id).all()
        existing_files_by_type = {
            'func': [],
            'img': [],
            'inv': []
        }
        # Separar archivos por tipo de estudio que tenían
        for study in existing_studies:
            if study.report_file:
                if study.study_type in ["Espirometría", "Test de la Marcha 6m", "DLCO", "Volúmenes pulmonares"]:
                    existing_files_by_type['func'].append(study.report_file)
                elif study.study_type in ["TC Tórax", "RM Tórax", "PET-CT", "RX", "Ecografía", "Ecocardiograma", "Ecodoppler Angiopower"]:
                    existing_files_by_type['img'].append(study.report_file)
                elif study.study_type in ["Fibrobroncoscopía", "Biopsia", "BAL", "Otro"]:
                    existing_files_by_type['inv'].append(study.report_file)
        
        # Eliminar estudios existentes para esta consulta y recrearlos
        Study.query.filter_by(consultation_id=consultation_id).delete()
        db.session.flush()
        
        # Procesar grupos de estudios (igual que en consultation_new)
        study_groups = request.form.getlist("study_groups") or []
        studies_created = []
        
        def _get_list(key):
            vals = request.form.getlist(key)
            if vals:
                return [v.strip() for v in vals]
            vals = request.form.getlist(f"{key}[]")
            return [v.strip() for v in vals] if vals else []
        
        def _get_files(key):
            files = request.files.getlist(key)
            if files:
                return files
            files = request.files.getlist(f"{key}[]")
            return files or []
        
        # Funcionales
        func_types = _get_list("study_type_func")
        func_dates = _get_list("study_date_func")
        func_desc = (request.form.get("study_description_func") or "").strip() or None
        func_files = _get_files("study_file_func")
        
        # Imágenes
        img_types = _get_list("study_type_img")
        img_dates = _get_list("study_date_img")
        img_centers = _get_list("study_center_img")
        img_accesses = _get_list("study_access_code_img")
        img_links = _get_list("study_portal_link_img")
        img_desc = (request.form.get("study_description_img") or "").strip() or None
        img_files = _get_files("study_file_img")
        
        # Invasivos
        inv_types = _get_list("study_type_inv")
        inv_dates = _get_list("study_date_inv")
        inv_desc = (request.form.get("study_description_inv") or "").strip() or None
        inv_files = _get_files("study_file_inv")
        
        def add_studies_from_lists(types, dates, shared_desc, centers=None, accesses=None, links=None):
            nonlocal studies_created
            added = 0
            max_len = max(len(types) if types else 0, len(dates) if dates else 0)
            for idx in range(max_len):
                stype = (types[idx] if idx < len(types) else "") or ""
                stype = stype.strip()
                sdate = (dates[idx] if idx < len(dates) else "") or ""
                sdate = sdate.strip()
                center = (centers[idx] if centers and idx < len(centers) else "").strip() if centers else ""
                access = (accesses[idx] if accesses and idx < len(accesses) else "").strip() if accesses else ""
                link = (links[idx] if links and idx < len(links) else "").strip() if links else ""
                if not stype and not sdate:
                    continue
                study = Study(
                    patient=patient,
                    consultation=consultation,
                    study_type=stype or "Estudio asociado a consulta",
                    date=sdate or consultation.date,
                    center=center or None,
                    description=shared_desc or None,
                    created_by=current_user,
                )
                study.access_code = access or None
                study.portal_link = link or None
                db.session.add(study)
                studies_created.append(study)
                added += 1
            return added
        
        group_indices = {}
        if "func" in study_groups:
            group_indices['func'] = (len(studies_created), add_studies_from_lists(func_types, func_dates, func_desc))
        if "img" in study_groups:
            group_indices['img'] = (len(studies_created), add_studies_from_lists(img_types, img_dates, img_desc, centers=img_centers, accesses=img_accesses, links=img_links))
        if "inv" in study_groups:
            group_indices['inv'] = (len(studies_created), add_studies_from_lists(inv_types, inv_dates, inv_desc))
        
        db.session.flush()
        
        # Guardar archivos para estudios
        def _save_file_for_study_filelist(filelist, start_idx, count, group_name):
            if not filelist:
                # Si no hay archivos nuevos, intentar preservar los antiguos por índice
                if group_name in existing_files_by_type:
                    old_files = existing_files_by_type[group_name]
                    for i in range(count):
                        idx = start_idx + i
                        if idx < len(studies_created) and i < len(old_files):
                            studies_created[idx].report_file = old_files[i]
                return
            if len(filelist) == 1 and count > 0:
                f = filelist[0]
                if f and getattr(f, "filename", ""):
                    filename = secure_filename(f.filename)
                    if allowed_study_file(filename):
                        idx = start_idx
                        if idx < len(studies_created):
                            unique_name = f"study_{studies_created[idx].id}_{int(time.time())}.pdf"
                            save_upload(f, unique_name)
                            studies_created[idx].report_file = unique_name
                    else:
                        flash("Solo se permiten archivos PDF para el reporte.", "danger")
                else:
                    # Archivo vacío pero existe old file, preservar
                    if group_name in existing_files_by_type and existing_files_by_type[group_name]:
                        old_files = existing_files_by_type[group_name]
                        idx = start_idx
                        if idx < len(studies_created) and idx < len(old_files):
                            studies_created[idx].report_file = old_files[idx]
                return
            for i in range(count):
                idx = start_idx + i
                if idx >= len(studies_created):
                    break
                f = filelist[i] if i < len(filelist) else None
                if not f or not getattr(f, "filename", ""):
                    # Si no se subió archivo nuevo pero existe uno anterior, preservarlo por índice
                    if group_name in existing_files_by_type:
                        old_files = existing_files_by_type[group_name]
                        if i < len(old_files):
                            studies_created[idx].report_file = old_files[i]
                    continue
                filename = secure_filename(f.filename)
                if allowed_study_file(filename):
                    unique_name = f"study_{studies_created[idx].id}_{int(time.time())}.pdf"
                    save_upload(f, unique_name)
                    studies_created[idx].report_file = unique_name
                else:
                    flash("Solo se permiten archivos PDF para el reporte.", "danger")
        
        if 'func' in group_indices:
            start, count = group_indices['func']
            _save_file_for_study_filelist(func_files, start, count, 'func')
        if 'img' in group_indices:
            start, count = group_indices['img']
            _save_file_for_study_filelist(img_files, start, count, 'img')
        if 'inv' in group_indices:
            start, count = group_indices['inv']
            _save_file_for_study_filelist(inv_files, start, count, 'inv')
        
        db.session.commit()
        
        # Solicitud de revisión (si se especificaron destinatarios)
        review_recips = _parse_recipient_ids(request.form.getlist("review_recipients"))
        review_message = request.form.get("review_message")
        study_for_review = studies_created[0] if studies_created else None
        if review_recips:
            create_review_request(
                patient,
                current_user,
                review_recips,
                review_message,
                consultation=consultation,
                study=study_for_review,
            )
        
        flash("Consulta actualizada correctamente.", "success")
        return redirect(url_for("consultation_view", consultation_id=consultation.id))
    
    # GET: pre-cargar datos
    immuno_values = _deserialize_kv(consultation.lab_immunology_values)
    immuno_selected = consultation.lab_immunology.split(",") if consultation.lab_immunology else []
    
    # Separar estudios por tipo para cargar en el template
    studies = Study.query.filter_by(consultation_id=consultation_id).all()
    
    func_studies = [s for s in studies if s.study_type in ["Espirometría", "Test de la Marcha 6m", "DLCO", "Volúmenes pulmonares"]]
    img_studies = [s for s in studies if s.study_type in ["TC Tórax", "RM Tórax", "PET-CT", "RX", "Ecografía", "Ecocardiograma", "Ecodoppler Angiopower"]]
    inv_studies = [s for s in studies if s.study_type in ["Fibrobroncoscopía", "Biopsia", "BAL", "Otro"]]
    other_studies = [s for s in studies if s not in func_studies + img_studies + inv_studies]
    
    return render_template(
        "consultation_edit.html",
        consultation=consultation,
        patient=patient,
        review_users=review_users,
        center_links=CENTER_PORTAL_LINKS,
        immuno_options=IMMUNO_LAB_OPTIONS,
        immuno_core_options=IMMUNO_LAB_CORE_OPTIONS,
        immuno_rheum_options=IMMUNO_LAB_RHEUM_OPTIONS,
        study_type_options=STUDY_TYPE_OPTIONS,
        center_options=CATALOGS.get("centers", []),
        func_calc_url=os.environ.get("FUNC_PROGRESS_CALC_URL"),
        immuno_values=immuno_values,
        immuno_selected=immuno_selected,
        func_studies=func_studies,
        img_studies=img_studies,
        inv_studies=inv_studies,
        other_studies=other_studies,
    )


@app.route("/consultations/<int:consultation_id>/delete", methods=["POST"])
@login_required
def consultation_delete(consultation_id):
    consultation = Consultation.query.get_or_404(consultation_id)
    patient_id = consultation.patient_id
    
    # Solo el creador puede borrar
    if consultation.created_by_id != current_user.id:
        flash("No tienes permiso para borrar esta consulta.", "danger")
        return redirect(url_for("consultation_view", consultation_id=consultation.id))
    
    db.session.delete(consultation)
    db.session.commit()
    flash("Consulta eliminada correctamente.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


# -------------------------------------------------
# CONTEXTO GLOBAL EXTRA (badge revisiones)
# -------------------------------------------------

@app.context_processor
def inject_review_helpers():
    """Badge de revisiones pendientes (robusto)."""
    try:
        count = get_pending_reviews_count_for_user(current_user)
    except Exception:
        count = 0
    return {
        "pending_reviews_count": count,
        "get_pending_reviews_count_for_user": get_pending_reviews_count_for_user,
    }


# -------------------------------------------------
# MAIN
# -------------------------------------------------

if __name__ == "__main__":
    with app.app_context():
        create_tables_and_admin()
        backup_database_if_changed()
    
    # Iniciar el scheduler
    if not scheduler.running:
        scheduler.start()
        print("[INFO] Scheduler iniciado - recordatorios automáticos activados")
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
