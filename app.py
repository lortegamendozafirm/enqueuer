import os
import json
import uuid
import base64
import time
from typing import Any, Dict, Optional, Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from google.cloud import tasks_v2

PROJECT_ID = os.environ["PROJECT_ID"]
TASKS_REGION = os.environ.get("TASKS_REGION", "us-central1")
CALLER_SA = os.environ["CALLER_SA"]  # tasks-dispatcher@....

# Config por servicio (1 cola + 1 endpoint)
SERVICES = {
    "brain": {
        "queue": os.environ.get("QUEUE_BRAIN", "queue-brain"),
        "url":   os.environ.get("URL_BRAIN", "https://brain-pahip4iobq-uc.a.run.app/process"),
        "aud":   os.environ.get("AUD_BRAIN", "https://brain-pahip4iobq-uc.a.run.app"),
        "deadline_s": int(os.environ.get("DEADLINE_BRAIN_S", "700")),
    },
    "testimonios": {
        "queue": os.environ.get("QUEUE_TESTI", "queue-testimonios"),
        "url":   os.environ.get("URL_TESTI", "https://testimonios-pahip4iobq-uc.a.run.app/generate-testimony"),
        "aud":   os.environ.get("AUD_TESTI", "https://testimonios-pahip4iobq-uc.a.run.app"),
        "deadline_s": int(os.environ.get("DEADLINE_TESTI_S", "390")),
    },
    "transcripciones": {
        "queue": os.environ.get("QUEUE_TRANS", "queue-transcripciones"),
        "url":   os.environ.get("URL_TRANS", "https://transcripciones-pahip4iobq-uc.a.run.app/api/transcribe"),
        "aud":   os.environ.get("AUD_TRANS", "https://transcripciones-pahip4iobq-uc.a.run.app"),
        "deadline_s": int(os.environ.get("DEADLINE_TRANS_S", "880")),
    },
}

app = FastAPI(title="Ortega Enqueuer API")

class EnqueueRequest(BaseModel):
    service: Literal["brain", "testimonios", "transcripciones"]
    payload: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    # Opcionales:
    delay_s: Optional[int] = Field(default=None, ge=0, description="Programar a futuro")
    deadline_s: Optional[int] = Field(default=None, ge=1, le=3600, description="dispatchDeadline override")

def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")

@app.post("/enqueue")
def enqueue(req: EnqueueRequest):
    if req.service not in SERVICES:
        raise HTTPException(status_code=422, detail="service inválido")

    cfg = SERVICES[req.service]
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(PROJECT_ID, TASKS_REGION, cfg["queue"])

    idem = req.idempotency_key or str(uuid.uuid4())
    body_json = json.dumps(req.payload, ensure_ascii=False)

    http_request = {
        "http_method": tasks_v2.HttpMethod.POST,
        "url": cfg["url"],
        "headers": {
            "Content-Type": "application/json",
            "X-Idempotency-Key": idem
        },
        "body": body_json.encode("utf-8"),
        "oidc_token": {
            "service_account_email": CALLER_SA,
            "audience": cfg["aud"],
        }
    }

    task: Dict[str, Any] = { "http_request": http_request }

    # Programación futura (opcional)
    if req.delay_s and req.delay_s > 0:
        task["schedule_time"] = {"seconds": int(time.time()) + req.delay_s}

    # Deadline (si quieres override por request)
    deadline_s = req.deadline_s or cfg["deadline_s"]
    # Solo disponible en la API REST; el cliente lo acepta como dict:
    task["dispatch_deadline"] = f"{deadline_s}s"

    try:
        resp = client.create_task(request={"parent": parent, "task": task})
        return {
            "ok": True,
            "task": resp.name,
            "service": req.service,
            "queue": cfg["queue"],
            "deadline_s": deadline_s,
            "idempotency_key": idem
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
