import os
import sys

def run_server():
    import uvicorn

    host = os.getenv("SHIYE_HOST", "127.0.0.1")
    port = int(os.getenv("SHIYE_PORT", "8000"))
    reload = os.getenv("SHIYE_RELOAD", "false").lower() in {"1", "true", "yes"}
    uvicorn.run("web:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        print("\nbye")
        sys.exit(0)
