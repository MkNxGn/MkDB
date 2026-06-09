"""
MkDB Client CLI — simple command-line interface for interacting with MkDB.
"""
import sys
import json
import argparse
from mkdb_client import MkDBClient

def interactive_shell(client):
    print("MkDB Interactive Shell")
    print("Type 'help' for commands, 'exit' to quit.")
    while True:
        try:
            line = input("mkdb> ").strip()
            if not line: continue
            if line in ("exit", "quit"): break
            
            parts = line.split()
            cmd = parts[0].lower()
            
            if cmd == "help":
                print("Commands: list-stores, get <store> <id>, query <store> <json_filter>, ping, exit")
            elif cmd == "ping":
                print("Pong! (Connected)")
            elif cmd == "list-stores" or cmd == "ls":
                res = client.list_stores()
                if not res.stores:
                    print("No stores found.")
                else:
                    print(f"{'Store Name':<20} | {'Records':<10}")
                    print("-" * 35)
                    for s in res.stores:
                        print(f"{s['name']:<20} | {s['record_count']:<10}")
            elif cmd == "get" and len(parts) == 3:
                resp = client.get(parts[1], parts[2])
                print(json.dumps(resp.data, indent=2) if resp.found else "Not found")
            elif cmd == "query" and len(parts) >= 3:
                filter_str = " ".join(parts[2:])
                try:
                    query_payload = json.loads(filter_str)
                    res = client.query(parts[1], query_payload, hydrate=True)
                    print(json.dumps(res.records, indent=2))
                    print(f"({res.count} shown / {res.total_matches} total matches)")
                except Exception as e:
                    print(f"Query error: {e}")
            else:
                print(f"Unknown command or wrong arguments: {cmd}")
        except EOFError:
            break
        except Exception as e:
            print(f"Error: {e}")

def main():
    parser = argparse.ArgumentParser(description="MkDB Client CLI")
    parser.add_argument("--host", default="127.0.0.1", help="MkDB host")
    parser.add_argument("--port", type=int, default=9001, help="MkDB port")
    parser.add_argument("--user", default="", help="Username")
    parser.add_argument("--password", default="mk_db", help="Password")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start interactive shell")
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Get
    get_p = subparsers.add_parser("get", help="Get a record")
    get_p.add_argument("store", help="Store name")
    get_p.add_argument("id", help="Record ID")
    
    # Query
    query_p = subparsers.add_parser("query", help="Query a store")
    query_p.add_argument("store", help="Store name")
    query_p.add_argument("filter", help="JSON filter string")
    query_p.add_argument("--hydrate", action="store_true", help="Return full records")
    
    # Ping
    subparsers.add_parser("ping", help="Ping the server")

    # List stores
    subparsers.add_parser("list-stores", help="List all stores")

    args = parser.parse_args()
    
    if not args.command and not args.interactive:
        parser.print_help()
        return

    client = MkDBClient(host=args.host, port=args.port, username=args.user, password=args.password)
    try:
        client.connect()
        
        if args.interactive:
            try:
                interactive_shell(client)
            except KeyboardInterrupt:
                print("\nExiting interactive shell.")
            return

        if args.command == "ping":
            # Just connecting is a ping in itself for now, but we can do more
            print(f"Connected to MkDB at {args.host}:{args.port}")
            
        elif args.command == "list-stores":
            res = client.list_stores()
            if not res.stores:
                print("No stores found.")
            else:
                print(json.dumps(res.stores, indent=2))
                
        elif args.command == "get":
            resp = client.get(args.store, args.id)
            if resp.found:
                print(json.dumps(resp.data, indent=2))
            else:
                print(f"Record {args.id} not found in {args.store}")
                sys.exit(1)
                
        elif args.command == "query":
            try:
                filter_dict = json.loads(args.filter)
            except json.JSONDecodeError:
                print("Error: filter must be valid JSON")
                sys.exit(1)
                
            resp = client.query(args.store, filter_dict, hydrate=args.hydrate)
            if args.hydrate:
                print(json.dumps(resp.records, indent=2))
            else:
                print(json.dumps(resp.ids, indent=2))
            print(f"-- Found {resp.count} results --")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        client.close()

if __name__ == "__main__":
    main()
