import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "samtal_server.app:app",
        host=os.environ.get("SAMTAL_HOST", "0.0.0.0"),
        port=int(os.environ.get("SAMTAL_PORT", "8003")),
    )


if __name__ == "__main__":
    main()
