"""PyInstaller entry shim for halbot daemon."""
from halbot.daemon import main

if __name__ == "__main__":
    raise SystemExit(main())
