# Comité Torax – Historia Clínica Web

[![Run tests](https://github.com/comitetoraxvm/comite-torax/actions/workflows/pytest.yml/badge.svg)](https://github.com/comitetoraxvm/comite-torax/actions/workflows/pytest.yml)

Aplicación Flask que permite a un pequeño grupo de médicos registrar pacientes, consultas y estudios de patología torácica. Incluye autenticación, aprobación de usuarios, ficha clínica estandarizada, filtros avanzados, consentimiento informado, auditoría y respaldos automáticos.

> Nota: Se agregó integración continua (tests) — re-ejecutando CI para verificar resultados.

## Pila tecnológica

- Python 3.11+/Flask
- Flask-Login, Flask-WTF, SQLAlchemy (SQLite o Postgres)
- HTML/CSS simples renderizados con Jinja2

## Requisitos

- Python ≥ 3.11
- Virtualenv recomendado

## Variables de entorno

| Variable       | Descripción                                              | Valor por defecto             |
| -------------- | -------------------------------------------------------- | ----------------------------- |
| `SECRET_KEY`   | Clave de sesión Flask/CSRF. Cambiar en producción.       | `cambia-esta-clave-por-una…` |
| `DATABASE_URL` | Cadena SQLAlchemy (Ej. `sqlite:///comite.db` / Postgres) | `sqlite:///comite.db`        |
| `UPLOAD_BUCKET` | Bucket de Google Cloud Storage para archivos (opcional). Si no se define, usa disco local. | *(vacío)* |
| `CLOUD_SQL_CONNECTION_NAME` | Nombre de conexión de Cloud SQL (`PROYECTO:REGION:INSTANCIA`). Si está definido usa el conector oficial. | *(vacío)* |
| `DB_USER` | Usuario de base de datos (Cloud SQL connector). | *(vacío)* |
| `DB_PASS` | Password de base de datos (Cloud SQL connector). | *(vacío)* |
| `DB_NAME` | Nombre de base de datos (Cloud SQL connector). | *(vacío)* |

Opcionalmente define `FLASK_APP=app.py` y `FLASK_ENV=production`.

## Configuración local

```bash
cd COMITE TORAX APP
python -m venv .venv
.venv\Scripts\activate        # Windows
python -m pip install -r requirements.txt
set SECRET_KEY=...            # o export en Linux/macOS
set DATABASE_URL=sqlite:///comite.db
set PYTHONIOENCODING=utf-8
python app.py
```

Se crea automáticamente la base `instance/comite.db`, un usuario admin (`admin` / `Admin2025!`) y un backup incremental en `backups/`.

## Características

- Registro/aprobación de usuarios (roles admin/médico).
- Ficha clínica completa con consentimiento informado, exposiciones, estudios, consultas y auditoría (`audit.log`).
- Filtros avanzados (centro, ciudad, edad, sexo, tabaquismo múltiple, patologías).
- Vista imprimible/anonimizable para reuniones.
- CSRF en todos los formularios y logging de acciones sensibles.

## Flujos de seguridad

- Contraseñas robustas (≥10 caracteres, mayúsculas/minúsculas/números/símbolos).
- Cambio de contraseña disponible en “Cambiar clave”.
- `audit.log` registra logins, cambios, impresiones y eliminaciones.
- Backups automáticos al detectar cambios en `instance/comite.db`.

## Despliegue sugerido

### Docker

```bash
docker compose build
docker compose up
```

### Google Cloud Run

**Resumen**
- Cloud Run inyecta `PORT`; el contenedor debe escuchar ese puerto.
- Define `SECRET_KEY` y `DATABASE_URL` como variables de entorno del servicio.

**1) Preparar GCP**
```bash
gcloud config set project TU_PROYECTO
gcloud services enable run.googleapis.com artifactregistry.googleapis.com
```

**2) Construir y subir imagen**
```bash
gcloud artifacts repositories create comite-torax \
  --repository-format=docker \
  --location=us-central1

gcloud builds submit \
  --tag us-central1-docker.pkg.dev/TU_PROYECTO/comite-torax/app:latest
```

