#!/usr/bin/env python3
"""
Migration script to add payload index to existing nlweb_user_data collection.

This script adds the required 'user_id' keyword index to enable filtered searches.
"""

import sys
import os

# Add code/python to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'code', 'python'))

import asyncio
from qdrant_client.http import models
from retrieval_providers.qdrant_retrieve import get_qdrant_client
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("migration")


async def migrate_user_data_index():
    """Add user_id payload index to nlweb_user_data collection."""
    collection_name = "nlweb_user_data"

    try:
        client = await get_qdrant_client()

        # Check if collection exists
        try:
            collection_info = await client.get_collection(collection_name)
            logger.info(f"Found collection: {collection_name}")
            logger.info(f"Vectors count: {collection_info.vectors_count}")
        except Exception as e:
            logger.error(f"Collection {collection_name} not found: {e}")
            return

        # Create payload index for user_id
        logger.info("Creating payload index for 'user_id' field...")
        try:
            await client.create_payload_index(
                collection_name=collection_name,
                field_name="user_id",
                field_schema=models.PayloadSchemaType.KEYWORD
            )
            logger.info("✅ Successfully created user_id index")
        except Exception as e:
            error_str = str(e)
            if "already exists" in error_str.lower():
                logger.info("✅ Index already exists, nothing to do")
            else:
                raise

        # Optionally create index for source_id as well (useful for filtering)
        logger.info("Creating payload index for 'source_id' field...")
        try:
            await client.create_payload_index(
                collection_name=collection_name,
                field_name="source_id",
                field_schema=models.PayloadSchemaType.KEYWORD
            )
            logger.info("✅ Successfully created source_id index")
        except Exception as e:
            error_str = str(e)
            if "already exists" in error_str.lower():
                logger.info("✅ Index already exists, nothing to do")
            else:
                raise

        # Verify indexes
        collection_info = await client.get_collection(collection_name)
        logger.info(f"Collection status: {collection_info.status}")
        logger.info("Migration completed successfully!")

    except Exception as e:
        logger.exception(f"Migration failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(migrate_user_data_index())
