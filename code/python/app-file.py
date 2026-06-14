# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
This file is the entry point for the NLWeb Sample App.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

import asyncio
import os
from dotenv import load_dotenv


async def main():
    # Load environment variables from .env file (project root, not cwd)
    import pathlib
    project_root = pathlib.Path(__file__).resolve().parent.parent.parent
    load_dotenv(project_root / '.env')
    
    # Setup root logger so modules using logging.getLogger(__name__) have output
    import logging
    from misc.logger.logger import JsonFormatter
    _root_handler = logging.StreamHandler()
    _root_handler.setFormatter(JsonFormatter())
    logging.root.addHandler(_root_handler)
    logging.root.setLevel(logging.INFO)
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
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())