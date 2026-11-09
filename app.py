from flask import Flask, redirect

from database import get_link_by_short_code


app = Flask(__name__)


@app.route("/")
def home():
    return "Telegram URL Shortener Bot is running"


@app.route("/<short_code>")
def open_short_link(short_code):
    link = get_link_by_short_code(short_code)

    if link is None:
        return "Link not found", 404

    original_url = link[2]

    return redirect(original_url)


if __name__ == "__main__":
    app.run(debug=True)