import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# إنشاء التطبيق
app = FastAPI(
    title="Alpha Iraq API",
    version="1.0.0"
)

# إعداد CORS (يسمح للفرونتند يتصل)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# لوق بسيط
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Route رئيسي
@app.get("/")
def root():
    return {"message": "API is running 🚀"}

# Route فحص
@app.get("/health")
def health():
    return {"status": "ok"}

# تشغيل التطبيق
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )