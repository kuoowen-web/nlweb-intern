import json
import logging
import sys
import os
import contextvars
from enum import Enum
from typing import Optional, Dict, Any
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# Module-level context var for correlation ID
correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'correlation_id', default=None
)


class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt or "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        corr_id = correlation_id_var.get()
        if corr_id:
            log_entry["correlation_id"] = corr_id
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

# Ensure environment variables are loaded
load_dotenv()

def resolve_log_path(log_file):
    """
    Resolve the log file path based on NLWEB_OUTPUT_DIR if available
    """
    if not log_file:
        return None
        
    # If absolute path, return as is
    if os.path.isabs(log_file):
        return log_file
        
    # Check for NLWEB_OUTPUT_DIR environment variable
    output_dir = os.getenv('NLWEB_OUTPUT_DIR')
    if output_dir:
        # Create logs subdirectory in the output directory
        log_dir = os.path.join(output_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        
        # Get just the filename from the original path
        log_filename = os.path.basename(log_file)
        
        # Return the full path in the output directory
        return os.path.join(log_dir, log_filename)
    
    # Default behavior - use relative path from current directory
    # Make sure logs directory exists
    log_dir = os.path.dirname(log_file) if os.path.dirname(log_file) else "logs"
    os.makedirs(log_dir, exist_ok=True)
    return log_file

class LogLevel(Enum):
    """Enumeration for logging verbosity levels."""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

    @classmethod
    def level_matches(cls, logger_level, message_level):
        """
        Check if a message at message_level should be logged given the logger's logger_level.
        
        Args:
            logger_level: The configured level of the logger
            message_level: The level of the message being logged
            
        Returns:
            True if the message should be logged, False otherwise
        """
        return message_level.value >= logger_level.value


class LoggerUtility:
    """A configurable logging utility with different verbosity levels."""

    # Shared unified log handler — all modules write to nlweb.log
    _unified_handler: Optional[RotatingFileHandler] = None

    @classmethod
    def _get_unified_handler(cls) -> Optional[RotatingFileHandler]:
        """Get or create the shared nlweb.log handler."""
        if cls._unified_handler is None:
            try:
                log_dir = resolve_log_path("logs/nlweb.log")
                log_dir_path = os.path.dirname(log_dir)
                if log_dir_path:
                    os.makedirs(log_dir_path, exist_ok=True)
                cls._unified_handler = RotatingFileHandler(
                    log_dir,
                    mode='a',
                    maxBytes=10485760,  # 10MB
                    backupCount=5,
                    encoding='utf-8',
                    delay=True,
                )
                cls._unified_handler.setFormatter(JsonFormatter())
                cls._unified_handler.setLevel(logging.INFO)
            except Exception as e:
                print(f"Error setting up unified log file: {e}")
                return None
        return cls._unified_handler

    def __init__(
        self,
        name: str = "AppLogger",
        level: LogLevel = LogLevel.ERROR,
        format_string: Optional[str] = None,
        log_file: Optional[str] = None,
        console_output: bool = True
    ):
        """
        Initialize the logger utility.
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level.value)
        self.logger.propagate = False  # Prevent duplicate logs

        # Store the current level for reference
        self._current_level = level

        # Clear any existing handlers
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)

        # Always use JsonFormatter; format_string is accepted but ignored in favour of JSON
        json_formatter = JsonFormatter()

        # Console + per-module file handlers are floored at INFO so module
        # default_level=INFO entries reach them even when a global/profile
        # level (e.g. NLWEB_LOGGING_PROFILE=production -> ERROR) is in effect.
        # Without this floor, INFO entries pass the logger (lowered to INFO by
        # the unified handler block below) but get filtered by per-handler
        # level=ERROR, so per-module log_file never receives anything.
        handler_level = min(level.value, logging.INFO)

        # Console handler
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(json_formatter)
            console_handler.setLevel(handler_level)
            self.logger.addHandler(console_handler)

        # Per-module file handler - with path resolution
        if log_file:
            resolved_log_file = resolve_log_path(log_file)
            log_dir = os.path.dirname(resolved_log_file)

            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            try:
                # Use delay=True to defer file opening until first write
                file_handler = RotatingFileHandler(
                    resolved_log_file,
                    mode='a',
                    maxBytes=10485760,  # 10MB
                    backupCount=5,
                    delay=True  # Defer file opening until first write
                )
                file_handler.setFormatter(json_formatter)
                file_handler.setLevel(handler_level)
                self.logger.addHandler(file_handler)
            except Exception as e:
                print(f"Error setting up log file {resolved_log_file}: {e}")

        # Unified nlweb.log handler — shared across all modules, always at INFO
        unified = self._get_unified_handler()
        if unified and unified not in self.logger.handlers:
            self.logger.addHandler(unified)
            # Ensure logger level is low enough for unified handler to receive INFO+
            if self.logger.level > logging.INFO:
                self.logger.setLevel(logging.INFO)
    
    def set_level(self, level: LogLevel):
        """Set the logging verbosity level."""
        self._current_level = level
        self.logger.setLevel(level.value)
    
    def get_level(self) -> LogLevel:
        """Get the current logging level."""
        return self._current_level
    
    def debug(self, message: str, *args, **kwargs):
        """Log a debug message."""
        self.logger.debug(message, *args, **kwargs)
    
    def info(self, message: str, *args, **kwargs):
        """Log an info message."""
        self.logger.info(message, *args, **kwargs)
    
    def warning(self, message: str, *args, **kwargs):
        """Log a warning message."""
        self.logger.warning(message, *args, **kwargs)
    
    def error(self, message: str, *args, **kwargs):
        """Log an error message."""
        self.logger.error(message, *args, **kwargs)
    
    def critical(self, message: str, *args, **kwargs):
        """Log a critical message."""
        self.logger.critical(message, *args, **kwargs)
    
    def _force_flush(self):
        """Force all handlers to flush their buffers."""
        for handler in self.logger.handlers:
            try:
                handler.flush()
            except Exception:
                pass
    
    def exception(self, message: str, **kwargs):
        """Log an exception with traceback."""
        self.logger.exception(message, **kwargs)
    
    def log_with_context(self, level: LogLevel, message: str, context: Dict[str, Any]):
        """
        Log a message with additional context information.
        
        Args:
            level: Log level
            message: Log message
            context: Dictionary of context information
        """
        if LogLevel.level_matches(self.get_level(), level):
            context_str = " - ".join(f"{k}={v}" for k, v in context.items())
            full_message = f"{message} | Context: {context_str}"
            self.logger.log(level.value, full_message)

def setup_logger(name, config=None):
    """
    Set up logger with the correct path based on environment variables
    """
    logger = logging.getLogger(name)
    
    # Default log directory and path
    default_log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    default_log_path = os.path.join(default_log_dir, f"{name}.log")
    
    # Check for environment variable and override if exists
    base_output_dir = os.getenv('NLWEB_OUTPUT_DIR')
    if base_output_dir:
        log_dir = os.path.join(base_output_dir, "logs")
        log_path = os.path.join(log_dir, f"{name}.log")
        print(f"Using environment-based log path: {log_path}")
    else:
        log_dir = default_log_dir
        log_path = default_log_path
        print(f"Using default log path: {log_path}")
    
    # Create log directory if it doesn't exist
    os.makedirs(log_dir, exist_ok=True)
    
    # Configure handler with the correct path
    handler = RotatingFileHandler(log_path, maxBytes=10485760, backupCount=5)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)  # Or from config
    
    return logger

@lru_cache(maxsize=None)
def get_logger(
    name: str,
    level_env_var: str = "LOG_LEVEL",
    default_level: LogLevel = LogLevel.ERROR,
    log_file: Optional[str] = None
) -> LoggerUtility:
    """
    Get or create a logger instance with caching.
    
    Args:
        name: Logger name
        level_env_var: Environment variable name for log level
        default_level: Default log level if env var not set
        log_file: Optional log file path
    
    Returns:
        LoggerUtility instance
    """
    # Check environment variable for log level
    env_level = os.getenv(level_env_var, "").upper()
    level = default_level
    
    if env_level:
        try:
            level = LogLevel[env_level]
        except KeyError:
            # Invalid env value, use default
            pass
    
    # Create logger
    return LoggerUtility(
        name=name,
        level=level,
        log_file=log_file,
        console_output=True
    )


# Configuration-based logger getter
def get_logger_from_config(module_name: str, config_path: str = "config/logging_config.yaml") -> LoggerUtility:
    """
    Get a logger configured from YAML configuration file.
    
    Args:
        module_name: Name of the module
        config_path: Path to the YAML configuration file
    
    Returns:
        LoggerUtility instance
    """
    try:
        from .logging_config_helper import get_configured_logger
        return get_configured_logger(module_name)
    except ImportError:
        # Fallback to default logger if config helper not available
        return get_logger(module_name)


# Example usage
if __name__ == "__main__":
    # Create logger with default settings
    logger = LoggerUtility("MyApp")
    
    # Test different log levels
    logger.debug("This is a debug message")
    logger.info("This is an info message")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    logger.critical("This is a critical message")
    
    print("\n--- Changing to DEBUG level ---")
    logger.set_level(LogLevel.DEBUG)
    
    logger.debug("Now debug messages are visible")
    logger.info("Info still visible")
    
    print("\n--- Changing to ERROR level ---")
    logger.set_level(LogLevel.ERROR)
    
    logger.debug("Debug not visible at ERROR level")
    logger.info("Info not visible at ERROR level")
    logger.error("Error messages are still visible")
    
    print("\n--- Using context logging ---")
    logger.set_level(LogLevel.INFO)
    logger.log_with_context(
        LogLevel.INFO,
        "User action",
        {"user_id": 123, "action": "login", "ip": "192.168.1.1"}
    )
    
    print("\n--- Creating a file logger ---")
    file_logger = LoggerUtility(
        "FileLogger",
        level=LogLevel.DEBUG,
        log_file="app.log",
        format_string='%(asctime)s [%(levelname)8s] %(message)s'
    )
    
    file_logger.info("This message goes to both console and file")
    file_logger.debug("Debug message in custom format")
    
    # Demonstrate exception logging
    try:
        result = 1 / 0
    except Exception as e:
        file_logger.exception("An error occurred during calculation")