-- ============================================================
-- 02_etl_log_rechazos.sql
-- Tablas para Control de Ejecuciones, Auditoría y Rechazos
-- ============================================================

CREATE SCHEMA IF NOT EXISTS staging;

CREATE TABLE IF NOT EXISTS staging.etl_log (
    id_log              BIGSERIAL PRIMARY KEY,
    id_corrida          UUID NOT NULL,
    proceso             VARCHAR(100) NOT NULL,
    fuente              VARCHAR(150),
    fecha_inicio        TIMESTAMP NOT NULL DEFAULT now(),
    fecha_fin           TIMESTAMP,
    registros_leidos    INTEGER DEFAULT 0,
    registros_insertados INTEGER DEFAULT 0,
    registros_actualizados INTEGER DEFAULT 0,
    registros_rechazados INTEGER DEFAULT 0,
    estado              VARCHAR(20) NOT NULL DEFAULT 'EN_PROCESO',
    mensaje             TEXT
);

CREATE INDEX IF NOT EXISTS ix_etl_log_corrida ON staging.etl_log (id_corrida);
CREATE INDEX IF NOT EXISTS ix_etl_log_proceso ON staging.etl_log (proceso);

-- Tabla de rechazos para registros no procesables en el modelo
CREATE TABLE IF NOT EXISTS staging.rechazos_etl (
    id_rechazo      BIGSERIAL PRIMARY KEY,
    id_corrida      UUID NOT NULL,
    tabla_origen    TEXT NOT NULL,
    id_carga_origen BIGINT,
    codigo_snies    TEXT,
    codigo_institucion TEXT,
    motivo_rechazo  TEXT NOT NULL,
    fecha_rechazo   TIMESTAMP NOT NULL DEFAULT now(),
    fila_completa   JSONB
);

CREATE INDEX IF NOT EXISTS ix_rechazos_corrida ON staging.rechazos_etl (id_corrida);

-- ============================================================
-- Ampliaciones para el sistema ETL sistematizado
-- ============================================================

-- FD-02: la bitácora guarda el tipo de carga de cada ejecución
ALTER TABLE staging.etl_log ADD COLUMN IF NOT EXISTS tipo_carga VARCHAR(20);

-- Auditoría de transformaciones (campo, regla, cuántos valores cambió, ejemplo)
CREATE TABLE IF NOT EXISTS staging.auditoria_transformacion (
    id_auditoria     BIGSERIAL PRIMARY KEY,
    id_corrida       UUID NOT NULL,
    tabla_destino    TEXT NOT NULL,
    campo            TEXT NOT NULL,
    regla            TEXT NOT NULL,
    cantidad         INTEGER NOT NULL,
    ejemplo_original TEXT,
    ejemplo_final    TEXT,
    fecha_registro   TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_auditoria_corrida
    ON staging.auditoria_transformacion (id_corrida);

-- FA-01: trazabilidad de la homologación geográfica contra el catálogo DANE
CREATE TABLE IF NOT EXISTS staging.homologacion_geografica (
    id_homologacion   BIGSERIAL PRIMARY KEY,
    id_corrida        UUID NOT NULL,
    nivel             VARCHAR(20) NOT NULL,      -- DEPARTAMENTO / MUNICIPIO
    valor_original    TEXT NOT NULL,
    departamento_ref  TEXT,                      -- departamento con el que se validó el municipio
    valor_homologado  TEXT,
    codigo_dane       TEXT,
    metodo            VARCHAR(20) NOT NULL,      -- EXACTA / SIMILITUD / SIN_COINCIDENCIA
    puntaje           NUMERIC(5,2),
    clasificacion     VARCHAR(20) NOT NULL,      -- AUTOMATICO / REVISION / ADVERTENCIA
    estado_revision   VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    fecha_registro    TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_homologacion_corrida
    ON staging.homologacion_geografica (id_corrida);
