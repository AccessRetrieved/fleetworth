"""Minimal Flask entry point for the camera-preview interface."""

from flask import Flask, render_template


app = Flask(__name__)


@app.get("/")
def index():
    """Render the client-side camera preview page."""
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True)
