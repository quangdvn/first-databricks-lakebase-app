"""
Databricks App boilerplate:
- Serves a small Flask API
- Reads/writes to Lakebase (Databricks-managed Postgres) via lakebase.py
- Pulls data from the Massive API via massive_client.py and syncs it into Lakebase

Run locally:
    python app.py
Deploy as a Databricks App using app.yaml.
"""

import logging
import os
import re

import requests
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify, render_template, request

import lakebase
from massive_client import MassiveClient

# Lazy-load the embedding model to avoid startup overhead
_embedding_model = None

def get_embedding_model():
    """Lazy-load the sentence-transformers model for embedding queries."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        model_name = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        logger.info(f"Loading embedding model: {model_name}")
        _embedding_model = SentenceTransformer(model_name)
    return _embedding_model

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("massive-app")

app = Flask(__name__)
_w = WorkspaceClient()

TABLE_NAME = os.environ.get("MASSIVE_TABLE_NAME", "massive_records")
WATCHLIST_TABLE_NAME = os.environ.get("WATCHLIST_TABLE_NAME", "watchlist")

# Basic stock ticker shape check: 1-10 uppercase letters, with an optional
# ".X" or ".XX" share-class suffix (e.g. "BRK.B"). This rejects obviously
# malformed input before we even call the Massive API.
_TICKER_RE = re.compile(r"^[A-Z]{1,10}(\.[A-Z]{1,2})?$")


def ensure_table():
    """Create the destination table in Lakebase if it doesn't exist yet."""
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id TEXT PRIMARY KEY,
            payload JSONB NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def ensure_watchlist_table():
    """Create the watchlist table in Lakebase if it doesn't exist yet.
    
    NOTE: On Databricks free edition, CDC/REPLICA IDENTITY may have limitations.
    If you need CDC for Lakehouse Sync, you may need a paid tier.
    """
    # Check if table exists
    exists = lakebase.run_query(
        f"""
        SELECT EXISTS (
            SELECT FROM pg_tables 
            WHERE schemaname = 'public' AND tablename = '{WATCHLIST_TABLE_NAME}'
        )
        """
    )
    
    if not exists[0]['exists']:
        # Create table
        lakebase.run_write(
            f"""
            CREATE TABLE {WATCHLIST_TABLE_NAME} (
                symbol TEXT NOT NULL,
                email TEXT NOT NULL,
                latest_price NUMERIC,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (symbol, email)
            )
            """
        )
        
        # Try to set REPLICA IDENTITY for CDC (may fail on free tier)
        try:
            lakebase.run_write(
                f"ALTER TABLE {WATCHLIST_TABLE_NAME} REPLICA IDENTITY FULL"
            )
            logger.info(f"Set REPLICA IDENTITY FULL on {WATCHLIST_TABLE_NAME}")
        except Exception as e:
            logger.warning(
                f"Could not set REPLICA IDENTITY on {WATCHLIST_TABLE_NAME}: {e}. "
                "This is expected on Databricks free edition. CDC/Lakehouse Sync may not be available."
            )


def _current_user_email() -> str:
    """
    Resolve the current user's email so the watchlist can be personalized.

    Databricks Apps inject the logged-in user's identity via the
    X-Forwarded-Email header on every request. Fall back to the Databricks
    SDK's current_user API for local development where that header isn't set.
    """
    header_email = request.headers.get("X-Forwarded-Email")
    if header_email:
        return header_email
    return _w.current_user.me().user_name


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON (not an HTML error page),
    so the frontend's resp.json() call never chokes on HTML."""
    logger.exception("Unhandled exception while processing request")
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    """Simple UI to submit a list of stock symbols to sync from Massive."""
    return render_template("index.html")


@app.route("/records")
def list_records():
    """Read records already synced into Lakebase."""
    limit = int(request.args.get("limit", 100))
    rows = lakebase.run_query(
        f"SELECT id, payload, synced_at FROM {TABLE_NAME} ORDER BY synced_at DESC LIMIT %s",
        (limit,),
    )
    return jsonify(rows)


@app.route("/sync", methods=["POST"])
def sync_from_massive():
    """
    Pull data from the Massive API (paginated, potentially huge dataset) and
    upsert it into Lakebase in batches.
    """
    ensure_table()
    client = MassiveClient()

    path = request.json.get("path", "/records") if request.is_json else "/records"
    batch_size = int(request.args.get("batch_size", 500))

    batch = []
    total = 0
    for item in client.paginated_get(path):
        batch.append(item)
        if len(batch) >= batch_size:
            total += _upsert_batch(batch)
            batch = []

    if batch:
        total += _upsert_batch(batch)

    return jsonify({"synced": total})


