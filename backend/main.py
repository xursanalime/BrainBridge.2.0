import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from db import init_db
from routes import auth, words, reset, google_auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="BrainBridge", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,        prefix="/api")
app.include_router(reset.router,       prefix="/api")
app.include_router(google_auth.router, prefix="/api")
app.include_router(words.router,       prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True, "version": "3.0.0"}


# Serve frontend
frontend = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend):
    app.mount("/static", StaticFiles(directory=frontend), name="static")

    @app.get("/", include_in_schema=False)
    def root():
        return FileResponse(os.path.join(frontend, "index.html"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    reload = os.getenv("ENV", "production") != "production"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload)
