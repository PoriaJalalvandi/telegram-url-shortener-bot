from flask import Flask, redirect

app = Flask(__name__)


@app.route("/")
def home():
    return "Telegram URL Shortener Bot is running"


@app.route("/<short_code>")
def open_short_link(short_code):
    print(f"Short code received: {short_code}")

    return redirect("https://example.com")


if __name__ == "__main__":
    app.run(debug=True)