# Enqueuer — Cloud Tasks Dispatcher for Cloud Run (FastAPI)

Servicio FastAPI mínimo que **crea Cloud Tasks** para llamar a tus **workers en Cloud Run** con **requests HTTP POST firmados con OIDC**.

Diseñado para los servicios de TMLF:

- `brain`
- `testimonios`
- `transcripciones`
- `regresos` (Back-Questions PDF → Texto → Vertex → Google Docs)

Cada servicio tiene su propia **cola**, **URL**, **audience** y **deadline** configurables por variables de entorno.

---

## ✨ Qué hace

- Expone `POST /enqueue` para **crear una Cloud Task**.
- Cada task ejecuta un **HTTP POST** contra un worker en Cloud Run con:
  - `Content-Type: application/json`
  - `X-Idempotency-Key: <uuid>` (o el que tú mandes en `idempotency_key`)
  - **Token OIDC** firmado por una **service account** (en este setup usamos la misma SA del Enqueuer: `enqueuer-api@...`).
- Cada worker tiene:
  - Una cola en Cloud Tasks (`QUEUE_*`)
  - Una URL de endpoint (`URL_*`)
  - Un audience para OIDC (`AUD_*`)
  - Un tiempo máximo de ejecución (`DEADLINE_*_S`)
- Soporta:
  - `delay_s`: programar la ejecución en N segundos.
  - `idempotency_key`: nombre estable de la tarea para evitar duplicados.

---

## 🧱 Arquitectura (alto nivel)

```text
Client → Enqueuer (Cloud Run, SA: enqueuer-api@...)
        └── Cloud Tasks (queue-...)
             └── Worker (Cloud Run: brain | testimonios | transcripciones | regresos)
                 ← HTTP POST + OIDC (service_account_email = enqueuer-api@...)
````

* El cliente solo llama al **Enqueuer**.
* El Enqueuer **no ejecuta el trabajo pesado**: solo crea tasks.
* Cloud Tasks se encarga de:

  * Reintentos.
  * Programación (delay/schedule).
  * Llamar al worker en Cloud Run con token OIDC.

---

## 🗂 Project layout

```text
enqueuer/
├─ app.py              # FastAPI + Cloud Tasks client + routing por servicio
├─ requirements.txt
└─ Dockerfile
```

---

## ⚙️ Variables de entorno

Mínimas:

* `PROJECT_ID`: id del proyecto GCP (ej. `ortega-473114`)
* `TASKS_REGION`: región de Cloud Tasks (ej. `us-central1`)
* `CALLER_SA`: **service account que se usará en el token OIDC**
  En este setup: `enqueuer-api@<PROJECT_ID>.iam.gserviceaccount.com`

Por servicio:

```text
QUEUE_BRAIN=queue-brain
QUEUE_TESTI=queue-testimonios
QUEUE_TRANS=queue-transcripciones
QUEUE_REGRESOS=queue-regresos

URL_BRAIN=https://brain-...a.run.app/process
URL_TESTI=https://testimonios-...a.run.app/generate-testimony
URL_TRANS=https://transcripciones-...a.run.app/api/transcribe
URL_REGRESOS=https://regresos-...a.run.app/_tasks/process-pdf-back-questions-run

AUD_BRAIN=https://brain-...a.run.app
AUD_TESTI=https://testimonios-...a.run.app
AUD_TRANS=https://transcripciones-...a.run.app
AUD_REGRESOS=https://regresos-...a.run.app

DEADLINE_BRAIN_S=700
DEADLINE_TESTI_S=390
DEADLINE_TRANS_S=880
DEADLINE_REGRESOS_S=1800   # ~30 min para PDFs grandes
```

> **Nota:**
>
> * `URL_*` incluye el **path completo** del endpoint del worker.
> * `AUD_*` es **solo la base URL** (sin path), que debe coincidir con la URL de Cloud Run para el audience del token OIDC.
> * `DEADLINE_REGRESOS_S` está alto (1800s) porque `regresos` procesa PDFs grandes (8–25 min típicamente).

---

## 🔐 IAM necesario

Supongamos:

```bash
PROJECT_ID="ortega-473114"
PROJECT_NUMBER="223080314602"
ENQUEUER_SA="enqueuer-api@${PROJECT_ID}.iam.gserviceaccount.com"
CALLER_SA="${ENQUEUER_SA}"  # usamos la misma
```

### 1) El Enqueuer puede crear tasks

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${ENQUEUER_SA}" \
  --role="roles/cloudtasks.enqueuer"
```

### 2) Cloud Tasks puede firmar tokens OIDC para `CALLER_SA`

```bash
gcloud iam service-accounts add-iam-policy-binding "$CALLER_SA" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudtasks.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project="$PROJECT_ID"
```

### 3) El propio Enqueuer puede “actuar como” `CALLER_SA` (actAs)

```bash
gcloud iam service-accounts add-iam-policy-binding "$CALLER_SA" \
  --member="serviceAccount:${CALLER_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --project="$PROJECT_ID"
```

