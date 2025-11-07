# Enqueuer — Cloud Tasks Dispatcher for Cloud Run (FastAPI)

A minimal FastAPI service that **creates Cloud Tasks** to call your **worker services on Cloud Run** with **OIDC-signed requests**.  
Designed for TMLF’s trio of workers: `brain`, `testimonios`, and `transcripciones` — each with its own queue, URL, and dispatch deadline.

---

## ✨ What it does

- Exposes `POST /enqueue` to **create a Cloud Task**.
- Each task performs an **HTTP POST** to a target **Cloud Run worker** with:
  - `Content-Type: application/json`
  - `X-Idempotency-Key: <uuid>` (or your own if provided)
  - **OIDC token** signed by the **caller service account** `tasks-dispatcher@…` with **audience** = worker base URL.
- Per‑service **queue**, **audience**, and **deadline** are configurable via env vars.
- Optional **scheduling** (`schedule_in_s`) and **deduplication** (`idempotency_key`).

---

## 🧱 Architecture (high level)

```
Client → Enqueuer (Cloud Run, SA: enqueuer-api@...)
        └── create task on Cloud Tasks (queue-...)
             └── Cloud Tasks calls Worker (Cloud Run: brain|testimonios|transcripciones)
                 with OIDC token (signed as tasks-dispatcher@...) → Worker handles job
```

- **Enqueuer SA** (`enqueuer-api@...`) needs: `roles/cloudtasks.enqueuer` and ability to **actAs** the caller SA.
- **Caller SA** (`tasks-dispatcher@...`) needs: `roles/iam.serviceAccountTokenCreator` (for Cloud Tasks SA) and `roles/run.invoker` **on the workers**.

---

## 🗂 Project layout

```
enqueuer/
├─ app.py              # FastAPI service
├─ requirements.txt
└─ Dockerfile
```

---

## ⚙️ Environment Variables

Required:

- `PROJECT_ID`: GCP project id (e.g., `ortega-473114`)
- `TASKS_REGION`: Cloud Tasks region (e.g., `us-central1`)
- `CALLER_SA`: Service account **that signs OIDC tokens** (e.g., `tasks-dispatcher@...`)

Per‑service (defaults shown):

- `QUEUE_BRAIN=queue-brain`
- `QUEUE_TESTI=queue-testimonios`
- `QUEUE_TRANS=queue-transcripciones`

- `URL_BRAIN=https://brain-...a.run.app/process`
- `URL_TESTI=https://testimonios-...a.run.app/generate-testimony`
- `URL_TRANS=https://transcripciones-...a.run.app/api/transcribe`

- `AUD_BRAIN=https://brain-...a.run.app`
- `AUD_TESTI=https://testimonios-...a.run.app`
- `AUD_TRANS=https://transcripciones-...a.run.app`

- `DEADLINE_BRAIN_S=700`
- `DEADLINE_TESTI_S=390`
- `DEADLINE_TRANS_S=880`

> **Important:** The **audience must match exactly** the worker base URL you expect (scheme + host).

---

## 🔐 IAM prerequisites

```bash
PROJECT_ID="ortega-473114"
PROJECT_NUMBER="223080314602"
ENQUEUER_SA="enqueuer-api@${PROJECT_ID}.iam.gserviceaccount.com"
CALLER_SA="tasks-dispatcher@${PROJECT_ID}.iam.gserviceaccount.com"

# 1) Enqueuer can create tasks
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${ENQUEUER_SA}" \
  --role="roles/cloudtasks.enqueuer"

# 2) Enqueuer can 'actAs' the caller SA
gcloud iam service-accounts add-iam-policy-binding "$CALLER_SA" \
  --member="serviceAccount:${ENQUEUER_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --project="$PROJECT_ID"

# 3) Cloud Tasks SA can sign OIDC for the caller SA
gcloud iam service-accounts add-iam-policy-binding "$CALLER_SA" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudtasks.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project="$PROJECT_ID"

# 4) Workers must allow the caller SA to invoke
for SVC in brain testimonios transcripciones; do
  gcloud run services add-iam-policy-binding "$SVC" \
    --region us-central1 \
    --member="serviceAccount:${CALLER_SA}" \
    --role="roles/run.invoker" \
    --project="$PROJECT_ID"
done
```

---

## 🏃 Local development

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export PROJECT_ID="ortega-473114"
export TASKS_REGION="us-central1"
export CALLER_SA="tasks-dispatcher@${PROJECT_ID}.iam.gserviceaccount.com"

# Optional: set all per-service envs for local tests
export QUEUE_BRAIN="queue-brain"
export URL_BRAIN="https://brain-...a.run.app/process"
export AUD_BRAIN="https://brain-...a.run.app"
# ... (repeat for TESTI and TRANS)

