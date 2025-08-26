import sys
from app import MemoryApp

if __name__ == "__main__":
    try:
        app = MemoryApp()
        app.run()
    except KeyboardInterrupt:
        print("\nbye")
        sys.exit(0)
