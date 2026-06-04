"""
Development launcher — delegates to the packaged CLI entry point.
When installed via pip, use the `mkdb` command directly instead.
"""

from pymkdb.cli import main

if __name__ == "__main__":
    main()
