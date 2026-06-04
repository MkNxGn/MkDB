"""
pymkdb.cli — console entry point for the `mkdb` command.

Usage:
    mkdb [PATH_TO_DB]          Start the server pointing at a database directory
    mkdb [PATH_TO_DB] -c       Generate a default config.json in that directory
"""

import os
import sys


def main():
    from colorama import Fore
    from src.db import mkdb
    from src.config.db import mkdb_config
    from src.filing import read_json, write_json
    from src.runtime import runtime_settings

    print(f"""{Fore.CYAN}
    ╔═══════════════════════════════════════╗
    ║                                       ║
    ║             Initializing              ║
    ║                 MkDB                  ║
    ║                                       ║
    ╚═══════════════════════════════════════╝
    {Fore.RESET}""")

    try:
        print("Initialized CWD:", os.getcwd())
        pointer = sys.argv[1] if len(sys.argv) > 1 else runtime_settings.args.config
        print("Pointing to:", pointer)
        print(f"{Fore.CYAN}Reading Config...{Fore.RESET}")
        if not pointer.endswith(".json"):
            os.chdir(pointer)
        CONFIG = read_json(runtime_settings.args.config)
    except FileNotFoundError:
        CONFIG = {}
        if "-c" in sys.argv or "--generate-config" in sys.argv:
            print(f"{Fore.GREEN}Generating default config file at "
                  f"{runtime_settings.args.config}{Fore.RESET}")
            write_json(runtime_settings.args.config, mkdb_config().json)
            sys.exit(0)
        print(f"{Fore.YELLOW}Config file not found at {runtime_settings.args.config}")
        print(f"{Fore.BLUE}Use a path to a db directory or -c to generate a "
              f"new config.{Fore.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"{Fore.RED}Error loading config: {e}{Fore.RESET}")
        sys.exit(1)

    DATA_BASE = mkdb(CONFIG)
    DATA_BASE.run()


if __name__ == "__main__":
    main()
