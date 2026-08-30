CREATE SCHEMA IF NOT EXISTS kikiori;

CREATE TABLE IF NOT EXISTS kikiori.entity_store (
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_type, entity_id),
    CONSTRAINT entity_store_type_check CHECK (
        entity_type IN (
            'audit_logs',
            'document_read_status',
            'documents',
            'guidance_drafts',
            'interview_prompt_profiles',
            'interview_states',
            'knowledge_dbs',
            'knowledge_fields',
            'knowledges',
            'learning_analysis_drafts',
            'messages',
            'proposals',
            'records',
            'voice_assistant_events',
            'voice_connection_events',
            'voice_sessions',
            'voice_turns'
        )
    )
);

CREATE INDEX IF NOT EXISTS entity_store_tenant_lookup_idx
    ON kikiori.entity_store (entity_type, tenant_id, created_at, entity_id);

CREATE INDEX IF NOT EXISTS entity_store_knowledge_lookup_idx
    ON kikiori.entity_store (entity_type, tenant_id, ((payload ->> 'knowledgeId')));

CREATE INDEX IF NOT EXISTS entity_store_record_lookup_idx
    ON kikiori.entity_store (entity_type, tenant_id, ((payload ->> 'recordId')));