**3) Base de datos**

Opción A: **Cloud SQL (Postgres)**
```bash
gcloud services enable sqladmin.googleapis.com
gcloud sql instances create comite-torax-db \
  --database-version=POSTGRES_14 \
  --region=us-central1
gcloud sql databases create comite --instance=comite-torax-db
gcloud sql users create comite_user --instance=comite-torax-db --password=TU_PASSWORD
```

Cadena SQLAlchemy para Cloud SQL:
```
postgresql+psycopg://comite_user:TU_PASSWORD@/comite?host=/cloudsql/TU_PROYECTO:us-central1:comite-torax-db
```

Si prefieres el **Connector oficial**, define variables en Cloud Run:
```
CLOUD_SQL_CONNECTION_NAME=TU_PROYECTO:us-central1:comite-torax-db
DB_USER=comite_user
DB_PASS=TU_PASSWORD
DB_NAME=comite
```

Opción B: **Postgres gestionado externo**
```
postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME
```

**4) Desplegar en Cloud Run**
```bash
gcloud run deploy comite-torax \
  --image us-central1-docker.pkg.dev/TU_PROYECTO/comite-torax/app:latest \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars SECRET_KEY=TU_SECRETO,DATABASE_URL="postgresql+psycopg://..." \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 3
```

Si usas **Cloud SQL**, agrega:
```bash
gcloud run deploy comite-torax \
  --image us-central1-docker.pkg.dev/TU_PROYECTO/comite-torax/app:latest \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars SECRET_KEY=TU_SECRETO,DATABASE_URL="postgresql+psycopg://..." \
  --add-cloudsql-instances TU_PROYECTO:us-central1:comite-torax-db
```

Si usas **Connector oficial** (recomendado):
```bash
gcloud run deploy comite-torax \
  --image us-central1-docker.pkg.dev/TU_PROYECTO/comite-torax/app:latest \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars SECRET_KEY=TU_SECRETO,CLOUD_SQL_CONNECTION_NAME=TU_PROYECTO:us-central1:comite-torax-db,DB_USER=comite_user,DB_PASS=TU_PASSWORD,DB_NAME=comite \
  --add-cloudsql-instances TU_PROYECTO:us-central1:comite-torax-db
```

**5) Notas importantes**
- `SECRET_KEY` debe ser aleatoria y segura (32+ caracteres).
- Cloud Run no preserva disco local; no uses SQLite en producción.
- `uploads/` y `instance/` deben ir a storage externo si los necesitás (Cloud Storage).

**5.1) Subidas de archivos (Cloud Storage)**

Cloud Run es efímero, por lo que los archivos deben persistir en un bucket.
La app usa `UPLOAD_BUCKET`: si está definido, guarda/lee desde GCS; si no, usa el directorio local `uploads/`.

Crear bucket:
```bash
gcloud storage buckets create gs://TU_BUCKET \
  --location=us-central1 \
  --uniform-bucket-level-access
```

Dar permisos al servicio de Cloud Run (sustituye `COMITE_SA`):
```bash
gcloud iam service-accounts create comite-torax-sa
gcloud projects add-iam-policy-binding TU_PROYECTO \
  --member="serviceAccount:comite-torax-sa@TU_PROYECTO.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

Deploy usando ese service account:
```bash
gcloud run deploy comite-torax \
  --image us-central1-docker.pkg.dev/TU_PROYECTO/comite-torax/app:latest \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars SECRET_KEY=TU_SECRETO,DATABASE_URL="postgresql+psycopg://...",UPLOAD_BUCKET=TU_BUCKET \
  --service-account comite-torax-sa@TU_PROYECTO.iam.gserviceaccount.com