uvicorn app:app --reload --port 8080
```

Test:

```bash
curl -s -X POST http://127.0.0.1:8080/enqueue \
  -H "Content-Type: application/json" \
  -d '{
    "service":"brain",
    "payload":{
      "system_instructions_doc_id":"<DOC>",
      "base_prompt_doc_id":"<DOC>",
      "input_doc_id":"<DOC>",
      "output_doc_id":"<DOC>"
    }
  }' | jq
```

---

## 🚀 Deploy to Cloud Run

```bash
PROJECT_ID="ortega-473114"
REGION="us-central1"
IMAGE="us-central1-docker.pkg.dev/${PROJECT_ID}/ai/enqueuer:latest"
SERVICE="enqueuer"
ENQUEUER_SA="enqueuer-api@${PROJECT_ID}.iam.gserviceaccount.com"
CALLER_SA="tasks-dispatcher@${PROJECT_ID}.iam.gserviceaccount.com"

# Build (if not already built)
gcloud builds submit --tag "$IMAGE"

# Deploy
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$ENQUEUER_SA" \
  --allow-unauthenticated \
  --platform=managed \
  --memory=512Mi --cpu=1 --concurrency=80 --timeout=60 \
  --set-env-vars=PROJECT_ID="${PROJECT_ID}",TASKS_REGION="us-central1",CALLER_SA="${CALLER_SA}" \
  --set-env-vars=QUEUE_BRAIN="queue-brain",QUEUE_TESTI="queue-testimonios",QUEUE_TRANS="queue-transcripciones" \
  --set-env-vars=URL_BRAIN="<brain>/process",URL_TESTI="<testimonios>/generate-testimony",URL_TRANS="<transcripciones>/api/transcribe" \
  --set-env-vars=AUD_BRAIN="<brain>",AUD_TESTI="<testimonios>",AUD_TRANS="<transcripciones>" \
  --set-env-vars=DEADLINE_BRAIN_S="700",DEADLINE_TESTI_S="390",DEADLINE_TRANS_S="880"
```

Get URL:

```bash
gcloud run services describe enqueuer \
  --region us-central1 --format='value(status.url)'
```

---

## 📮 API

### `POST /enqueue`

**Body**

```json
{
  "service": "brain | testimonios | transcripciones",
  "payload": { "any": "JSON sent to worker" },
  "schedule_in_s": 30,
  "idempotency_key": "CASE-001-20251106T1615"
}
```

**Response**

```json
{
  "ok": true,
  "task": "projects/.../queues/.../tasks/...",
  "service": "brain",
  "queue": "queue-brain",
  "deadline_s": 700,
  "idempotency_key": "CASE-001-20251106T1615"
}
```

Notes:
- `schedule_in_s` (optional) schedules the execution in N seconds.
- `idempotency_key` (optional) forces a **stable task name**; duplicates will be rejected by Cloud Tasks if the previous task still exists.

---

## ➕ Add a new worker or change I/O

### Add a new worker

1) **Create a queue** (or reuse one):

```bash
gcloud tasks queues create queue-myworker \
  --location=us-central1 \
  --max-dispatches-per-second=5 \
  --max-concurrent-dispatches=50
```

2) **Grant invoker** to `tasks-dispatcher@...` on the worker service:

```bash
gcloud run services add-iam-policy-binding myworker \
  --region us-central1 \
  --member="serviceAccount:tasks-dispatcher@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

3) **Set env vars** for the new worker in Enqueuer (URL/AUD/QUEUE/DEADLINE), and **extend `SERVICES`** in `app.py` with a new entry matching the `service` name you will send in `/enqueue`.

4) **Deploy** Enqueuer again.

### Change worker endpoint or payload schema

- Update `URL_*`/`AUD_*` env vars in Enqueuer (the **audience must match the new base URL**).
- If the **payload schema** changed, **no code change is required** in Enqueuer (it forwards your `payload` verbatim). Update only the client that calls `/enqueue` and the worker’s request model.
- Redeploy Enqueuer with the new env vars.

---

## 🛡 Security & migration tips

- During migration you can keep workers **public (`allUsers`)**; OIDC will still work. Later, **remove `allUsers`** and keep only `run.invoker` for `tasks-dispatcher@...`.
- The **OIDC audience must equal** the worker base URL (no path). Mismatch ⇒ `401/403` from Cloud Run.
- Prefer **`idempotency_key`** derived from your business key to avoid duplicate processing.

---

## 🧰 Troubleshooting

- **403 iam.serviceAccounts.actAs** when testing locally:
  - Ensure your **local principal** (or the Enqueuer SA in Cloud Run) has `roles/iam.serviceAccountUser` **on** `tasks-dispatcher@...`.
- **401/403 from worker**:
  - Check `roles/run.invoker` to `tasks-dispatcher@...` **on the worker**.
  - Verify `aud` env var equals the worker base URL.
- **Task created but worker not hit**:
  - Inspect Cloud Tasks → queue → task → **last attempt** details (HTTP status/response).
- **Deadline too small**:
  - Increase `DEADLINE_*_S` accordingly.

---

## 🧾 License

MIT
