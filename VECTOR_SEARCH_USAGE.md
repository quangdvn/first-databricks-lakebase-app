# Vector Search - Usage Guide

## Overview

Vector search is now **ENABLED** in your Databricks App! 🎉

**Live URL**: https://1st-databricks-lakebase-app-7474657204013806.aws.databricksapps.com/search

## Quick Start

### 1. Access the Search Interface

Visit the search page from your app:
- Click "🔍 Search News & Articles →" from the main watchlist page
- Or navigate directly to `/search`

### 2. Perform a Search

**Basic search**:
- Enter a natural language query: "AI regulation concerns"
- Click "Search Documents" (default) or check "Search Chunks" for more granular results
- Results are ranked by semantic similarity

**Filtered search**:
- Enter a ticker symbol (e.g., "AAPL", "TSLA") to limit results to that stock
- Combine with your query for targeted search

### 3. API Usage

**Endpoint**: `POST /api/search`

**Example Request**:
```bash
curl -X POST https://1st-databricks-lakebase-app-7474657204013806.aws.databricksapps.com/api/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "renewable energy investments",
    "search_type": "documents",
    "ticker": "TSLA",
    "top_k": 10
  }'
```

**Parameters**:
- `query` (required): Natural language search query
- `search_type` (optional): `"documents"` (default) or `"chunks"`
- `ticker` (optional): Filter by ticker symbol (uppercase)
- `top_k` (optional): Number of results (1-50, default 10)

## How It Works

### Embedding Model
- **Model**: `sentence-transformers/all-MiniLM-L6-v2`
- **Dimensions**: 384
- **Load Time**: Model is lazy-loaded on first search (not at startup)

### Search Types

**Document Search** (default):
- Searches against document-level embeddings (title + description)
- Returns full articles ranked by relevance
- Best for: Finding relevant articles/news stories

**Chunk Search**:
- Searches against chunk-level embeddings (article content segments)
- Returns specific passages from articles
- Best for: Finding exact information or quotes

### Similarity Scoring
- Uses cosine similarity (pgvector's `<=>` operator)
- Score range: 0.0 (not similar) to 1.0 (identical)
- Results sorted by descending similarity

## Data Requirements

Vector search requires three Lakebase tables:

### 1. `ticker_news_documents`
Raw news articles with metadata:
- `id`, `ticker`, `title`, `description`
- `article_url`, `published_utc`, `author`, `publisher_name`

### 2. `ticker_news_embeddings`
Document-level embeddings:
- `id` (references documents)
- `ticker`
- `title`
- `embedding` (vector(384))
- HNSW index on `embedding` column

### 3. `ticker_news_chunk_embeddings`
Chunk-level embeddings:
- `id`, `article_id`, `ticker`, `chunk_index`
- `chunk_text`
- `embedding` (vector(384))
- HNSW index on `embedding` column

## Populating Search Data

### Ingestion Notebook
The repository includes `notebooks/ingest_ticker_news_embeddings.py` which:
1. Fetches news articles from Massive API for watchlisted tickers
2. Extracts article content using trafilatura
3. Generates embeddings using sentence-transformers
4. Stores data in the three Lakebase tables

### Running the Pipeline

```python
# Run the ingestion notebook to populate search data
# The notebook will:
# - Query your watchlist for active tickers
# - Fetch recent news articles
# - Generate and store embeddings
# - Create HNSW indexes for fast search
```

## Example Queries

Try these search queries:
- "Recent AI developments"
- "Renewable energy investments"  
- "Tech stock performance concerns"
- "Regulatory challenges"
- "Earnings beat expectations"
- "Supply chain disruptions"
- "Market volatility analysis"

## Performance

- **Query Time**: Typically < 100ms for top-10 results
- **Index Type**: HNSW (Hierarchical Navigable Small World)
- **Scalability**: Handles millions of vectors efficiently

## Troubleshooting

### "No results found"
- Make sure the ingestion notebook has been run
- Check that the three Lakebase tables exist and have data
- Verify pgvector extension is enabled in Lakebase

### "sentence-transformers is not installed"
- This error should not occur in the deployed app (dependency is now in requirements.txt)
- For local development: `pip install sentence-transformers`

### Slow first search
- The embedding model is lazy-loaded on first request (~500MB download)
- Subsequent searches are fast (model is cached in memory)

## Next Steps

**Enhancements to consider**:
1. **Hybrid Search**: Combine vector + keyword search (BM25)
2. **Reranking**: Add cross-encoder for better relevance
3. **Date Filters**: Filter by publication date range
4. **Sentiment Analysis**: Filter by article sentiment
5. **Export**: Download search results as CSV/JSON
6. **Search Analytics**: Track popular queries and CTR

## Technical Details

**Dependencies**:
```
sentence-transformers>=3.3.1
torch (installed automatically with sentence-transformers)
transformers (installed automatically with sentence-transformers)
```

**Database**:
```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create HNSW index for fast similarity search
CREATE INDEX ON ticker_news_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON ticker_news_chunk_embeddings USING hnsw (embedding vector_cosine_ops);
```

**API Implementation**:
- Flask endpoint: `/api/search`
- Lazy model loading to avoid startup overhead
- Parameterized SQL queries to prevent injection
- Cosine distance ranking with pgvector

---

**Questions?** Check the [full documentation](VECTOR_SEARCH_README.md) or the source code in `app.py`.
