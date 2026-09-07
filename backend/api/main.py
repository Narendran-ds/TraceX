"""FastAPI application.

Routes implement the frozen contract in CLAUDE.md exactly — no endpoint is added
or renamed here.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.errors import DomainError
from backend.api.routes import router
from backend.models.db import init_db

logger = logging.getLogger("fundtrail")

API_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Ensure the schema exists before the first request."""
    init_db()
    yield


app = FastAPI(
    title="SIH26183 — Fund-Trail Investigation Assistant",
    version=API_VERSION,
    description=(
        "Decision support for a victim-initiated cybercrime complaint. Traces a "
        "reported wallet address, clusters the transaction graph, flags patterns "
        "consistent with laundering using a transparent rule engine, and produces "
        "a hash-sealed investigator report. Every finding requires human review."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
def _domain_error_handler(_request, exc: DomainError) -> JSONResponse:
    """Domain failures surface as a readable message, never a raw traceback."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message, "detail": exc.detail},
    )


@app.exception_handler(Exception)
def _unhandled_error_handler(_request, exc: Exception) -> JSONResponse:
    """Last line of defence for CLAUDE.md rule 6: never surface a raw error.

    The full traceback goes to the server log; the client gets a message an
    investigator can read and act on.
    """
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": (
                "The investigation service hit an unexpected condition and stopped "
                "this action. No case data was modified. Check the server log for "
                "the full trace."
            ),
            "detail": {},
        },
    )


app.include_router(router)
