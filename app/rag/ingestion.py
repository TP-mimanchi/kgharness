"""LlamaIndex-based ingestion pipeline executed by the persistent worker."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter

from app.rag.config import settings
from app.rag.db import database
from app.rag.embeddings import embedding_provider, lexicalize
from app.rag.parsers import parse_document


async def ingest_job(job_id: UUID) -> None:
    job = await database.load_job_document(job_id)
    if not job:
        raise ValueError(f"ingestion job not found: {job_id}")

    await database.update_job(
        job_id, status="processing", stage="parsing", progress=10
    )
    source_path = Path(job["storage_path"])
    if not source_path.is_file():
        raise FileNotFoundError(f"stored document is missing: {source_path}")

    documents = parse_document(source_path, job["original_filename"])
    await database.update_job(
        job_id, status="processing", stage="chunking", progress=35
    )

    splitter = SentenceSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    pipeline = IngestionPipeline(transformations=[splitter])
    nodes = await pipeline.arun(documents=documents)
    nodes = [node for node in nodes if node.get_content().strip()]
    if not nodes:
        raise ValueError("document produced no searchable chunks")

    await database.update_job(
        job_id, status="processing", stage="embedding", progress=55
    )
    contents = [node.get_content().strip() for node in nodes]
    embeddings: list[list[float]] = []
    for start in range(0, len(contents), settings.embedding_batch_size):
        embeddings.extend(
            await embedding_provider.embed(
                contents[start : start + settings.embedding_batch_size]
            )
        )

    chunks = []
    for index, (node, content, embedding) in enumerate(zip(nodes, contents, embeddings)):
        page = node.metadata.get("page")
        section = node.metadata.get("section_path") or []
        chunks.append(
            {
                "id": uuid4(),
                "chunk_index": index,
                "content": content,
                "lexical_text": lexicalize(content),
                "embedding": embedding,
                "page_start": int(page) if page else None,
                "page_end": int(page) if page else None,
                "section_path": section if isinstance(section, list) else [str(section)],
                "metadata": {
                    key: value
                    for key, value in node.metadata.items()
                    if isinstance(value, (str, int, float, bool, list, dict, type(None)))
                },
            }
        )

    await database.update_job(
        job_id, status="processing", stage="indexing", progress=80
    )
    await database.replace_document_chunks(
        tenant_id=job["tenant_id"],
        knowledge_base_id=job["knowledge_base_id"],
        document_id=job["document_id"],
        index_version=job["active_index_version"],
        chunks=chunks,
    )
    await database.update_job(
        job_id, status="completed", stage="completed", progress=100
    )
