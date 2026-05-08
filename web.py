from flask import Flask, render_template

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/farmer-profiles")
def farmer_profiles():
    return render_template("farmer_profiles.html")


@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/report")
def report():
    return render_template("report.html")


@app.route("/news-updates")
def news_updates():
    return render_template("news_updates.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
