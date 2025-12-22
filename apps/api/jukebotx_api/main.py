from fastapi import FastAPI

app = FastAPI(title="JukeBotx API")

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
