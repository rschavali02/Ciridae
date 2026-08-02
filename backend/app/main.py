import os
import tempfile

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.extraction.pipeline import extract_invoice

app = FastAPI(title="Invoice Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result = extract_invoice(tmp_path)
    finally:
        os.unlink(tmp_path)

    return {
        "used_vision_fallback": result.used_vision_fallback,
        "raw_text": result.raw_text,
        "fields": result.fields.model_dump(),
    }
