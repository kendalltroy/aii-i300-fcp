import os
import json
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

BRIGHTDATA_API_KEY = os.environ.get("BRIGHTDATA_API_KEY", "")
BRIGHTDATA_ZONE = os.environ.get("BRIGHTDATA_ZONE", "serp")
BRIGHTDATA_HOST = "brd.superproxy.io"
BRIGHTDATA_PORT = int(os.environ.get("BRIGHTDATA_PORT", "33335"))


def get_proxy_url():
    return (
        f"http://{BRIGHTDATA_ZONE}:{BRIGHTDATA_API_KEY}"
        f"@{BRIGHTDATA_HOST}:{BRIGHTDATA_PORT}"
    )


def search_google_shopping(query, num_results=8):
    """Search Google Shopping via Bright Data Web Unlocker proxy."""
    proxy = get_proxy_url()
    url = "https://www.google.com/search"
    params = {
        "q": query,
        "tbm": "shop",
        "gl": "us",
        "hl": "en",
        "num": num_results,
    }

    resp = requests.get(
        url,
        params=params,
        proxies={"http": proxy, "https": proxy},
        verify=False,
        timeout=30,
    )
    resp.raise_for_status()
    return parse_shopping_results(resp.text)


def parse_shopping_results(html):
    """Extract product cards from Google Shopping HTML."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    # Google Shopping results appear in various container formats.
    # We look for the most common patterns.
    for item in soup.select(".sh-dgr__gr-auto, .sh-dlr__list-result, .sh-np__click-target"):
        product = extract_product(item)
        if product:
            products.append(product)

    # Fallback: broader search for product-like cards
    if not products:
        for item in soup.select("[data-docid], .sh-pr__product-results-grid div[data-prmn]"):
            product = extract_product(item)
            if product:
                products.append(product)

    # Final fallback: look for any card with price + link
    if not products:
        for link in soup.select("a[href*='/shopping/product/'], a[href*='url?q=']"):
            name = link.get_text(strip=True)
            href = link.get("href", "")
            if name and len(name) > 3:
                # Extract the actual URL from Google's redirect
                actual_url = extract_redirect_url(href)
                products.append({
                    "name": name[:120],
                    "imageUrl": None,
                    "pageUrl": actual_url,
                    "source": extract_domain(actual_url),
                    "price": None,
                })

    return products[:8]


def extract_product(item):
    """Parse a single product card element."""
    # Product name
    name_el = (
        item.select_one("h3")
        or item.select_one("h4")
        or item.select_one("[aria-label]")
        or item.select_one(".tAxDx, .EI11Pd, .Xjkr3b")
    )
    name = name_el.get_text(strip=True) if name_el else None
    if not name or len(name) < 2:
        return None

    # Price
    price = None
    price_el = (
        item.select_one(".a8Pemb, .HRLxBb, .kHxwFf")
        or item.select_one("span[aria-label*='$']")
        or item.select_one("b")
    )
    if price_el:
        price_text = price_el.get_text(strip=True)
        if "$" in price_text:
            price = price_text

    # Image
    img_url = None
    img_el = item.select_one("img[src*='http'], img[data-src*='http']")
    if img_el:
        img_url = img_el.get("src") or img_el.get("data-src")

    # Link
    page_url = None
    link_el = item.select_one("a[href]")
    if link_el:
        href = link_el.get("href", "")
        page_url = extract_redirect_url(href)

    # Merchant/source
    source = ""
    merchant_el = item.select_one(".aULzUe, .IuHnof, .E5ocAb")
    if merchant_el:
        source = merchant_el.get_text(strip=True)
    elif page_url:
        source = extract_domain(page_url)

    if not page_url:
        return None

    return {
        "name": name[:120],
        "imageUrl": img_url,
        "pageUrl": page_url,
        "source": source,
        "price": price,
    }


def extract_redirect_url(href):
    """Pull the real URL out of Google's redirect wrapper."""
    if "/url?q=" in href or "url?q=" in href:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        if "q" in qs:
            return qs["q"][0]
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.google.com" + href
    return href


def extract_domain(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname.replace("www.", "")
    except Exception:
        return ""


@app.route("/api/search", methods=["POST"])
def api_search():
    """Search for similar products based on a text query."""
    if not BRIGHTDATA_API_KEY:
        return jsonify({"error": "BRIGHTDATA_API_KEY not configured"}), 500

    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        products = search_google_shopping(query)
        return jsonify({"results": products})
    except requests.exceptions.ProxyError as e:
        return jsonify({"error": "Bright Data proxy error — check your API key and zone", "detail": str(e)}), 502
    except requests.exceptions.Timeout:
        return jsonify({"error": "Search timed out — try again"}), 504
    except Exception as e:
        return jsonify({"error": "Search failed", "detail": str(e)}), 500


@app.route("/api/search-batch", methods=["POST"])
def api_search_batch():
    """Search for multiple items (one query per wardrobe item)."""
    if not BRIGHTDATA_API_KEY:
        return jsonify({"error": "BRIGHTDATA_API_KEY not configured"}), 500

    data = request.get_json(silent=True) or {}
    queries = data.get("queries", [])
    if not queries or not isinstance(queries, list):
        return jsonify({"error": "queries array is required"}), 400

    results = []
    for item in queries[:20]:
        query = item.get("query", "").strip()
        label = item.get("label", query)
        image_src = item.get("imageSrc", None)
        if not query:
            continue
        try:
            products = search_google_shopping(query)
            results.append({
                "label": label,
                "imageSrc": image_src,
                "results": products,
            })
        except Exception as e:
            results.append({
                "label": label,
                "imageSrc": image_src,
                "results": [],
                "error": str(e),
            })

    return jsonify({"results": results})


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "configured": bool(BRIGHTDATA_API_KEY),
        "zone": BRIGHTDATA_ZONE,
    })


# Serve the frontend
@app.route("/")
def index():
    return send_from_directory(".", "closet.html")


if __name__ == "__main__":
    if not BRIGHTDATA_API_KEY:
        print("\n  WARNING: BRIGHTDATA_API_KEY is not set.")
        print("  Copy .env.example to .env and add your key.\n")
    print("  ClosetAI server running at http://localhost:5000\n")
    app.run(debug=True, port=5000)
