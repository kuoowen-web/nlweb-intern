# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Core service class for managing user-uploaded files and private knowledge base.

This module orchestrates the entire workflow:
1. File upload and validation
2. Text extraction (parsing)
3. Text chunking
4. Embedding generation
5. Vector indexing in PostgreSQL (pgvector)
6. Database metadata management
"""

import os
import time
import uuid
import hashlib
from typing import Dict, Any, List, Optional, BinaryIO
from pathlib import Path
import yaml

from core.user_data_db import get_user_data_db
from core.user_file_storage import get_file_storage_manager
from core.parsers import ParserFactory
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("user_data_manager")


class UserDataManager:
    """Core service class for user data management."""

    def __init__(self, config_path: str = None):
        """
        Initialize the UserDataManager.

        Args:
            config_path: Path to user_data.yaml config file
        """
        # Load configuration
        self.config = self._load_config(config_path)

        # Initialize database (no await needed — initialize() is called separately)
        self.db = get_user_data_db()

        # Initialize storage manager
        storage_backend = self.config['storage']['backend']
        # Exclude 'backend' from config to avoid duplicate argument
        storage_config = {k: v for k, v in self.config['storage'].items() if k != 'backend'}
        self.storage = get_file_storage_manager(storage_backend, **storage_config)

        logger.info("UserDataManager initialized")

    def _load_config(self, config_path: str = None) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if config_path is None:
            # Use NLWEB_CONFIG_DIR if available (Docker), otherwise fall back to path calculation
            config_dir = os.environ.get('NLWEB_CONFIG_DIR')
            if config_dir:
                config_path = Path(config_dir) / "user_data.yaml"
            else:
                current_file = Path(__file__).resolve()
                project_root = current_file.parent.parent.parent.parent
                config_path = project_root / "config" / "user_data.yaml"

        if not Path(config_path).exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        logger.info(f"Loaded configuration from: {config_path}")
        return config

    async def validate_file(self, filename: str, file_size: int, user_id: str) -> Dict[str, Any]:
        """
        Validate an uploaded file before processing.

        Args:
            filename: Original filename
            file_size: File size in bytes
            user_id: User identifier

        Returns:
            Validation result dict with 'valid' and optional 'error' keys
        """
        # Check file extension
        file_ext = Path(filename).suffix.lower()
        allowed_extensions = self.config['file_limits']['allowed_extensions']

        if file_ext not in allowed_extensions:
            return {
                'valid': False,
                'error': f'File type not allowed. Allowed types: {", ".join(allowed_extensions)}'
            }

        # Check file size
        max_size = self.config['file_limits']['max_file_size_bytes']
        if file_size > max_size:
            max_size_mb = max_size / (1024 * 1024)
            return {
                'valid': False,
                'error': f'File size exceeds maximum allowed size of {max_size_mb:.1f}MB'
            }

        # Check user's total storage usage
        total_usage = await self.get_user_storage_usage(user_id)
        max_total = self.config['file_limits']['max_total_size_per_user_bytes']

        if total_usage + file_size > max_total:
            max_total_mb = max_total / (1024 * 1024)
            return {
                'valid': False,
                'error': f'Total storage limit ({max_total_mb:.1f}MB) would be exceeded'
            }

        # Check number of files
        file_count = await self.get_user_file_count(user_id)
        max_files = self.config['file_limits']['max_files_per_user']

        if file_count >= max_files:
            return {
                'valid': False,
                'error': f'Maximum number of files ({max_files}) reached'
            }

        return {'valid': True}

    async def get_user_storage_usage(self, user_id: str) -> int:
        """
        Get total storage usage for a user in bytes.

        Args:
            user_id: User identifier

        Returns:
            Total bytes used
        """
        result = await self.db.fetchone(
            "SELECT SUM(size_bytes) as total FROM user_sources WHERE user_id = ?",
            (user_id,)
        )
        total = result['total'] if result and result.get('total') else 0
        return int(total)

    async def get_user_file_count(self, user_id: str) -> int:
        """
        Get number of files uploaded by a user.

        Args:
            user_id: User identifier

        Returns:
            File count
        """
        result = await self.db.fetchone(
            "SELECT COUNT(*) as count FROM user_sources WHERE user_id = ?",
            (user_id,)
        )
        return int(result['count']) if result else 0

    async def create_source(self, user_id: str, filename: str, file_size: int, org_id: str = None) -> str:
        """
        Create a new source record in the database.

        Args:
            user_id: User identifier
            filename: Original filename
            file_size: File size in bytes
            org_id: Organization identifier (optional, for B2B isolation)

        Returns:
            source_id (UUID)
        """
        source_id = str(uuid.uuid4())
        file_type = Path(filename).suffix.lower()
        current_time = time.time()

        await self.db.execute(
            """
            INSERT INTO user_sources
            (source_id, user_id, org_id, name, file_type, status, size_bytes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_id, user_id, org_id, filename, file_type, 'uploading', file_size, current_time, current_time)
        )
        logger.info(f"Created source record: {source_id} for user: {user_id}, org: {org_id}")
        return source_id

    async def update_source_status(self, source_id: str, status: str, error_message: str = None):
        """
        Update the status of a source.

        Args:
            source_id: Source identifier
            status: New status ('uploading', 'processing', 'ready', 'failed')
            error_message: Optional error message if status is 'failed'
        """
        current_time = time.time()

        await self.db.execute(
            """
            UPDATE user_sources
            SET status = ?, error_message = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (status, error_message, current_time, source_id)
        )
        logger.info(f"Updated source {source_id} status to: {status}")

    def save_file(self, user_id: str, source_id: str, file_data: BinaryIO, filename: str) -> str:
        """
        Save an uploaded file to storage.

        Args:
            user_id: User identifier
            source_id: Source identifier
            file_data: File binary data
            filename: Original filename

        Returns:
            File path
        """
        try:
            file_path = self.storage.save_file(user_id, source_id, file_data, filename)
            logger.info(f"File saved: {file_path}")
            return file_path
        except Exception as e:
            logger.exception(f"Failed to save file: {str(e)}")
            raise

    def parse_file(self, file_path: str) -> Dict[str, Any]:
        """
        Parse a file and extract text content.

        Args:
            file_path: Path to the file

        Returns:
            Parsed content dict with 'text' and 'metadata' keys
        """
        try:
            result = ParserFactory.parse_file(file_path)
            logger.info(f"File parsed successfully: {file_path}")
            return result
        except Exception as e:
            logger.exception(f"Failed to parse file: {str(e)}")
            raise

    def compute_checksum(self, text: str) -> str:
        """
        Compute SHA256 checksum of text content.

        Args:
            text: Text content

        Returns:
            Hexadecimal checksum string
        """
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    async def create_document_record(self, source_id: str, checksum: str, chunk_count: int) -> str:
        """
        Create a document record in the database.

        Args:
            source_id: Source identifier
            checksum: SHA256 checksum of content
            chunk_count: Number of chunks created

        Returns:
            doc_id (UUID)
        """
        doc_id = str(uuid.uuid4())
        processed_at = time.time()

        await self.db.execute(
            """
            INSERT INTO user_documents
            (doc_id, source_id, checksum, chunk_count, processed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (doc_id, source_id, checksum, chunk_count, processed_at)
        )
        logger.info(f"Created document record: {doc_id}")
        return doc_id

    async def list_user_sources(self, user_id: str, org_id: str = None) -> List[Dict[str, Any]]:
        """
        List all sources for a user, optionally filtered by organization.

        Args:
            user_id: User identifier
            org_id: Organization identifier (for B2B isolation)

        Returns:
            List of source dictionaries
        """
        if org_id:
            rows = await self.db.fetchall(
                """
                SELECT source_id, name, file_type, status, size_bytes,
                       created_at, updated_at, error_message
                FROM user_sources
                WHERE user_id = ? AND org_id = ?
                ORDER BY created_at DESC
                """,
                (user_id, org_id)
            )
        else:
            rows = await self.db.fetchall(
                """
                SELECT source_id, name, file_type, status, size_bytes,
                       created_at, updated_at, error_message
                FROM user_sources
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,)
            )

        sources = []
        for row in rows:
            sources.append({
                'source_id': row['source_id'],
                'name': row['name'],
                'file_type': row['file_type'],
                'status': row['status'],
                'size_bytes': row['size_bytes'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at'],
                'error_message': row['error_message']
            })

        return sources

    async def delete_source(self, user_id: str, source_id: str, org_id: str = None) -> bool:
        """
        Delete a source and all its associated data.

        Args:
            user_id: User identifier (for authorization check)
            source_id: Source identifier
            org_id: Organization identifier (for B2B isolation)

        Returns:
            True if deletion successful, False otherwise
        """
        try:
            # Verify ownership (+ org isolation if org_id provided)
            if org_id:
                result = await self.db.fetchone(
                    "SELECT user_id FROM user_sources WHERE source_id = ? AND org_id = ?",
                    (source_id, org_id)
                )
            else:
                result = await self.db.fetchone(
                    "SELECT user_id FROM user_sources WHERE source_id = ?",
                    (source_id,)
                )

            if not result or result['user_id'] != user_id:
                logger.warning(f"Unauthorized deletion attempt: user={user_id}, source={source_id}")
                return False

            # Delete from storage
            self.storage.delete_file(user_id, source_id)

            # Delete from database (cascades to user_documents)
            await self.db.execute(
                "DELETE FROM user_sources WHERE source_id = ?",
                (source_id,)
            )

            logger.info(f"Deleted source: {source_id}")
            return True

        except Exception as e:
            logger.exception(f"Failed to delete source: {str(e)}")
            return False


# Global instance for reuse
_user_data_manager_instance = None


def get_user_data_manager(config_path: str = None) -> UserDataManager:
    """
    Get or create the global UserDataManager instance.

    Args:
        config_path: Optional path to config file

    Returns:
        UserDataManager instance
    """
    global _user_data_manager_instance
    if _user_data_manager_instance is None:
        _user_data_manager_instance = UserDataManager(config_path)
    return _user_data_manager_instance
