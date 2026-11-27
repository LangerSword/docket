"""AWS Lambda entry point. API Gateway REST events go through Mangum."""
from __future__ import annotations

from mangum import Mangum

from .app import app

handler = Mangum(app, lifespan="off")