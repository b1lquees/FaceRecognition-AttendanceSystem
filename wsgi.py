"""Entry point for both the development server and a production WSGI server.

Development:
    python wsgi.py

Production (never use Flask's built-in server there -- it is single-threaded and its
debugger allows arbitrary code execution):
    gunicorn wsgi:app          # linux/macos
    waitress-serve wsgi:app    # windows
"""

from attendance import create_app

# module-level `app` is the name gunicorn and waitress look for in "wsgi:app"
app = create_app()

if __name__ == "__main__":
    # debug comes from the config class, so FLASK_ENV=production turns it off rather
    # than it being hardcoded on the way app.run(debug=True) used to be
    app.run(debug=app.config["DEBUG"])