> En este diseño, usamos la misma SA (`enqueuer-api@...`) como:
>
> * Identidad del servicio Cloud Run `enqueuer`.
> * Identidad del token OIDC que usa Cloud Tasks (`CALLER_SA`).

### 4) Workers permiten el token OIDC (opcional si son públicos)

Si en algún momento cierras los workers (quitando `--allow-unauthenticated`), debes darles `run.invoker` a `CALLER_SA`:

```bash
for SVC in brain testimonios transcripciones regresos; do
  gcloud run services add-iam-policy-binding "$SVC" \
    --region us-central1 \
    --member="serviceAccount:${CALLER_SA}" \
    --role="roles/run.invoker" \
    --project="$PROJECT_ID"
done
```

---

## 🏃 Desarrollo local

Requisitos:

* Python 3.11
* `GOOGLE_APPLICATION_CREDENTIALS` apuntando a la key JSON de `enqueuer-api@...`.
* `gcloud` configurado con acceso al proyecto.

Instalación:

```bash
python -m venv venv
source venv/bin/activate   # En Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Variables mínimas en local (PowerShell):

```powershell
$Env:GOOGLE_APPLICATION_CREDENTIALS = "C:\ruta\a\enqueuer-api-key.json"
$Env:PROJECT_ID    = "ortega-473114"
$Env:TASKS_REGION  = "us-central1"
$Env:CALLER_SA     = "enqueuer-api@ortega-473114.iam.gserviceaccount.com"

$Env:QUEUE_BRAIN   = "queue-brain"
$Env:QUEUE_TESTI   = "queue-testimonios"
$Env:QUEUE_TRANS   = "queue-transcripciones"
$Env:QUEUE_REGRESOS = "queue-regresos"

$Env:URL_BRAIN     = "https://brain-...a.run.app/process"
$Env:URL_TESTI     = "https://testimonios-...a.run.app/generate-testimony"
$Env:URL_TRANS     = "https://transcripciones-...a.run.app/api/transcribe"
$Env:URL_REGRESOS  = "https://regresos-...a.run.app/_tasks/process-pdf-back-questions-run"

$Env:AUD_BRAIN     = "https://brain-...a.run.app"
$Env:AUD_TESTI     = "https://testimonios-...a.run.app"
$Env:AUD_TRANS     = "https://transcripciones-...a.run.app"
$Env:AUD_REGRESOS  = "https://regresos-...a.run.app"

$Env:DEADLINE_BRAIN_S  = "700"
$Env:DEADLINE_TESTI_S  = "390"
$Env:DEADLINE_TRANS_S  = "880"
$Env:DEADLINE_REGRESOS_S = "1800"

uvicorn app:app --reload --port 8080
```

---

## 🚀 Deploy a Cloud Run (PowerShell)

Ejemplo de script para build + deploy:

```powershell
$PROJECT_ID = "ortega-473114"
$REGION     = "us-central1"
$SERVICE    = "enqueuer"
$TAG        = (Get-Date -Format "yyyyMMdd-HHmmss")
$IMAGE      = "us-central1-docker.pkg.dev/$PROJECT_ID/ai/${SERVICE}:$TAG"

# gcloud auth login
# gcloud config set project $PROJECT_ID

Write-Host ">> Build & Push imagen: $IMAGE" -ForegroundColor Cyan
gcloud builds submit --tag $IMAGE

Write-Host ">> Deploy a Cloud Run servicio: $SERVICE" -ForegroundColor Cyan
gcloud run deploy $SERVICE `
  --image $IMAGE `
  --region $REGION `
  --platform managed `
  --service-account "enqueuer-api@$PROJECT_ID.iam.gserviceaccount.com" `
  --allow-unauthenticated `
  --memory 512Mi `
  --cpu 1 `
  --concurrency 80 `
  --timeout 60 `
  --set-env-vars "PROJECT_ID=$PROJECT_ID,TASKS_REGION=$REGION,CALLER_SA=enqueuer-api@$PROJECT_ID.iam.gserviceaccount.com,QUEUE_BRAIN=queue-brain,QUEUE_TESTI=queue-testimonios,QUEUE_TRANS=queue-transcripciones,QUEUE_REGRESOS=queue-regresos,URL_BRAIN=https://brain-pahip4iobq-uc.a.run.app/process,URL_TESTI=https://testimonios-pahip4iobq-uc.a.run.app/generate-testimony,URL_TRANS=https://transcripciones-pahip4iobq-uc.a.run.app/api/transcribe,URL_REGRESOS=https://regresos-223080314602.us-central1.run.app/_tasks/process-pdf-back-questions-run,AUD_BRAIN=https://brain-pahip4iobq-uc.a.run.app,AUD_TESTI=https://testimonios-pahip4iobq-uc.a.run.app,AUD_TRANS=https://transcripciones-...a.run.app,AUD_REGRESOS=https://regresos-223080314602.us-central1.run.app,DEADLINE_BRAIN_S=700,DEADLINE_TESTI_S=390,DEADLINE_TRANS_S=880,DEADLINE_REGRESOS_S=1800,APP_VERSION=$TAG"
