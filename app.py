import os
import json
import uuid
import time
from typing import Any, Dict, Optional
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
from google.cloud import tasks_v2
from google.cloud import storage
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN ---
PROJECT_ID = os.environ["PROJECT_ID"]
TASKS_REGION = os.environ.get("TASKS_REGION", "us-central1")
CALLER_SA = os.environ["CALLER_SA"]

# Nombre del bucket y archivo (Defínelos en tus variables de entorno)
CONFIG_BUCKET_NAME = os.environ.get("CONFIG_BUCKET_NAME", "mi-bucket-de-configs-enqueuer")
CONFIG_FILE_NAME = os.environ.get("CONFIG_FILE_NAME", "services.json")

# --- GESTOR DE CONFIGURACIÓN (CON CACHÉ) ---
class ConfigManager:
    def __init__(self):
        self._config = {}
        self._last_loaded = datetime.min
        self._cache_ttl = timedelta(minutes=5) # Recargar cada 5 minutos
        self._storage_client = storage.Client()

    def get_services(self) -> Dict[str, Any]:
        """Devuelve la configuración, recargándola si ha expirado."""
        now = datetime.utcnow()
        if now - self._last_loaded > self._cache_ttl:
            print("🔄 Recargando configuración desde GCS...")
            self._load_from_gcs()
        return self._config

    def _load_from_gcs(self):
        try:
            bucket = self._storage_client.bucket(CONFIG_BUCKET_NAME)
            blob = bucket.blob(CONFIG_FILE_NAME)
            
            # Descargamos el contenido como texto
            json_content = blob.download_as_text()
            self._config = json.loads(json_content)
            
            self._last_loaded = datetime.utcnow()
            print(f"✅ Configuración actualizada. Servicios: {list(self._config.keys())}")
        except Exception as e:
            print(f"⚠️ Error leyendo bucket: {e}")
            # Estrategia de fallo: Si ya tenemos config, la mantenemos. 
            # Si está vacía, intentamos leer local como fallback.
            if not self._config:
                self._load_local_fallback()

    def _load_local_fallback(self):
        print("📂 Intentando cargar services.json local...")
        try:
            with open("services.json", "r") as f:
                self._config = json.load(f)
            self._last_loaded = datetime.utcnow()
        except Exception as e:
            print(f"❌ Fallo total de configuración: {e}")

# Instancia global
config_manager = ConfigManager()

app = FastAPI(title="Ortega Enqueuer API (GCS Powered)")

# --- MODELOS ---
class EnqueueRequest(BaseModel):
    service: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    delay_s: Optional[int] = Field(default=None, ge=0)
    deadline_s: Optional[int] = Field(default=None, ge=1, le=3600)

    @field_validator('service')
    def service_must_exist(cls, v):
        # Obtenemos la config actual para validar
        current_services = config_manager.get_services()
        if v not in current_services:
            raise ValueError(f"Servicio '{v}' no encontrado. Disponibles: {list(current_services.keys())}")
        return v

# --- ENDPOINTS ---
@app.post("/enqueue")
def enqueue(req: EnqueueRequest):
    services = config_manager.get_services()
    cfg = services[req.service]
    
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

    if req.delay_s and req.delay_s > 0:
        task["schedule_time"] = {"seconds": int(time.time()) + req.delay_s}

    deadline_s = req.deadline_s or cfg.get("deadline_s", 900)
    task["dispatch_deadline"] = f"{deadline_s}s"

    try:
        resp = client.create_task(request={"parent": parent, "task": task})
        return {
            "ok": True,
            "task": resp.name,
            "service": req.service,
            "from_config_ver": str(config_manager._last_loaded) # Debug info
        }
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Error en Cloud Tasks")

# Endpoint útil para forzar recarga manual sin esperar el TTL
@app.post("/config/refresh")
def refresh_config():
    config_manager._load_from_gcs()
    return {"ok": True, "services": list(config_manager._config.keys())}