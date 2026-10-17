import os
import uvicorn
from fastapi import FastAPI
from .orders import router

app = FastAPI(title="shop-api")
app.include_router(router)

def run():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