```

---

## 📮 API

### `POST /enqueue`

**Body**

```json
{
  "service": "brain | testimonios | transcripciones | regresos",
  "payload": { "any": "JSON enviado al worker" },
  "delay_s": 0,
  "idempotency_key": "CASE-001-20251106T1615"
}
```

* `service`: uno de los keys definidos en `SERVICES` (`brain`, `testimonios`, `transcripciones`, `regresos`).
* `payload`: se envía **tal cual** al worker (sin modificarlo).
* `delay_s` (opcional): programa la ejecución N segundos en el futuro.
* `idempotency_key` (opcional): si se repite mientras exista una task con ese nombre, Cloud Tasks rechaza la duplicada.

**Respuesta**

```json
{
  "ok": true,
  "task": "projects/.../locations/.../queues/.../tasks/...",
  "service": "regresos",
  "queue": "queue-regresos",
  "deadline_s": 1800,
  "idempotency_key": "CASE-001-20251106T1615"
}
```

---

## 🆕 Ejemplo de payload para `regresos` (Back-Questions)

El worker `regresos` espera un payload tipo `TaskRunBackQuestionsPayload`. Ejemplo real:

```json
{
  "service": "regresos",
  "payload": {
    "system_instructions_doc_id": "1WLo-dBiXU8Cd7uBPZcY_qt9QwmzbWiuae0wEfKu5ERY",
    "base_prompt_doc_id": "1w64h4PmvmaHLImjVqT6R6be8kItQRyU5xBmB9YVoFZs",
    "pdf_url": "https://drive.google.com/file/d/1E3sC9rhEG_pGYwwCXxOcklJtlDstOLEn/view",
    "drive_file_id": "1E3sC9rhEG_pGYwwCXxOcklJtlDstOLEn",
    "output_doc_id": "1eG5Cmxl19qVC0dIIYqbQnUc94DuiefHhW_LfVXOLmMQ",
    "sampling_first_pages": 0,
    "sampling_last_pages": 3,
    "sheet_id": "1HwFByvIxfL8gLl0vY-QBz0rnqCZuh6H2e7E_wXhQXko",
    "row": 359,
    "col": 5,
    "additional_params": {
      "visa_type": "visa t",
      "strategy": "hybrid"
    }
  },
  "idempotency_key": "TEST-REGRESOS-001"
}
```

Ejemplo de prueba local:

```bash
curl -X POST "http://127.0.0.1:8080/enqueue" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "regresos",
    "payload": {
      "system_instructions_doc_id":"1WLo-dBiXU8Cd7uBPZcY_qt9QwmzbWiuae0wEfKu5ERY",
      "base_prompt_doc_id":"1w64h4PmvmaHLImjVqT6R6be8kItQRyU5xBmB9YVoFZs",
      "pdf_url":"https://drive.google.com/file/d/1E3sC9rhEG_pGYwwCXxOcklJtlDstOLEn/view",
      "drive_file_id":"1E3sC9rhEG_pGYwwCXxOcklJtlDstOLEn",
      "output_doc_id":"1eG5Cmxl19qVC0dIIYqbQnUc94DuiefHhW_LfVXOLmMQ",
      "sampling_first_pages": 0,
      "sampling_last_pages": 3,
      "sheet_id": "1HwFByvIxfL8gLl0vY-QBz0rnqCZuh6H2e7E_wXhQXko",
      "row": 359,
      "col": 5,
      "additional_params":{
        "visa_type": "visa t",
        "strategy": "hybrid"
      }
    },
    "idempotency_key": "TEST-REGRESOS-001"
  }'
```

---

## 🧰 Troubleshooting

* **403 iam.serviceAccounts.actAs**:

  * Falta `roles/iam.serviceAccountUser` para `CALLER_SA` (sobre sí misma o sobre la SA configurada).
  * Falta `roles/iam.serviceAccountTokenCreator` para la SA `service-<PROJECT_NUMBER>@gcp-sa-cloudtasks.iam.gserviceaccount.com` sobre `CALLER_SA`.

* **Task se crea pero worker no responde**:

  * Revisar Cloud Tasks → queue → task → última ejecución (HTTP status, respuesta).
  * Verificar que `URL_*` apunta al endpoint correcto y que el servicio está `Ready`.

* **401/403 desde el worker** (cuando cierres `--allow-unauthenticated`):

  * Asegúrate de que `CALLER_SA` tenga `roles/run.invoker` sobre el servicio Cloud Run.

* **Deadline demasiado corto**:

  * Aumenta `DEADLINE_*_S` (máx. ~1800s para HTTP).
  * Asegúrate de que el `timeoutSeconds` del servicio Cloud Run sea ≥ a ese valor.

---

## 🧾 License

MIT

```

::contentReference[oaicite:0]{index=0}
