import asyncio
import logging
import threading
from typing import Callable, Optional
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as _HTTPServer
from urllib.parse import urlparse



class HTTPServer(_HTTPServer):
    """Production-ready HTTP server with multi-threaded request handling."""

    def __init__(self, name:str, host:str, port:int, responder:BaseHTTPRequestHandler):
        """Initialize HTTP server.
        
        Args:
            name: Server name
            host: Server host address
            port: Server port
        """
        self.host = host
        self.port = port
        self.name = name
        self.logger = logging.getLogger(f"HTTPServer-{name}")
        super().__init__((host, port), responder) #type: ignore
        self.logger.info(f"HTTP Server initialized on {host}:{port}")
        threading.Thread(target=self.run, daemon=True).start()

    def run(self) -> None:
        """Start the HTTP server."""
        print(f"Starting {self.name} HTTP Server on {self.host}:{self.port}")
        self.logger.info(f"Starting HTTP Server on {self.host}:{self.port}")
        try:
            self.serve_forever()
        except KeyboardInterrupt:
            self.logger.info("Server interrupted by user")
            self.shutdown()
        except Exception as e:
            self.logger.error(f"Server error: {e}", exc_info=True)
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully shutdown the server."""
        self.logger.info("Shutting down HTTP Server")
        super().shutdown()   # signals serve_forever() to stop
        self.server_close()  # closes the server socket
