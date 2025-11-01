from fastapi import FastAPI
from pydantic import BaseModel
from models import AuditLogEntry
import os
import json

app = FastAPI()
log_file = os.environ.get("AUDIT_LOG_FILE", "audit.log")

@app.post("/log")
async def log_entry(entry: AuditLogEntry):
    line = entry.json()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok"}