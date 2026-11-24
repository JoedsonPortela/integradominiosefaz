-- Esquema do coletor DF-e (MVP). Tipos portáveis: vale para PostgreSQL e SQLite (testes).
-- Datas em ISO 8601 com fuso (texto), para não depender de tipos específicos do banco.

CREATE TABLE IF NOT EXISTS dfe_controle (
    cnpj                          TEXT PRIMARY KEY,
    ultimo_nsu                    TEXT NOT NULL DEFAULT '000000000000000',
    max_nsu                       TEXT,
    ultima_consulta_em            TEXT,
    proxima_consulta_permitida_em TEXT,
    ultimo_cstat                  TEXT
);

CREATE TABLE IF NOT EXISTS dfe_documento (
    cnpj               TEXT NOT NULL,
    nsu                TEXT NOT NULL,
    schema_doc         TEXT NOT NULL,
    chave_nfe          TEXT,
    tipo_evento        TEXT,
    caminho_bruto      TEXT NOT NULL,
    sha256             TEXT NOT NULL,
    tamanho_bytes      INTEGER NOT NULL,
    recebido_em        TEXT NOT NULL,
    status_envio_dominio TEXT NOT NULL DEFAULT 'pendente',
    tentativas_envio   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cnpj, nsu)
);

CREATE INDEX IF NOT EXISTS ix_dfe_documento_chave ON dfe_documento (cnpj, chave_nfe);
