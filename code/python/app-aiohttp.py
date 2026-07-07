# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Entry point for the NLWeb Sample App with aiohttp server.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

import asyncio
import os
import io
import sys
from dotenv import load_dotenv

# Load .env BEFORE any application imports so module-level os.environ.get() calls
# (e.g. JWT_SECRET in auth_service.py) pick up the values.
# Use project root path (nlweb/.env), not cwd (code/python/)
import pathlib
_project_root = pathlib.Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / '.env')

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Initialize Sentry error tracking (DSN strictly from env; no hardcoded fallback — see lessons-general)
import sentry_sdk
_sentry_dsn = os.environ.get('SENTRY_DSN')
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        environment=os.environ.get('SENTRY_ENVIRONMENT', 'development'),
        send_default_pii=True,
        traces_sample_rate=0.1,  # 10% of requests for performance monitoring
    )
else:
    print("[sentry] SENTRY_DSN not set — error tracking disabled", file=sys.stderr)


async def main():

    # Setup root logger with JSON format so all modules have structured output
    import logging
    from misc.logger.logger import JsonFormatter
    _root_handler = logging.StreamHandler()
    _root_handler.setFormatter(JsonFormatter())
    logging.root.addHandler(_root_handler)
    logging.root.setLevel(logging.INFO)

    # Suppress verbose HTTP client logging from OpenAI SDK
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    
    # Suppress Azure SDK HTTP logging
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
    logging.getLogger("azure").setLevel(logging.WARNING)
    
    # Suppress webserver middleware INFO logs
    logging.getLogger("webserver.middleware.logging_middleware").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    
    # Initialize router
    import core.router as router
    router.init()
    
    # Initialize LLM providers
    import core.llm as llm
    llm.init()
    
    # Initialize retrieval clients
    import core.retriever as retriever
    retriever.init()
    
    print("Starting aiohttp server...")
    from webserver.aiohttp_server import AioHTTPServer
    server = AioHTTPServer()
    await server.start()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())