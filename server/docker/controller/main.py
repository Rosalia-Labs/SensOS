# main.py
from fastapi import FastAPI
from api import router as api_router
from core import lifespan

app = FastAPI(lifespan=lifespan)
app.include_router(api_router)
