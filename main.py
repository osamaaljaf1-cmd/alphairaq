import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# إنشاء التطبيق
app = FastAPI(
    title="Alpha Iraq API",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# routes الأساسية
@app.get("/")
def root():
    return {"message": "API is running 🚀"}

@app.get("/health")
def health():
    return {"status": "ok"}

# test router
from fastapi import APIRouter

test_router = APIRouter()

@test_router.get("/test")
def test():
    return {"message": "working"}

app.include_router(test_router)

# 🔥 المهم هذا
from routers import auth
app.include_router(auth.router)

# تشغيل
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )

from routers.auth import simple_router

app.include_router(simple_router)
