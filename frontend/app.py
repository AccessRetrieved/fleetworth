"""Minimal Flask entry point for Fleetworth's guided-capture frontend."""

from flask import Flask, render_template


app = Flask(__name__)


@app.get("/")
def index():
    """Render the client-side guided truck capture page."""
    return render_template("index.html")


if __name__ == "__main__":
    # 0.0.0.0 so other devices on the same network can load this page (and
    # reach the backend, which the frontend JS targets dynamically based on
    # whatever host/IP was used to load this page — see apiOrigin() in
    # static/app.js).
    app.run(host="0.0.0.0", debug=True)
