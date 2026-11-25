"""Centralized logging configuration."""
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime


def setup_logging(log_dir: str = "logs", 
                  log_level: str = "INFO",
                  bot_name: str = "QuantTradingBot") -> logging.Logger:
    """Setup centralized logging with console and rotating file handlers.
    
    Creates two handlers:
    - Console: INFO level, colored format
    - File: DEBUG level, detailed format, daily rotation
    
    Returns:
        Root logger configured for the application
    """
    try:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # Root logger
        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)
        
        # Clear existing handlers
        logger.handlers.clear()
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        console_format = logging.Formatter(
            fmt='[%(asctime)s] %(levelname)-8s %(name)-25s %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_format)
        logger.addHandler(console_handler)
        
        # File handler with daily rotation
        log_file = log_path / f"{bot_name.lower()}.log"
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_file),
            when='midnight',
            interval=1,
            backupCount=30,  # Keep 30 days
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            fmt='[%(asctime)s] %(levelname)-8s %(name)-30s %(funcName)-20s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
        
        logger.info(f"{bot_name} logging initialized - Level: {log_level}, File: {log_file}")
        return logger
    except Exception as e:
        print(f"Failed to setup logging: {e}")
        # Fallback to simple console logging
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger()


def get_logger(name: str) -> logging.Logger:
    """Get a named logger (convenience function)."""
    return logging.getLogger(name)