@app.route("/watchlist", methods=["GET"])
def get_watchlist():
    """Return the current user's watchlist symbols, with their last known price."""
    ensure_watchlist_table()
    email = _current_user_email()
    rows = lakebase.run_query(
        f"SELECT symbol, email, latest_price, updated_at FROM {WATCHLIST_TABLE_NAME} "
        f"WHERE email = %s ORDER BY symbol ASC",
        (email,),
    )
    return jsonify(rows)


@app.route("/watchlist", methods=["POST"])
def add_to_watchlist():
    """
    Fetch the latest price for a single stock symbol from Massive using
    exactly ONE API call (see MassiveClient.get_latest_price), then add/
    update that symbol on the watchlist in Lakebase.
    """
    ensure_watchlist_table()

    if request.is_json:
        symbol = request.json.get("symbol", "")
    else:
        symbol = request.form.get("symbol", "")

    symbol = symbol.strip().upper() if isinstance(symbol, str) else ""

    if not symbol or not _TICKER_RE.match(symbol):
        return jsonify({"error": f"Invalid ticker symbol: {symbol!r}"}), 400

    client = MassiveClient()
    try:
        data = client.get_latest_price(symbol)  # <-- single API call, latest price only
    except requests.HTTPError:
        # Massive returns a 404/4xx for tickers it doesn't recognize.
        return jsonify({"error": f"Unknown ticker symbol: {symbol}"}), 400

    price = _extract_latest_price(data)
    if price is None:
        # No usable price in the response (e.g. delisted/invalid ticker
        # that still 200s with an empty result set) - don't add it.
        return jsonify({"error": f"No price data available for ticker: {symbol}"}), 400

    email = _current_user_email()

    lakebase.run_write(
        f"""
        INSERT INTO {WATCHLIST_TABLE_NAME} (symbol, email, latest_price, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (symbol, email) DO UPDATE
            SET latest_price = EXCLUDED.latest_price,
                updated_at = EXCLUDED.updated_at
        """,
        (symbol, email, price),
    )

    return jsonify({"symbol": symbol, "email": email, "latest_price": price})


@app.route("/watchlist/<symbol>", methods=["DELETE"])
def delete_from_watchlist(symbol):
    """
    Remove a stock symbol from the current user's watchlist.
    """
    ensure_watchlist_table()
    
    symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
    
    if not symbol:
        return jsonify({"error": "Symbol is required"}), 400
    
    email = _current_user_email()
    
    # Delete the symbol from the watchlist
    lakebase.run_write(
        f"DELETE FROM {WATCHLIST_TABLE_NAME} WHERE symbol = %s AND email = %s",
        (symbol, email),
    )
    
    return jsonify({"symbol": symbol, "deleted": True})


@app.route("/ticker/<symbol>/details", methods=["GET"])
def get_ticker_details(symbol):
    """
    Fetch rich company details for a ticker symbol from Massive API.
    Returns company name, description, market cap, sector, industry, logo, etc.
    """
    symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
    
    if not symbol or not _TICKER_RE.match(symbol):
        return jsonify({"error": f"Invalid ticker symbol: {symbol!r}"}), 400
    
    client = MassiveClient()
    try:
        data = client.get_ticker_details(symbol)
        return jsonify(data)
    except requests.HTTPError as e:
        # Massive returns 404 for unknown tickers
        return jsonify({"error": f"Ticker details not found: {symbol}"}), 404
    except Exception as e:
        return jsonify({"error": f"Failed to fetch ticker details: {str(e)}"}), 500


@app.route("/search")
def search_page():
    """Render the vector search UI."""
    return render_template("search.html")


@app.route("/api/search", methods=["POST"])
def vector_search():
    """
    Semantic search over news articles using vector similarity.
    
    Accepts a natural language query, embeds it, and searches both:
    1. Document-level embeddings (title + description)
    2. Chunk-level embeddings (article content chunks)
    
    Returns the most relevant results with metadata.
    """
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    
    query = request.json.get("query", "").strip()
    if not query:
        return jsonify({"error": "Query cannot be empty"}), 400
    
    # Search parameters
    top_k = min(int(request.json.get("top_k", 10)), 50)  # Cap at 50
    search_type = request.json.get("search_type", "documents")  # "documents" or "chunks"
    ticker_filter = request.json.get("ticker", "").strip().upper() or None
    
    try:
        # Embed the query
        model = get_embedding_model()
        query_embedding = model.encode([query])[0].tolist()
        
        # Format embedding as PostgreSQL array literal
        embedding_str = "{" + ",".join(str(float(x)) for x in query_embedding) + "}"
        
        if search_type == "documents":
            results = _search_documents(embedding_str, top_k, ticker_filter)
        else:
            results = _search_chunks(embedding_str, top_k, ticker_filter)
        
        return jsonify({
            "query": query,
            "search_type": search_type,
            "ticker_filter": ticker_filter,
            "results": results,
            "count": len(results)
        })
    
    except Exception as e:
        logger.exception("Error during vector search")
        return jsonify({"error": f"Search failed: {str(e)}"}), 500


