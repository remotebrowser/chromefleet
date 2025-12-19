#!/usr/bin/env python3

import uvicorn

from datetime import datetime
from fastapi import FastAPI

app = FastAPI(title="Chrome Fleet")


@app.get("/health")
async def health() -> str:
    return f"OK {int(datetime.now().timestamp())}"


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8300, reload=True)