```

Nota: en producción es recomendable usar siempre `UPLOAD_BUCKET`.

**6) Variables de entorno (resumen)**
- `SECRET_KEY`: clave de sesión.
- `DATABASE_URL`: cadena SQLAlchemy (Postgres recomendado).

### Manual / Gunicorn

1. Provisión de VPS o servicio (Ubuntu + Nginx) o PaaS (Render/Fly).
2. Configurar `SECRET_KEY` y `DATABASE_URL` (recomendado Postgres).
3. Instalar dependencias y ejecutar Gunicorn:
   ```bash
   gunicorn --bind 0.0.0.0:8000 app:app
   ```
4. Servir vía Nginx/traefik con HTTPS.
5. Programar respaldos (`backups/`) y rotación de `audit.log`.

Para ambientes con más usuarios, migra la base a Postgres/MySQL cambiando `DATABASE_URL` y ejecutando `flask db upgrade` (o `db.create_all()` según corresponda).

## Usuarios iniciales

- Admin creado automáticamente: `admin` / `Admin2025!`. Cambia la contraseña tras el primer login.

## Mantenimiento

- Revisa `catalogs.json` para actualizar listas (centros, patologías, exposiciones).
- Supervisa `audit.log` y limpia respaldos antiguos en `backups/`.
- Actualiza `requirements.txt` y ejecuta `pip install -r requirements.txt` al incorporar nuevas dependencias.

## Próximos pasos

- Dockerfile / docker-compose para despliegue reproducible. ✅
- Exportaciones anonimizadas (CSV/PDF). ✅ (CSV incluido)
- Recuperación de contraseñas por email.
- Landing pública (`landing/`) para presentar al comité y enlazar a la app.
- Recuperación de contraseñas por email.
- Landing pública (`landing/`) para presentar al comité y enlazar a la app.

## Guía de despliegue recomendado

1. **Infraestructura**
   - VPS (Ubuntu 22.04) en DigitalOcean, Hetzner o similar, o PaaS tipo Render/Fly.
   - Configura un registro DNS: `app.comite` para la app, `www.comite` o `landing` para el sitio informativo (carpeta `landing/`).

2. **Sistema operativo**
   - Instala Docker y Docker Compose o usa `python3-pip + venv`.
   - Crea un usuario no root para correr la app.

3. **Base de datos**
   - En desarrollo puedes seguir con SQLite.
   - En producción se recomienda un Postgres gestionado (ElephantSQL, RDS, etc.). Cambia `DATABASE_URL` a `postgresql+psycopg://...`.

4. **Aplicación**
   - Copia el código al servidor (Git, rsync o SFTP).
   - Exporta variables en `/etc/environment` o usa un `.env`:
     ```
     SECRET_KEY=… (mínimo 32 caracteres aleatorios)
     DATABASE_URL=postgresql+psycopg://user:pass@host/db
     ```
   - Arranca con `docker compose up -d` o `gunicorn --workers 3 --bind 0.0.0.0:8000 app:app`.

5. **Nginx + HTTPS**
   - Instala Nginx, crea un sitio que haga proxy a `localhost:8000`.
   - Emite certificados con Let's Encrypt/Certbot.
   - Sirve la carpeta `landing/` en otro server block (puede estar en el mismo Nginx) para la landing pública.

6. **Backups y auditoría**
   - Monta `instance/` y `backups/` fuera del contenedor (ya está en `docker-compose.yml`).
   - Programa una tarea cron (o script) que copie `instance/comite.db`, `backups/` y `audit.log` a un almacenamiento externo (S3, Google Drive, etc.).
   - Revisa y rota `audit.log` periódicamente.

7. **Monitoreo y actualizaciones**
   - Mantén el sistema actualizado (`apt upgrade`, `pip install -r requirements.txt`).
   - Revisa accesos y acciones en `audit.log`.
   - Cambia la contraseña del admin inicial y crea cuentas para cada médico (aprobación vía panel admin).
