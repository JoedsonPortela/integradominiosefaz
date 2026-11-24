# Imagem do coletor DF-e (MVP). Não contém certificado nem senha.
FROM python:3.12-slim

# curl com TLS: a cadeia ICP-Brasil e da SEFAZ-BA deve ser copiada em /usr/local/share/ca-certificates
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "psycopg[binary]==3.2.3"

RUN useradd --create-home --uid 10001 coletor
WORKDIR /app
COPY coletor.py schema.sql ./
USER coletor

ENTRYPOINT ["python3", "-m", "coletor"]
