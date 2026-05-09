from fastapi import FastAPI

from .routes import (
    applications,
    cold_email,
    companies,
    dashboard,
    jobs,
    profile,
    settings,
)

app = FastAPI(title="AutoApply", docs_url=None, redoc_url=None)

app.include_router(dashboard.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(companies.router)
app.include_router(settings.router)
app.include_router(cold_email.router)
app.include_router(profile.router)
