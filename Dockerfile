FROM python:3.12.4-alpine AS builder

# Set environment variables
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install Poetry
RUN pip install --no-cache-dir poetry

WORKDIR /app

# Copy only the pyproject.toml and poetry.lock
COPY pyproject.toml poetry.lock ./

# Install Python dependencies with Poetry
RUN poetry config virtualenvs.in-project true \
    && poetry install --no-dev --no-interaction --no-ansi

FROM python:3.12.4-alpine AS runtime

# Set environment variables
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /app/.venv /opt/venv

COPY kubernetes_job_notifier.py .

CMD ["python", "kubernetes_job_notifier.py"]
