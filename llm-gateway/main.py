from fastapi import FastAPI
from pydantic import BaseModel, Field
import os
import time
import jwt
import httpx
from models import LLMAnswer


class GenerateRequest(BaseModel):
    prompt: str
    context: str | None = None
    max_tokens: int | None = Field(1000, ge=1)
    temperature: float | None = Field(0.6)


app = FastAPI()
service_account_id = os.environ.get("SERVICE_ACCOUNT_ID")
key_id = os.environ.get("KEY_ID")
private_key = os.environ.get("PRIVATE_KEY", "").replace("\\n", "\n")
folder_id = os.environ.get("FOLDER_ID")
model_name = os.environ.get("MODEL_NAME", "yandexgpt-lite")
system_prompt = os.environ.get("SYSTEM_PROMPT", "Ты помощник, который отвечает кратко и понятно.")

_iam_token: str | None = None
_token_expires: float = 0.0


async def get_iam_token() -> str:
    global _iam_token, _token_expires
    now = time.time()
    if _iam_token and now < _token_expires - 30:
        return _iam_token
    payload = {
        "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        "iss": service_account_id,
        "iat": int(now),
        "exp": int(now + 3600),
    }
    token = jwt.encode(payload, private_key, algorithm="PS256", headers={"kid": key_id})
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://iam.api.cloud.yandex.net/iam/v1/tokens",
            json={"jwt": token},
        )
        data = resp.json()
        _iam_token = data.get("iamToken")
        _token_expires = now + 3500
        return _iam_token


@app.post("/generate", response_model=LLMAnswer)
async def generate(req: GenerateRequest):
    token = await get_iam_token()
    messages = []
    messages.append({"role": "system", "text": system_prompt})
    if req.context:
        user_text = f"Вопрос: {req.prompt}\nКонтекст:\n{req.context}"
    else:
        user_text = req.prompt
    messages.append({"role": "user", "text": user_text})
    body = {
        "modelUri": f"gpt://{folder_id}/{model_name}",
        "completionOptions": {
            "stream": False,
            "temperature": req.temperature,
            "maxTokens": req.max_tokens,
        },
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-folder-id": folder_id or "",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers=headers,
            json=body,
        )
        data = resp.json()
        alt = data.get("result", {}).get("alternatives", [{}])[0]
        answer_text = alt.get("message", {}).get("text", "")
        tokens_used = alt.get("tokens", 0)
        return LLMAnswer(answer=answer_text, tokens=tokens_used, model=model_name)


@app.get("/health")
async def health():
    return {"status": "ok"}