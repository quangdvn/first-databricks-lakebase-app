# Vector Search Feature

## Overview

This feature adds semantic search capabilities to the Massive Stock Watchlist app, allowing users to search through ticker news articles and content chunks using natural language queries.

## Architecture

### Data Pipeline
1. **News Ingestion**: `notebooks/ingest_ticker_news_embeddings` fetches news articles from the Massive API for watchlisted tickers
2. **Content Extraction**: Article content is fetched and chunked into overlapping segments using `trafilatura`
3. **Embedding Generation**: Both full documents (title + description) and content chunks are embedded using `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions)
4. **Vector Storage**: Embeddings are stored in Lakebase (Postgres) with pgvector extension and HNSW indexing

### Tables in Lakebase
- `ticker_news_documents`: Raw news articles with metadata
- `ticker_news_embeddings`: Document-level embeddings (title + description)
- `ticker_news_chunk_embeddings`: Chunk-level embeddings (article content chunks)

### Search Endpoint

**Route**: `POST /api/search`

**Request Body**:
```json
{
  "query": "AI regulation concerns",
  "search_type": "chunks",  // or "documents"
  "ticker": "AAPL",          // optional ticker filter
  "top_k": 10               // number of results (max 50)
}
```

**Response**:
```json
{
  "query": "AI regulation concerns",
  "search_type": "chunks",
  "ticker_filter": null,
  "count": 5,
  "results": [
    {
      "id": "article_id_chunk_index",
      "article_id": "article_id",
      "ticker": "TSLA",
      "chunk_index": 2,
      "chunk_text": "relevant excerpt...",
      "title": "Article title",
      "article_url": "https://...",
      "published_utc": "2024-01-15T10:30:00Z",
      "author": "John Doe",
      "publisher_name": "TechNews",
      "similarity_score": 0.87
    }
  ]
}
```

## UI

### Search Page
- **Route**: `/search`
- **Features**:
  - Natural language query input
  - Toggle between document and chunk search
  - Optional ticker filtering
  - Results displayed with similarity scores
  - Link to original articles
  - Responsive design matching the main watchlist UI

### Navigation
- Added link from main watchlist page to search page: "🔍 Search News & Articles →"
- Back link from search page to watchlist

## Implementation Details

### Embedding Model
- Model: `sentence-transformers/all-MiniLM-L6-v2`
- Dimensions: 384
- Lazy-loaded on first search request (not at app startup)
- Environment variable: `EMBEDDING_MODEL` (defaults to all-MiniLM-L6-v2)

### Vector Similarity
- Uses pgvector's cosine distance operator (`<=>`)
- Similarity score = 1 - cosine_distance
- Results sorted by ascending distance (most similar first)

### Performance
- HNSW index on both embedding tables for fast approximate nearest neighbor search
- Query time typically < 100ms for top-10 results

## Usage

### Running the App
```bash
python app.py
```

Then visit:
- Main watchlist: http://localhost:8000/
- Search page: http://localhost:8000/search

### Example Queries
- "Recent AI developments"
- "Renewable energy investments"
- "Tech stock performance concerns"
- "Regulatory challenges"
- "Earnings beat expectations"

## Dependencies

New dependency added to `pyproject.toml`:
- `sentence-transformers>=3.3.1`

Install with:
```bash
uv sync
```

## Next Steps

Potential enhancements:
1. **Hybrid Search**: Combine vector search with keyword/BM25 for better results
2. **Reranking**: Add a cross-encoder reranking step for improved relevance
3. **Filters**: Date range, sentiment, publisher filters
4. **Caching**: Cache query embeddings for frequently-used queries
5. **Analytics**: Track search queries and click-through rates
6. **Export**: Allow users to export search results
