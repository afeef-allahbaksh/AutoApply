from fastapi import FastAPI

from .routes import (
    applications,
    apply,
    cold_email,
    companies,
    dashboard,
    email,
    jobs,
    optimize,
    pipeline,
    profile,
    settings,
    setup,
)

app = FastAPI(title="AutoApply", docs_url=None, redoc_url=None)

app.include_router(setup.router)
app.include_router(dashboard.router)
app.include_router(pipeline.router)
app.include_router(jobs.router)
app.include_router(optimize.router)
app.include_router(apply.router)
app.include_router(applications.router)
app.include_router(companies.router)
app.include_router(settings.router)
app.include_router(cold_email.router)
app.include_router(profile.router)
app.include_router(email.router)
