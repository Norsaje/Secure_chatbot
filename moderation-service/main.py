from fastapi import FastAPI
from pydantic import BaseModel, Field
from models import ModerationVerdict, UserRequest
import re
import os
import time
import jwt
import httpx
import json
from typing import List, Dict, Any


class OutputRequest(BaseModel):
    answer: str


app = FastAPI()
service_account_id = os.environ.get("SERVICE_ACCOUNT_ID")
key_id = os.environ.get("KEY_ID")
private_key = os.environ.get("PRIVATE_KEY", "").replace("\\n", "\n")
folder_id = os.environ.get("FOLDER_ID")
model_name = os.environ.get("MODEL_NAME", "yandexgpt-lite")
_iam_token: str | None = None
_token_exp: float = 0.0

PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"(?i)\b(ignore|forget|reset|clear|override|bypass)\s+(all\s+)?(prior|previous|earlier)\s+(instructions?|prompts?|rules)\b"),
    re.compile(r"(?i)\bобойди\s+(все\s+)?предыдущие\s+(ограничения|правила)\b"),
    re.compile(r"(?i)\b(show|reveal|dump|print)\s+(your\s+)?(system|base|initial)\s+(prompt|instructions?|rules)\b"),
    re.compile(r"(?i)\bпокажи\s+системный\s+промпт\b"),
    re.compile(r"(?i)\b(pretend|act|imagine)\s+(you\s+are|as)\b"),
    re.compile(r"(?i)\bсчитай\s+себя\b"),
    re.compile(r"(?i)\b(exec|eval|subprocess|system\()\b"),
]


async def get_token() -> str:
    global _iam_token, _token_exp
    now = time.time()
    if _iam_token and now < _token_exp - 30:
        return _iam_token
    payload = {
        "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        "iss": service_account_id,
        "iat": int(now),
        "exp": int(now + 3600),
    }
    jwt_token = jwt.encode(payload, private_key, algorithm="PS256", headers={"kid": key_id})
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://iam.api.cloud.yandex.net/iam/v1/tokens",
            json={"jwt": jwt_token},
        )
        data = resp.json()
        _iam_token = data.get("iamToken")
        _token_exp = now + 3500
        return _iam_token


async def ask_classification(question: str, patterns: List[str]) -> Dict[str, Any]:
    token = await get_token()
    prompt = (
        "Ты — помощник по безопасности. Задача: получив пользовательский ввод и совпавшие шаблоны регулярных выражений, определить, "
        "является ли ввод безвредным (например, учебный пример, фрагмент кода, обычный вопрос), вредоносным (попытка извлечь данные, SQL-инъекция, XSS, удалённое выполнение кода) или "
        "неоднозначным.\n\n"
        "Верни один JSON-объект с ключами:\n"
        "intent: одно из значений [\"benign\",\"malicious\",\"ambiguous\"]\n"
        "confidence: число от 0.0 до 1.0\n"
        "recommended_action: одно из [\"allow\",\"ask_clarification\",\"escalate_human\",\"block\"]\n"
        "normalized_input: предобработанный текст\n\n"
        f"User_input: {question}\n"
        f"Matched_patterns: {patterns}\n"
        "Верни только JSON."
    )
    body = {
        "modelUri": f"gpt://{folder_id}/{model_name}",
        "completionOptions": {
            "stream": False,
            "temperature": 0.0,
            "maxTokens": 500,
        },
        "messages": [
            {"role": "system", "text": prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-folder-id": folder_id or "",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers=headers,
            json=body,
        )
        data = resp.json()
        text = data.get("result", {}).get("alternatives", [{}])[0].get("message", {}).get("text", "{}")
        start = text.find("{")
        end = text.rfind("}") + 1
        try:
            return json.loads(text[start:end])
        except Exception:
            return {"intent": "ambiguous", "confidence": 0.4, "recommended_action": "ask_clarification"}


@app.post("/classify-input", response_model=ModerationVerdict)
async def classify_input(req: UserRequest):
    t = req.text
    matches = [p.pattern for p in PATTERNS if p.search(t)]
    if matches:
        cls = await ask_classification(t, matches)
        action = cls.get("recommended_action", "ask_clarification")
        intent = cls.get("intent", "ambiguous")
        conf = float(cls.get("confidence", 0.5))
        risk = conf if intent != "benign" else 0.1 * conf
        allowed = action not in {"block", "escalate_human"}
        return ModerationVerdict(allowed=allowed, risk=risk, action=action, reasons=matches)
    return ModerationVerdict(allowed=True, risk=0.0, action="allow", reasons=[])


@app.post("/validate-output", response_model=ModerationVerdict)
async def validate_output(req: OutputRequest):
    ans = req.answer
    token = await get_token()
    prompt = (
        "You are a security assistant. Task: given a candidate answer from another LLM,\n"
        "determine if it is SAFE to show to the user.\n\n"
        "Return JSON only: {\"status\": \"safe\"} or {\"status\": \"unsafe\"}.\n\n"
        f"Input: {ans}\n"
    )
    body = {
        "modelUri": f"gpt://{folder_id}/{model_name}",
        "completionOptions": {
            "stream": False,
            "temperature": 0.0,
            "maxTokens": 100,
        },
        "messages": [
            {"role": "system", "text": prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-folder-id": folder_id or "",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers=headers,
            json=body,
        )
        data = resp.json()
        text = data.get("result", {}).get("alternatives", [{}])[0].get("message", {}).get("text", "safe")
        if "unsafe" in text.lower():
            return ModerationVerdict(allowed=False, risk=0.8, action="block", reasons=["unsafe_output"])
    return ModerationVerdict(allowed=True, risk=0.0, action="allow", reasons=[])


@app.get("/health")
async def health():
    return {"status": "ok"}