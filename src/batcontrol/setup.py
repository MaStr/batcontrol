import logging
import sys
import os
import yaml
from logging.handlers import RotatingFileHandler

from pydantic import ValidationError

from .config_model import validate_config


def setup_logging(level=logging.INFO, logfile=None, max_logfile_size_kb=200):
    """Configure root logger with consistent formatting.
    
    Args:
        level (int): Log level to set for the root logger.
        logfile (str): If specified, log to this file as well as the console.
        max_logfile_size_kb (int): Maximum log file size in kilobytes before rotation.

    Returns:
        logging.Logger: Root logger
    """
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Clear existing handlers to avoid duplicates
    root_logger.handlers = []
    
    # Create formatter with module name included
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler if specified
    if logfile:
        if not os.path.exists(os.path.dirname(logfile)):
            os.makedirs(os.path.dirname(logfile))
        # Convert KB to bytes for RotatingFileHandler
        max_bytes = max_logfile_size_kb * 1024
        file_handler = RotatingFileHandler(logfile, maxBytes=max_bytes, backupCount=2)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

def load_config(configfile:str) -> dict:
    """ Load the configuration file and check for validity.

    This maps some config entries for compatibility reasons.

    Args:
        configfile (str): Path to the config file
    
    Returns:
        dict: The loaded configuration
        
    Raises:
        RuntimeError: If the config file is not found, config is invalid/malformed, or no PV installations are found

    """
    if not os.path.isfile(configfile):
        raise RuntimeError(f'Configfile {configfile} not found')

    with open(configfile, 'r', encoding='UTF-8') as f:
        config_str = f.read()

    try:
        config = yaml.safe_load(config_str)
    except yaml.YAMLError as exc:
        raise RuntimeError(f'Configfile {configfile} is not valid YAML: {exc}') from exc

    if not isinstance(config, dict):
        raise RuntimeError(f'Configfile {configfile} is empty or not a valid YAML mapping')

    # Validate and coerce types via Pydantic before any other checks.
    # Re-raise ValidationError as RuntimeError to keep callers' expected error type.
    try:
        config = validate_config(config)
    except ValidationError as exc:
        # Build a sanitized error: include field path and error type but NOT
        # the raw input values (which can contain secrets like passwords).
        details = '; '.join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise RuntimeError(f'Config validation failed: {details}') from exc

    if not config.get('pvinstallations'):
        raise RuntimeError('No PV Installation found')

    return config
