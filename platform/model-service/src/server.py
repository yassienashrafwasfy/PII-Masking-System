"""Run one private team model service for the Compose demo."""

from wsgiref.simple_server import make_server

from service import application

if __name__ == "__main__":
    with make_server("0.0.0.0", 8080, application) as server:
        server.serve_forever()