def _search_documents(query_embedding: str, top_k: int, ticker_filter: str = None) -> list[dict]:
    """
    Search document-level embeddings (title + description).
    
    Returns articles ranked by cosine similarity to the query.
    """
    ticker_clause = "AND d.ticker = %s" if ticker_filter else ""
    params = [query_embedding, top_k] + ([ticker_filter] if ticker_filter else [])
    
    sql = f"""
        SELECT
            e.id,
            e.ticker,
            e.title,
            d.description,
            d.article_url,
            d.published_utc,
            d.author,
            d.publisher_name,
            1 - (e.embedding <=> %s::vector) AS similarity_score
        FROM ticker_news_embeddings e
        JOIN ticker_news_documents d ON e.id = d.id
        WHERE e.embedding IS NOT NULL
          {ticker_clause}
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
    """
    
    # Adjust params: query embedding appears twice in the SQL (for similarity calc and ORDER BY)
    adjusted_params = [query_embedding] + ([ticker_filter] if ticker_filter else []) + [query_embedding, top_k]
    
    rows = lakebase.run_query(sql, tuple(adjusted_params))
    return rows


def _search_chunks(query_embedding: str, top_k: int, ticker_filter: str = None) -> list[dict]:
    """
    Search chunk-level embeddings (article content chunks).
    
    Returns chunks ranked by cosine similarity to the query, with article metadata.
    """
    ticker_clause = "AND c.ticker = %s" if ticker_filter else ""
    params = [query_embedding] + ([ticker_filter] if ticker_filter else []) + [query_embedding, top_k]
    
    sql = f"""
        SELECT
            c.id,
            c.article_id,
            c.ticker,
            c.chunk_index,
            c.chunk_text,
            d.title,
            d.article_url,
            d.published_utc,
            d.author,
            d.publisher_name,
            1 - (c.embedding <=> %s::vector) AS similarity_score
        FROM ticker_news_chunk_embeddings c
        JOIN ticker_news_documents d ON c.article_id = d.id
        WHERE c.embedding IS NOT NULL
          {ticker_clause}
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
    """
    
    rows = lakebase.run_query(sql, tuple(params))
    return rows


def _extract_latest_price(data: dict) -> float | None:
    """Pull the trade price out of the Massive 'previous close' response shape.

    The /v2/aggs/ticker/{symbol}/prev endpoint returns "results" as a LIST
    containing a single aggregate bar (not a dict), e.g.:
        {"status": "OK", "resultsCount": 1, "results": [{"c": 148.845, ...}]}
    Previously this code treated "results" as a dict, so isinstance(results, dict)
    was always False for this endpoint's real shape and the price silently
    resolved to None. Unwrap the list here, and check "status"/"resultsCount"
    so invalid tickers (empty results) are detected instead of "succeeding"
    with a null price.

    Adjust the key lookup here if the real Massive API returns a different
    field name for the traded/close price.
    """
    if not isinstance(data, dict):
        return None
    if data.get("status") not in (None, "OK") or data.get("resultsCount") == 0:
        return None
    results = data.get("results", data)
    if isinstance(results, list):
        results = results[0] if results else None
    if isinstance(results, dict):
        for key in ("c", "p", "price", "last_price", "vw"):
            if key in results:
                return results[key]
    return None


def _upsert_batch(items: list[dict]) -> int:
    """Upsert a batch of Massive API items into Lakebase, one statement per row.

    For very large batches, consider psycopg2.extras.execute_values for
    higher throughput instead of per-row execute calls.
    """
    import json as _json

    count = 0
    with lakebase.get_connection() as conn, conn.cursor() as cur:
        for item in items:
            cur.execute(
                f"""
                    INSERT INTO {TABLE_NAME} (id, payload, synced_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (id) DO UPDATE
                        SET payload = EXCLUDED.payload,
                            synced_at = EXCLUDED.synced_at
                    """,
                (str(item.get("id")), _json.dumps(item)),
            )
            count += 1
        conn.commit()
    return count


if __name__ == "__main__":
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_RUN_PORT", 8000))
    app.run(debug=True, host=host, port=port)
    print(f"Flask app running on http://{host}:{port}")
