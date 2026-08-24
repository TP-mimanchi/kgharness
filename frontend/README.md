# TP · KG Studio Frontend

React + Vite + Tailwind CSS + Ant Design frontend for TP · KG Studio and its FastAPI agent backend.

## Run

```bash
pnpm install
pnpm dev
```

By default the app talks to `http://localhost:8000` and `ws://localhost:8000`.
Override with `.env.local`:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
VITE_TENANT_ID=00000000-0000-0000-0000-000000000001
```

## Backend Contract

- `POST /api/task`
- `POST /api/upload`
- `GET /api/files`
- `GET /api/download`
- `GET/POST /api/v1/knowledge-bases`
- `GET/POST /api/v1/knowledge-bases/{knowledge_base_id}/documents`
- `GET/POST /api/v1/ingestion-jobs/{job_id}`
- `POST /api/v1/retrieval/search`
- `WebSocket /ws/{thread_id}`

The session attachment endpoint (`/api/upload`) does not ingest files into RAG.
Use the **知识库管理** page for persistent knowledge-base ingestion.
