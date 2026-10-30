from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.db.mongo import get_client, get_db, ensure_indexes, close_client
from app.routers.api import router

@asynccontextmanager
async def lifespan(_app):
    client = get_client()
    client.admin.command("ping")
    ensure_indexes(get_db())
    yield
    close_client()

app = FastAPI(title="SJMS · Jewellery Management", version="1.0.0", description="Jewellery retail operations, inventory and billing API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix="/api")

@app.get("/health")
def health(): return {"status":"ok"}

