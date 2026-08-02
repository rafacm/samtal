import argparse
import sys

import uvicorn

from samtal_server.config import ConfigError, load_config
from samtal_server.config.loader import CONFIG_ENV_VAR


def main() -> None:
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

    uvicorn.run(
        "samtal_server.app:app",
        host=config.server.host,
        port=config.server.port,
    )


if __name__ == "__main__":
    main()
