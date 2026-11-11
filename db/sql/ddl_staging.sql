-- ============================================================
-- 01_staging_schema.sql
-- Bodega de datos - Universidad de Nariño (SNIES)
-- CAPA DE STAGING (Carga Incremental en formato TEXT)
-- ============================================================

CREATE SCHEMA IF NOT EXISTS staging;

-- ------------------------------------------------------------
-- 1. staging.instituciones_raw
-- Contiene atributos de DIMENSION_INSTITUCION
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.instituciones_raw (
    id_carga                        BIGSERIAL PRIMARY KEY,
    codigo_institucion              TEXT UNIQUE NOT NULL, -- Clave única requerida para UPSERT
    codigo_ies_padre                TEXT,
    nombre                          TEXT,
    estado                          TEXT,
    nit                             TEXT,
    principal_seccional             TEXT,
    naturaleza_juridica             TEXT,
    sector                          TEXT,
    caracter_academico              TEXT,
    pagina_web                      TEXT,
    acreditacion                    TEXT,
    fecha_acreditacion              TEXT,
    vigencia_acreditacion           TEXT,
    directivo_actual                TEXT
);

-- ------------------------------------------------------------
-- 2. staging.directivos_raw
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.directivos_raw (
    id_carga                        BIGSERIAL PRIMARY KEY,
    codigo_institucion              TEXT NOT NULL,
    cargo                           TEXT,
    nombre                          TEXT,
    apellido                        TEXT,
    fecha_desde                     TEXT,
    fecha_hasta                     TEXT
);

-- ------------------------------------------------------------
-- 3. staging.programas_raw
-- Contiene atributos del hecho, AREA_CONOCIMIENTO, MODALIDAD,
-- ESTADO_CALIDAD y la ubicación de oferta (departamento/municipio)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.programas_raw (
    id_carga                              BIGSERIAL PRIMARY KEY,
    codigo_snies                          TEXT UNIQUE NOT NULL, -- Clave única requerida para UPSERT
    codigo_icfes                          TEXT,
    registro_unico                        TEXT,
    nombre_programa                       TEXT,
    titulo_otorgado                       TEXT,
    resolucion_aprobacion                 TEXT,
    codigo_institucion                    TEXT,
    departamento_oferta                   TEXT, -- DEPARTAMENTO_OFERTA_PROGRAMA (alimenta DIM_UBICACION)
    municipio_oferta                      TEXT, -- MUNICIPIO_OFERTA_PROGRAMA   (alimenta DIM_UBICACION)
    area_conocimiento                     TEXT,
    nbc                                   TEXT,
    cine_f_amplio                         TEXT,
    cine_f_especifico                     TEXT,
    cine_f_detallado                      TEXT,
    nivel_academico                       TEXT,
    nivel_formacion                       TEXT,
    modalidad                             TEXT,
    periodicidad                          TEXT,
    periodicidad_admisiones               TEXT,
    ciclos_propedeuticos                  TEXT,
    estado_programa                       TEXT,
    reconocimiento_ministerio             TEXT,
    fecha_resolucion                      TEXT,
    fecha_ejecutoria                      TEXT,
    fecha_registro_snies                  TEXT,
    creditos                              TEXT,
    duracion_periodos                     TEXT,
    vigencia_anios                        TEXT,
    costo_matricula                       TEXT
);

-- ------------------------------------------------------------
-- 4. staging.cobertura_raw
-- Aporta tipo_cobertura a DIMENSION_UBICACION. Un programa puede tener
-- varias filas (varios municipios y varios tipos de cubrimiento), por eso
-- se conservan departamento y municipio como llave de cruce con
-- programas_raw (departamento_oferta / municipio_oferta).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.cobertura_raw (
    id_carga                        BIGSERIAL PRIMARY KEY,
    codigo_snies                    TEXT NOT NULL,
    departamento                    TEXT,
    municipio                       TEXT,
    tipo_cobertura                  TEXT,
    -- En el Excel un mismo (programa, depto, municipio) llega con varios
    -- tipos de cubrimiento, por eso tipo_cobertura hace parte de la llave.
    CONSTRAINT uk_cobertura_programa_muni_tipo
        UNIQUE (codigo_snies, departamento, municipio, tipo_cobertura)
);

-- ------------------------------------------------------------
-- Índices para optimizar cruces y consultas incrementales
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_staging_instituciones_codigo
    ON staging.instituciones_raw (codigo_institucion);
CREATE INDEX IF NOT EXISTS ix_staging_directivos_codigo
    ON staging.directivos_raw (codigo_institucion);
-- Llave natural de directivos: evita duplicados en cargas repetidas
-- (COALESCE porque los NULL no se consideran iguales entre sí)
CREATE UNIQUE INDEX IF NOT EXISTS uk_directivos_raw
    ON staging.directivos_raw
    (codigo_institucion, cargo, nombre, apellido, COALESCE(fecha_desde, ''));
CREATE INDEX IF NOT EXISTS ix_staging_programas_codigo
    ON staging.programas_raw (codigo_snies);
CREATE INDEX IF NOT EXISTS ix_staging_cobertura_codigo
    ON staging.cobertura_raw (codigo_snies);