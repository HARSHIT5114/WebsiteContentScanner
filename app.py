from __future__ import annotations

from flask import Flask, jsonify, render_template, request

from scanner import ContentScanner

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/sample")
def sample():
    return render_template("sample.html")


@app.post("/api/scan")
def scan():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or request.form.get("url") or "").strip()
    max_pages = payload.get("max_pages", request.form.get("max_pages", 5))
    check_links = payload.get("check_links", True)
    if isinstance(check_links, str):
        check_links = check_links.lower() in {"1", "true", "yes", "on"}

    if not url:
        return jsonify({"error": "Enter a website URL to scan."}), 400

    try:
        max_pages = int(max_pages)
    except (TypeError, ValueError):
        max_pages = 5

    try:
        result = ContentScanner(url, max_pages=max_pages, check_links=bool(check_links)).scan()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Scan failed: {exc}"}), 500

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5050, threaded=True)
