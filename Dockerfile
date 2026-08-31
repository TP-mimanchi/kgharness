FROM python:3.12-slim
ARG PYPI_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
ENV UV_DEFAULT_INDEX=${PYPI_INDEX_URL}
RUN pip install --no-cache-dir --index-url ${PYPI_INDEX_URL} uv==0.8.15
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/workspace/.venv/bin:$PATH"

RUN addgroup --system app && adduser --system --ingroup app app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY --chown=app:app app ./app
RUN mkdir -p /data /workspace/app/output /workspace/app/updated && chown -R app:app /data /workspace/app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
