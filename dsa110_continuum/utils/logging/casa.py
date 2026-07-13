"""CASA logging integration for DSA-110 continuum imaging pipeline.

This module provides handlers and adapters for routing Python logs to CASA's
global logger, ensuring pipeline logs and CASA task logs appear in the same
stream.

Classes
-------
CasaLogHandler : logging.Handler
    Routes Python logs to CASA's casalog
DsaSyslogger : object
    Adapter compatible with dsautils.dsa_syslog.DsaSyslogger

Functions
---------
exception_logger : Log exception and optionally re-raise
warning_logger : Log warning message
"""

import logging
import sys


class CasaLogHandler(logging.Handler):
    """Custom logging handler that routes Python logs to CASA's global logger.

    This ensures that pipeline logs and CASA task logs appear in the same
    stream and log file (e.g., casa-YYYY.log).

    Parameters
    ----------
    origin : str
        Origin identifier for CASA log messages (default: "dsa110-contimg")

    Examples
    --------
    >>> import logging
    >>> logger = logging.getLogger("my_task")
    >>> handler = CasaLogHandler(origin="my_task")
    >>> logger.addHandler(handler)
    """

    def __init__(self, origin: str = "dsa110-contimg"):
        super().__init__()
        self.origin = origin
        try:
            from dsa110_continuum.adapters.casa import casa_adapter

            if casa_adapter.is_available:
                self._casalog = casa_adapter.casalog()
                self._available = True
            else:
                self._available = False
        except (ImportError, RuntimeError):
            self._available = False

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to CASA's logger.

        Parameters
        ----------
        record : logging.LogRecord
            The log record to emit
        """
        if not self._available:
            return

        try:
            msg = self.format(record)
            # Map Python log levels to CASA priorities
            if record.levelno >= logging.CRITICAL:
                priority = "SEVERE"
            elif record.levelno >= logging.ERROR:
                priority = "SEVERE"
            elif record.levelno >= logging.WARNING:
                priority = "WARN"
            elif record.levelno >= logging.INFO:
                priority = "INFO"
            else:
                priority = "DEBUG"

            self._casalog.post(msg, priority=priority, origin=self.origin)
        except Exception:
            self.handleError(record)


class DsaSyslogger:
    """Simplified logger for DSA-110 continuum imaging pipeline.

    This is a lightweight adapter that provides a compatible interface
    with dsautils.dsa_syslog.DsaSyslogger but uses standard Python logging.
    It includes integration with CASA logging.

    Parameters
    ----------
    proj_name : str
        Project name (default: 'dsa110-contimg')
    subsystem_name : str
        Subsystem name (default: 'conversion')
    log_level : int
        Logging level (default: logging.INFO)
    logger_name : str
        Logger name (default: __name__)
    log_stream : file-like, optional
        Output stream (default: sys.stdout)

    Examples
    --------
    >>> logger = DsaSyslogger(
    ...     proj_name="dsa110-contimg",
    ...     subsystem_name="conversion"
    ... )
    >>> logger.info("Starting conversion")
    """

    def __init__(
        self,
        proj_name: str = "dsa110-contimg",
        subsystem_name: str = "conversion",
        log_level: int = logging.INFO,
        logger_name: str = __name__,
        log_stream=None,
    ):
        self.proj_name = proj_name
        self.subsystem_name = subsystem_name
        self._log_level = log_level

        # Create logger
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(log_level)

        # Add handlers if not already present
        if not self.logger.handlers:
            # 1. Console Handler
            if log_stream is None:
                log_stream = sys.stdout

            console_handler = logging.StreamHandler(log_stream)
            console_handler.setLevel(log_level)
            formatter = logging.Formatter(
                f"%(asctime)s - {proj_name}/{subsystem_name} - "
                f"%(levelname)s - %(message)s"
            )
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

            # 2. CASA Handler (routes logs to casalog)
            try:
                casa_handler = CasaLogHandler(
                    origin=f"{proj_name}/{subsystem_name}"
                )
                # We typically want INFO+ to go to CASA logs
                casa_handler.setLevel(max(log_level, logging.INFO))
                self.logger.addHandler(casa_handler)
            except ImportError:
                pass  # CASA not available, skip handler

    def subsystem(self, name: str) -> None:
        """Set the subsystem name.

        Parameters
        ----------
        name : str
            New subsystem name
        """
        self.subsystem_name = name

    def level(self, level: int) -> None:
        """Set the logging level.

        Parameters
        ----------
        level : int
            New logging level (e.g., logging.DEBUG)
        """
        self._log_level = level
        self.logger.setLevel(level)

    def debug(self, event: str) -> None:
        """Log a debug message."""
        self.logger.debug(event)

    def info(self, event: str) -> None:
        """Log an info message."""
        self.logger.info(event)

    def warning(self, event: str) -> None:
        """Log a warning message."""
        self.logger.warning(event)

    def error(self, event: str) -> None:
        """Log an error message."""
        self.logger.error(event)

    def critical(self, event: str) -> None:
        """Log a critical message."""
        self.logger.critical(event)


def exception_logger(
    logger,
    task: str,
    exception: Exception,
    throw: bool,
) -> None:
    """Log an exception and optionally re-raise it.

    Parameters
    ----------
    logger : DsaSyslogger or logging.Logger
        Logger instance
    task : str
        Description of the task that failed
    exception : Exception
        The exception that occurred
    throw : bool
        Whether to re-raise the exception

    Raises
    ------
    Exception
        Re-raises the exception if throw=True
    """
    error_msg = (
        f"{task} failed with exception: "
        f"{type(exception).__name__}: {str(exception)}"
    )

    if hasattr(logger, "error"):
        logger.error(error_msg)
    else:
        logging.error(error_msg)

    if throw:
        raise exception


def warning_logger(logger, message: str) -> None:
    """Log a warning message.

    Parameters
    ----------
    logger : DsaSyslogger or logging.Logger
        Logger instance
    message : str
        Warning message
    """
    if hasattr(logger, "warning"):
        logger.warning(message)
    else:
        logging.warning(message)
