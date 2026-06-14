# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
File storage abstraction layer for user-uploaded files.

Supports both local filesystem (development) and cloud storage (production).
This abstraction allows easy migration from local to cloud storage in the future.
"""

import os
import shutil
from pathlib import Path
from typing import Optional, BinaryIO
from abc import ABC, abstractmethod
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("user_file_storage")


class FileStorageBackend(ABC):
    """Abstract base class for file storage backends."""

    @abstractmethod
    def save_file(self, user_id: str, source_id: str, file_data: BinaryIO, filename: str) -> str:
        """
        Save a file to storage.

        Args:
            user_id: User identifier
            source_id: Source identifier (UUID)
            file_data: File binary data
            filename: Original filename

        Returns:
            Storage path or URI
        """
        pass

    @abstractmethod
    def get_file_path(self, user_id: str, source_id: str) -> str:
        """
        Get the path/URI of a stored file.

        Args:
            user_id: User identifier
            source_id: Source identifier

        Returns:
            File path or URI
        """
        pass

    @abstractmethod
    def delete_file(self, user_id: str, source_id: str) -> bool:
        """
        Delete a file from storage.

        Args:
            user_id: User identifier
            source_id: Source identifier

        Returns:
            True if deletion successful, False otherwise
        """
        pass

    @abstractmethod
    def file_exists(self, user_id: str, source_id: str) -> bool:
        """
        Check if a file exists in storage.

        Args:
            user_id: User identifier
            source_id: Source identifier

        Returns:
            True if file exists, False otherwise
        """
        pass


class LocalFileStorage(FileStorageBackend):
    """Local filesystem storage backend."""

    def __init__(self, base_path: str = "data/user_uploads"):
        """
        Initialize local file storage.

        Args:
            base_path: Base directory for file storage (relative to project root)
        """
        # Use NLWEB_DATA_DIR if available (Docker), otherwise fall back to path calculation
        data_dir = os.environ.get('NLWEB_DATA_DIR')
        if data_dir:
            # In Docker: NLWEB_DATA_DIR=/data, base_path='data/user_uploads'
            # Strip 'data/' prefix since NLWEB_DATA_DIR already points to data root
            relative = base_path[len('data/'):] if base_path.startswith('data/') else base_path
            self.base_path = Path(data_dir) / relative
        else:
            current_file = Path(__file__).resolve()
            project_root = current_file.parent.parent.parent.parent
            self.base_path = project_root / base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Local file storage initialized at: {self.base_path.absolute()}")

    def _get_user_dir(self, user_id: str, source_id: str) -> Path:
        """Get the directory path for a specific user and source."""
        return self.base_path / user_id / source_id

    def save_file(self, user_id: str, source_id: str, file_data: BinaryIO, filename: str) -> str:
        """
        Save a file to local filesystem.

        Args:
            user_id: User identifier
            source_id: Source identifier (UUID)
            file_data: File binary data
            filename: Original filename

        Returns:
            Absolute file path
        """
        user_dir = self._get_user_dir(user_id, source_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        # Strip directory components to prevent path traversal
        file_path = user_dir / Path(filename).name

        try:
            with open(file_path, 'wb') as f:
                shutil.copyfileobj(file_data, f)
            logger.info(f"File saved: {file_path}")
            return str(file_path.absolute())
        except Exception as e:
            logger.exception(f"Failed to save file: {str(e)}")
            raise

    def get_file_path(self, user_id: str, source_id: str) -> str:
        """
        Get the path of a stored file.

        Args:
            user_id: User identifier
            source_id: Source identifier

        Returns:
            Absolute file path (returns first file in directory)
        """
        user_dir = self._get_user_dir(user_id, source_id)
        if not user_dir.exists():
            raise FileNotFoundError(f"Directory not found: {user_dir}")

        # Get first file in directory
        files = list(user_dir.glob('*'))
        if not files:
            raise FileNotFoundError(f"No files found in: {user_dir}")

        return str(files[0].absolute())

    def delete_file(self, user_id: str, source_id: str) -> bool:
        """
        Delete a file and its directory from local filesystem.

        Args:
            user_id: User identifier
            source_id: Source identifier

        Returns:
            True if deletion successful, False otherwise
        """
        user_dir = self._get_user_dir(user_id, source_id)
        if not user_dir.exists():
            logger.warning(f"Directory not found for deletion: {user_dir}")
            return False

        try:
            shutil.rmtree(user_dir)
            logger.info(f"Deleted directory: {user_dir}")
            return True
        except Exception as e:
            logger.exception(f"Failed to delete directory: {str(e)}")
            return False

    def file_exists(self, user_id: str, source_id: str) -> bool:
        """
        Check if a file exists in local filesystem.

        Args:
            user_id: User identifier
            source_id: Source identifier

        Returns:
            True if file exists, False otherwise
        """
        user_dir = self._get_user_dir(user_id, source_id)
        return user_dir.exists() and any(user_dir.glob('*'))


class CloudFileStorage(FileStorageBackend):
    """
    Cloud storage backend (Azure Blob, S3, GCS).

    This is a placeholder for future implementation.
    """

    def __init__(self, provider: str, **config):
        """
        Initialize cloud file storage.

        Args:
            provider: Cloud provider ('azure', 's3', 'gcs')
            **config: Provider-specific configuration
        """
        self.provider = provider
        self.config = config
        logger.info(f"Cloud file storage initialized: {provider}")
        raise NotImplementedError("Cloud storage not yet implemented")

    def save_file(self, user_id: str, source_id: str, file_data: BinaryIO, filename: str) -> str:
        raise NotImplementedError("Cloud storage not yet implemented")

    def get_file_path(self, user_id: str, source_id: str) -> str:
        raise NotImplementedError("Cloud storage not yet implemented")

    def delete_file(self, user_id: str, source_id: str) -> bool:
        raise NotImplementedError("Cloud storage not yet implemented")

    def file_exists(self, user_id: str, source_id: str) -> bool:
        raise NotImplementedError("Cloud storage not yet implemented")


class FileStorageManager:
    """
    File storage manager that selects the appropriate backend.
    """

    def __init__(self, backend: str = 'local', **config):
        """
        Initialize file storage manager.

        Args:
            backend: Storage backend ('local' or 'cloud')
            **config: Backend-specific configuration
        """
        if backend == 'local':
            base_path = config.get('local_path', 'data/user_uploads')
            self.storage = LocalFileStorage(base_path)
        elif backend == 'cloud':
            provider = config.get('provider')
            self.storage = CloudFileStorage(provider, **config)
        else:
            raise ValueError(f"Unknown storage backend: {backend}")

        logger.info(f"FileStorageManager initialized with backend: {backend}")

    def save_file(self, user_id: str, source_id: str, file_data: BinaryIO, filename: str) -> str:
        """Save a file using the configured backend."""
        return self.storage.save_file(user_id, source_id, file_data, filename)

    def get_file_path(self, user_id: str, source_id: str) -> str:
        """Get file path using the configured backend."""
        return self.storage.get_file_path(user_id, source_id)

    def delete_file(self, user_id: str, source_id: str) -> bool:
        """Delete a file using the configured backend."""
        return self.storage.delete_file(user_id, source_id)

    def file_exists(self, user_id: str, source_id: str) -> bool:
        """Check if file exists using the configured backend."""
        return self.storage.file_exists(user_id, source_id)


# Global instance for reuse
_storage_manager_instance = None


def get_file_storage_manager(backend: str = 'local', **config) -> FileStorageManager:
    """
    Get or create the global FileStorageManager instance.

    Args:
        backend: Storage backend ('local' or 'cloud')
        **config: Backend-specific configuration

    Returns:
        FileStorageManager instance
    """
    global _storage_manager_instance
    if _storage_manager_instance is None:
        _storage_manager_instance = FileStorageManager(backend, **config)
    return _storage_manager_instance
