from fastapi import FastAPI
from pydantic import BaseModel, Field
import os
import asyncio
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores.faiss import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from typing import List


class RetrieveRequest(BaseModel):
    query: str
    top_k: int | None = Field(3, ge=1)


app = FastAPI()
s3_endpoint = os.environ.get("S3_ENDPOINT")
s3_access_key = os.environ.get("S3_ACCESS_KEY")
s3_secret_key = os.environ.get("S3_SECRET_KEY")
s3_bucket = os.environ.get("S3_BUCKET")
s3_prefix = os.environ.get("S3_PREFIX", "").rstrip("/")
embedding_model = os.environ.get("EMBEDDINGS_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

index: FAISS | None = None
_index_lock = asyncio.Lock()


async def build_index() -> None:
    global index
    async with _index_lock:
        if index is not None:
            return
        s3 = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
        )
        docs: List[str] = []
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=s3_bucket, Prefix=s3_prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not key.endswith((".txt", ".md", ".pdf")):
                        continue
                    res = s3.get_object(Bucket=s3_bucket, Key=key)
                    content = res["Body"].read().decode("utf-8", errors="ignore")
                    docs.append(content)
        except (BotoCoreError, ClientError):
            pass
        texts: List[str] = []
        splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=128)
        for d in docs:
            texts.extend(splitter.split_text(d))
        if not texts:
            texts.append("")
        emb = HuggingFaceEmbeddings(model_name=embedding_model)
        index = FAISS.from_texts(texts, emb)


@app.post("/retrieve")
async def retrieve(req: RetrieveRequest):
    if not req.query.strip():
        return {"context": ""}
    if index is None:
        await build_index()
    results = await asyncio.to_thread(lambda: index.similarity_search(req.query, k=req.top_k))
    context = "\n".join(r.page_content for r in results)
    return {"context": context[:2000]}


@app.get("/health")
async def health():
    return {"status": "ok"}