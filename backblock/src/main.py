from fastapi import FastAPI
from routers import account, blockchain, health
from settings import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.fastapi.app_name,
    version=settings.fastapi.version,
    description=settings.fastapi.description,
)

# Register controllers
app.include_router(blockchain.router)
app.include_router(account.router)
app.include_router(health.router)

print("ROUTERS INCLUDED")
