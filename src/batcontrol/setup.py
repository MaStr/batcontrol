import logging
import sys
import os
import yaml
from logging.handlers import RotatingFileHandler

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
        RuntimeError: If the config file is not found or no PV installations are found

    """
    if not os.path.isfile(configfile):
        raise RuntimeError(f'Configfile {configfile} not found')

    with open(configfile, 'r', encoding='UTF-8') as f:
        config_str = f.read()

    config = yaml.safe_load(config_str)

    if not isinstance(config, dict):
        raise RuntimeError(f'Configfile {configfile} is empty or not a valid YAML mapping')

    # Validate and coerce types via Pydantic before any other checks
    config = validate_config(config)

    if not config.get('pvinstallations'):
        raise RuntimeError('No PV Installation found')

    return config
