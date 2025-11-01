from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import os
import uuid
import httpx
from models import UserRequest, ProcessResponse


class ProcessRequest(BaseModel):
    user_id: str
    text: str
    session_id: str | None = None


app = FastAPI()
moderation_url = os.environ.get("MODERATION_URL", "http://moderation-service:8002")
rag_url = os.environ.get("RAG_URL", "http://rag-service:8003")
llm_url = os.environ.get("LLM_URL", "http://llm-gateway:8001")
audit_url = os.environ.get("AUDIT_URL", "http://audit-service:8000")
service_token = os.environ.get("ORCHESTRATOR_TOKEN")


async def post_json(url: str, path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{url}{path}", json=payload)
        return resp.json()


async def log_event(trace_id: str, event: str, details: dict) -> None:
    try:
        await post_json(audit_url, "/log", {"trace_id": trace_id, "event": event, "details": details})
    except Exception:
        pass


@app.post("/process", response_model=ProcessResponse)
async def process(req: ProcessRequest, authorization: str | None = Header(default=None)):
    if service_token and authorization != f"Bearer {service_token}":
        raise HTTPException(status_code=401, detail="Invalid token")
    trace_id = uuid.uuid4().hex
    await log_event(trace_id, "request_received", {"user_id": req.user_id})
    # pre‑moderation
    mod_res = await post_json(moderation_url, "/classify-input", req.dict())
    await log_event(trace_id, "moderation_before", mod_res)
    if not mod_res.get("allowed", True):
        await log_event(trace_id, "blocked_pre_generation", {})
        return ProcessResponse(trace_id=trace_id, answer=None, risk=mod_res.get("risk", 1.0), template="blocked_pre_generation", meta={})
    # RAG retrieval
    ctx = ""
    try:
        rag_res = await post_json(rag_url, "/retrieve", {"query": req.text, "top_k": 3})
        ctx = rag_res.get("context", "")
    except Exception:
        ctx = ""
        await log_event(trace_id, "rag_error", {})
    await log_event(trace_id, "rag_retrieved", {"length": len(ctx)})
    # call LLM
    llm_payload = {"prompt": req.text, "context": ctx, "max_tokens": 1000, "temperature": 0.6}
    try:
        llm_res = await post_json(llm_url, "/generate", llm_payload)
    except Exception:
        await log_event(trace_id, "llm_error", {})
        return ProcessResponse(trace_id=trace_id, answer=None, risk=mod_res.get("risk", 1.0), template="generation_error", meta={})
    await log_event(trace_id, "llm_generated", {"model": llm_res.get("model"), "tokens": llm_res.get("tokens")})
    llm_ans = llm_res.get("answer", "")
    # post‑moderation
    moder_out = await post_json(moderation_url, "/validate-output", {"answer": llm_ans})
    await log_event(trace_id, "moderation_after", moder_out)
    if not moder_out.get("allowed", True):
        return ProcessResponse(trace_id=trace_id, answer=None, risk=max(mod_res.get("risk", 0.0), moder_out.get("risk", 1.0)), template="blocked_post_generation", meta={})
    return ProcessResponse(trace_id=trace_id, answer=llm_ans, risk=mod_res.get("risk", 0.0), template=None, meta={"context_used": bool(ctx)})


@app.get("/health")
async def health():
    return {"status": "ok"}