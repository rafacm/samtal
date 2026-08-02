import argparse
import sys

import uvicorn
from dotenv import find_dotenv, load_dotenv

from samtal_server.app import create_app
from samtal_server.config import ConfigError, load_config
from samtal_server.config.loader import CONFIG_ENV_VAR


def main() -> None:
    # Read a .env file into the environment before anything looks at it, so
    # it can carry SAMTAL_* overrides, SAMTAL_CONFIG, and provider secrets.
    # Real environment variables keep priority over .env values. usecwd makes
    # the search start from the invocation directory, not this file's.
    load_dotenv(find_dotenv(usecwd=True))

    parser = argparse.ArgumentParser(prog="samtal-server")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=f"path to the YAML config file (default: ${CONFIG_ENV_VAR})",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None

    # Pass the app object rather than an import string: the config just read
    # (from --config, which reaches nothing else) has to be the one the app
    # serves from.
    uvicorn.run(
        create_app(config),
        host=config.server.host,
        port=config.server.port,
    )


if __name__ == "__main__":
    main()
