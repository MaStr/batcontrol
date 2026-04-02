"""Batcontrol entry point: parses arguments, sets up logging, and runs the main loop."""
import argparse
import time
import datetime
import sys
import logging

from .core import Batcontrol
from .setup import setup_logging, load_config
from .inverter import InverterOutageError
from . import mcp_server as mcp_module


CONFIGFILE = "config/batcontrol_config.yaml"
EVALUATIONS_EVERY_MINUTES = 3  # Every x minutes on the clock
LOGFILE_ENABLED_DEFAULT = True
LOGFILE = "logs/batcontrol.log"


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog='batcontrol',
        description='Battery control system for dynamic tariff optimization'
    )
    parser.add_argument(
        '--one-shot',
        action='store_true',
        help='Run once and exit (load config, fetch data, run main loop once, then exit)'
    )
    parser.add_argument(
        '--config', '-c',
        default=CONFIGFILE,
        help=f'Path to configuration file (default: {CONFIGFILE})'
    )
    parser.add_argument(
        '--mcp-stdio',
        action='store_true',
        help='Run MCP server with stdio transport (for direct integration with AI tools)'
    )
    return parser.parse_args()


def main() -> int:  # pylint: disable=too-many-locals,too-many-statements
    """Run batcontrol: load config, set up logging, and start the main loop."""
    # Parse command line arguments
    args = parse_arguments()

    # Configure a basic logger to be able to log even before the configuration is loaded
    setup_logging(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info('Looking for config file at %s', args.config)

    # Load the configuration
    config = load_config(args.config)

    # Load the config for the logger with the loaded configuration
    loglevel = config.get('loglevel', 'info')
    logfile_enabled = config.get('logfile_enabled', LOGFILE_ENABLED_DEFAULT)
    log_everything = config.get('log_everything', False)
    max_logfile_size = config.get('max_logfile_size', 200)  # Default 200KB
    logfile_path = config.get('logfile_path', LOGFILE)
    logfile = logfile_path if logfile_enabled else None

    if not logfile_enabled:
        logger.info("Logfile disabled in config. Proceeding without logfile")

    # Establish the loglevel mapping
    loglevel_mapping = {
        'debug': logging.DEBUG,
        'warning': logging.WARNING,
        'error': logging.ERROR,
        'info': logging.INFO
    }

    # Setup the logger based on the config
    setup_logging(
        level=loglevel_mapping.get(loglevel, logging.INFO),
        logfile=logfile,
        max_logfile_size_kb=max_logfile_size,
    )
    logger = logging.getLogger(__name__)

    # Reduce the default loglevel for urllib3.connectionpool
    if not log_everything:
        logging.getLogger("websockets.protocol").setLevel(logging.WARNING)
        logging.getLogger("websockets.client").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
        logging.getLogger("batcontrol.inverter.fronius.auth").setLevel(logging.INFO)
        ha_base = "batcontrol.forecastconsumption.forecast_homeassistant"
        logging.getLogger(f"{ha_base}.details").setLevel(logging.INFO)
        logging.getLogger(f"{ha_base}.communication").setLevel(logging.INFO)

    # When using stdio transport, prevent core.py from starting an HTTP MCP server.
    # Both would share the same Batcontrol instance and compete for the port.
    # Copy the mcp section to avoid mutating the dict stored in bc.config later.
    if args.mcp_stdio:
        config['mcp'] = {**config.get('mcp', {}), 'enabled': False}

    bc = Batcontrol(config)

    # Handle --mcp-stdio: run MCP server in stdio mode (blocking)
    if args.mcp_stdio:
        if not mcp_module.is_available():
            logger.error(
                'MCP server requires the "mcp" package (Python >=3.10). '
                'Install with: pip install batcontrol[mcp]')
            bc.shutdown()
            del bc
            return 1
        logger.info("Running MCP server in stdio mode")
        mcp = mcp_module.BatcontrolMcpServer(bc, config.get('mcp', {}))
        try:
            mcp.run_stdio()
        except KeyboardInterrupt:
            pass
        finally:
            bc.shutdown()
            del bc
        return 0

    try:
        while True:
            logger.info("Starting batcontrol")
            bc.run()

            # Exit after first run if --one-shot is specified
            if args.one_shot:
                logger.info("One-shot mode: exiting after single run")
                break

            loop_now = datetime.datetime.now().astimezone(bc.timezone)
            # reset base to full minutes on the clock
            next_eval = loop_now - datetime.timedelta(
                minutes=loop_now.minute % EVALUATIONS_EVERY_MINUTES,
                seconds=loop_now.second,
                microseconds=loop_now.microsecond
            )
            # add time increments to trigger next evaluation
            next_eval += datetime.timedelta(minutes=EVALUATIONS_EVERY_MINUTES)
            sleeptime = (next_eval - loop_now).total_seconds()
            logger.info(
                "Next evaluation at %s. Sleeping for %d seconds",
                next_eval.strftime('%H:%M:%S'), int(sleeptime))
            time.sleep(sleeptime)
    except KeyboardInterrupt:
        print("Shutting down")
    except InverterOutageError as e:
        logger.critical(
            "Inverter has been unreachable for too long. "
            "Terminating batcontrol. Error: %s", e
        )
        print(f"FATAL: Inverter outage exceeded tolerance - {e}")
        return 1
    finally:
        bc.shutdown()
        del bc
    return 0

if __name__ == "__main__":
    sys.exit(main())
