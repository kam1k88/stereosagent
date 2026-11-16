-- PostgreSQL schema for the StereosAgent document and contract domain.
-- Apply with: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f src/database/schema.sql

BEGIN;

CREATE TYPE document_type AS ENUM (
    'contract',
    'amendment_signed',
    'amendment_draft',
    'supplier_email',
    'appendix',
    'other',
    'unknown'
);

CREATE TYPE relation_type AS ENUM (
    'amends',
    'appendix_of',
    'correspondence_about',
    'supersedes',
    'references'
);

CREATE TYPE signature_status AS ENUM (
    'signed',
    'not_signed',
    'unknown'
);

CREATE TYPE reference_type AS ENUM (
    'appendix',
    'specification',
    'attachment',
    'other'
);

CREATE TYPE reference_status AS ENUM (
    'resolved',
    'missing',
    'not_checked',
    'ambiguous'
);

CREATE TYPE condition_status AS ENUM (
    'applied',
    'proposed',
    'needs_review',
    'superseded',
    'rejected'
);

CREATE TABLE parties (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    inn VARCHAR(12),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT parties_name_nonempty CHECK (length(btrim(name)) > 0),
    CONSTRAINT parties_inn_format CHECK (inn IS NULL OR inn ~ '^[0-9]{10}([0-9]{2})?$')
);

CREATE UNIQUE INDEX parties_inn_unique ON parties (inn) WHERE inn IS NOT NULL;
CREATE UNIQUE INDEX parties_name_ci_unique ON parties (lower(name));

CREATE TABLE contracts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contract_number TEXT NOT NULL UNIQUE,
    buyer_id BIGINT REFERENCES parties(id) ON DELETE RESTRICT,
    supplier_id BIGINT REFERENCES parties(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT contracts_number_nonempty CHECK (length(btrim(contract_number)) > 0),
    CONSTRAINT contracts_parties_differ CHECK (buyer_id IS NULL OR supplier_id IS NULL OR buyer_id <> supplier_id)
);

CREATE TABLE documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_id TEXT NOT NULL UNIQUE,
    contract_id BIGINT REFERENCES contracts(id) ON DELETE RESTRICT,
    filename TEXT NOT NULL,
    document_type document_type NOT NULL DEFAULT 'other',
    content_hash CHAR(64) NOT NULL UNIQUE,
    content TEXT NOT NULL,
    sender TEXT,
    recipient TEXT,
    signature_date DATE,
    effective_date DATE,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT documents_filename_nonempty CHECK (length(btrim(filename)) > 0),
    CONSTRAINT documents_hash_format CHECK (content_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX documents_contract_idx ON documents(contract_id);
CREATE INDEX documents_type_idx ON documents(document_type);
CREATE INDEX documents_content_fts_idx
    ON documents USING GIN (to_tsvector('russian', coalesce(content, '')));

CREATE TABLE document_signatures (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    party_id BIGINT NOT NULL REFERENCES parties(id) ON DELETE RESTRICT,
    status signature_status NOT NULL DEFAULT 'unknown',
    signed_at DATE,
    evidence_id BIGINT,
    UNIQUE (document_id, party_id),
    CONSTRAINT signature_date_consistent CHECK (status <> 'signed' OR signed_at IS NOT NULL)
);

CREATE TABLE document_relations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    target_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    relation relation_type NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT document_relation_not_self CHECK (source_document_id <> target_document_id),
    UNIQUE (source_document_id, target_document_id, relation)
);

CREATE INDEX document_relations_target_idx ON document_relations(target_document_id, relation);

CREATE TABLE document_references (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    referenced_title TEXT NOT NULL,
    reference_type reference_type NOT NULL DEFAULT 'other',
    resolved_document_id BIGINT REFERENCES documents(id) ON DELETE RESTRICT,
    status reference_status NOT NULL DEFAULT 'not_checked',
    evidence_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT reference_title_nonempty CHECK (length(btrim(referenced_title)) > 0),
    CONSTRAINT reference_resolution_consistent CHECK (
        (status = 'resolved' AND resolved_document_id IS NOT NULL)
        OR (status <> 'resolved' AND resolved_document_id IS NULL)
    )
);

CREATE INDEX document_references_source_idx ON document_references(source_document_id);
CREATE INDEX document_references_status_idx ON document_references(status);

CREATE TABLE evidence (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    paragraph_number INTEGER NOT NULL CHECK (paragraph_number > 0),
    quote TEXT NOT NULL,
    char_start INTEGER CHECK (char_start IS NULL OR char_start >= 0),
    char_end INTEGER CHECK (char_end IS NULL OR char_end >= char_start),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, paragraph_number, quote)
);

ALTER TABLE document_signatures
    ADD CONSTRAINT document_signatures_evidence_fk
    FOREIGN KEY (evidence_id) REFERENCES evidence(id) ON DELETE SET NULL;

ALTER TABLE document_references
    ADD CONSTRAINT document_references_evidence_fk
    FOREIGN KEY (evidence_id) REFERENCES evidence(id) ON DELETE SET NULL;

CREATE TABLE document_facts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    value_json JSONB NOT NULL,
    evidence_id BIGINT REFERENCES evidence(id) ON DELETE RESTRICT,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    extractor_version TEXT,
    CONSTRAINT document_facts_field_nonempty CHECK (length(btrim(field_name)) > 0),
    UNIQUE (document_id, field_name)
);

CREATE INDEX document_facts_field_idx ON document_facts(field_name);

CREATE TABLE contract_conditions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contract_id BIGINT NOT NULL REFERENCES contracts(id) ON DELETE RESTRICT,
    field_name TEXT NOT NULL,
    value_json JSONB NOT NULL,
    status condition_status NOT NULL,
    valid_from DATE,
    valid_to DATE,
    source_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    evidence_id BIGINT REFERENCES evidence(id) ON DELETE RESTRICT,
    rule_set_id TEXT NOT NULL DEFAULT 'procurement_policy',
    rule_version INTEGER NOT NULL DEFAULT 1 CHECK (rule_version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT contract_conditions_field_nonempty CHECK (length(btrim(field_name)) > 0),
    CONSTRAINT contract_conditions_validity CHECK (
        valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from
    ),
    CONSTRAINT contract_conditions_applied_dates CHECK (
        status <> 'applied' OR valid_from IS NOT NULL
    )
);

CREATE INDEX contract_conditions_lookup_idx
    ON contract_conditions(contract_id, field_name, status, valid_from, valid_to);

CREATE INDEX contract_conditions_source_idx ON contract_conditions(source_document_id);

COMMIT;
