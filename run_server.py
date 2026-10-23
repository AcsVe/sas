"""Production entry point (also the PyInstaller target): waitress, one
process, several threads — same model as the venues local server."""
import os

from waitress import serve

from app import create_app

if __name__ == '__main__':
    serve(create_app(),
          host=os.environ.get('HOST', '127.0.0.1'),
          port=int(os.environ.get('PORT', '8080')),
          threads=int(os.environ.get('THREADS', '8')))
