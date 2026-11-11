-- ============================================================
-- 03_dw_schema.sql
-- Bodega de datos - Universidad de Nariño (SNIES)
-- Modelo dimensional en estrella: 7 dimensiones + 1 hecho
-- (según el diagrama de la bodega de datos)
-- ============================================================
-- Diferencias entre el diagrama (lógico) y esta implementación (física):
--  1. hecho.vigencia_anios es DECIMAL(4,1) y no INTEGER: el origen trae
--     valores como 8,5 (272 programas) y un entero los truncaría.
--  2. hecho.indicador_oferta se agrega porque las reglas 3.2 del documento
--     la exigen (no aparece en el diagrama). fecha_carga y fuente se omiten
--     por redundantes; la trazabilidad queda en staging.etl_log.
--  3. Se agregan restricciones UNIQUE sobre las llaves de negocio para poder
--     hacer la carga incremental (UPSERT) y evitar duplicados.
--  4. En el diagrama sk_programa del hecho aparece como PK; aquí es FK
--     (la PK del hecho es sk_hecho).
-- Para reiniciar la bodega: DROP SCHEMA dw CASCADE;
-- ============================================================

CREATE SCHEMA IF NOT EXISTS dw;

-- ------------------------------------------------------------
-- DIMENSION_INSTITUCION
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.dim_institucion (
    sk_institucion         SERIAL PRIMARY KEY,
    codigo_institucion     TEXT NOT NULL,
    codigo_ies_padre       TEXT,
    nombre                 TEXT NOT NULL,
    estado                 TEXT,
    nit                    TEXT,
    principal_seccional    TEXT,
    naturaleza_juridica    TEXT,
    sector                 TEXT,
    caracter_academico     TEXT,
    pagina_web             TEXT,
    acreditacion           TEXT,          -- SI / NO / NO INFORMADO
    fecha_acreditacion     DATE,
    vigencia_acreditacion  INTEGER,
    directivo_actual       TEXT,
    CONSTRAINT uq_dim_institucion_codigo UNIQUE (codigo_institucion)
);

-- ------------------------------------------------------------
-- DIMENSION_UBICACION
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.dim_ubicacion (
    sk_ubicacion    SERIAL PRIMARY KEY,
    departamento    TEXT NOT NULL,
    municipio       TEXT NOT NULL,
    tipo_cobertura  TEXT NOT NULL,
    CONSTRAINT uq_dim_ubicacion UNIQUE (departamento, municipio, tipo_cobertura)
);

-- ------------------------------------------------------------
-- DIMENSION_AREA_CONOCIMIENTO
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.dim_area_conocimiento (
    sk_area_conocimiento   SERIAL PRIMARY KEY,
    area_conocimiento      TEXT NOT NULL,
    nbc                    TEXT NOT NULL,
    cine_f_amplio          TEXT NOT NULL,
    cine_f_especifico      TEXT NOT NULL,
    cine_f_detallado       TEXT NOT NULL,
    CONSTRAINT uq_dim_area UNIQUE (area_conocimiento, nbc, cine_f_amplio, cine_f_especifico, cine_f_detallado)
);

-- ------------------------------------------------------------
-- DIMENSION_MODALIDAD
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.dim_modalidad (
    sk_modalidad             SERIAL PRIMARY KEY,
    nivel_academico          TEXT NOT NULL,
    nivel_formacion          TEXT NOT NULL,
    modalidad                TEXT NOT NULL,
    periodicidad             TEXT NOT NULL,
    periodicidad_admisiones  TEXT NOT NULL,
    ciclos_propedeuticos     TEXT NOT NULL,   -- SI / NO / NO INFORMADO
    CONSTRAINT uq_dim_modalidad UNIQUE (nivel_academico, nivel_formacion, modalidad,
                                        periodicidad, periodicidad_admisiones, ciclos_propedeuticos)
);

-- ------------------------------------------------------------
-- DIMENSION_ESTADO_CALIDAD
-- reconocimiento_ministerio guarda el valor descriptivo del origen
-- (Registro calificado / Acreditación de alta calidad / Acreditación previa)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.dim_estado_calidad (
    sk_estado_calidad          SERIAL PRIMARY KEY,
    estado_programa            TEXT NOT NULL,
    reconocimiento_ministerio  TEXT NOT NULL,
    CONSTRAINT uq_dim_estado_calidad UNIQUE (estado_programa, reconocimiento_ministerio)
);

-- ------------------------------------------------------------
-- DIMENSION_TIEMPO (dimensión de rol: una tabla, tres roles en el hecho)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.dim_tiempo (
    sk_tiempo    SERIAL PRIMARY KEY,
    fecha        DATE NOT NULL,
    anio         INTEGER NOT NULL,
    trimestre    INTEGER NOT NULL,
    mes          INTEGER NOT NULL,
    dia_semana   VARCHAR(50) NOT NULL,
    CONSTRAINT uq_dim_tiempo_fecha UNIQUE (fecha)
);

-- ------------------------------------------------------------
-- DIMENSION_PROGRAMA
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.dim_programa (
    sk_programa            SERIAL PRIMARY KEY,
    codigo_snies           TEXT NOT NULL,
    codigo_icfes           TEXT,
    registro_unico         TEXT,
    nombre_programa        TEXT NOT NULL,
    titulo_otorgado        TEXT,
    resolucion_aprobacion  TEXT,
    CONSTRAINT uq_dim_programa_snies UNIQUE (codigo_snies),
    CONSTRAINT ck_dim_programa_snies CHECK (codigo_snies ~ '^[0-9]+$')
);

-- ------------------------------------------------------------
-- HECHO_OFERTA_PROGRAMA
-- Grano: un programa académico (una fila por sk_programa)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.hecho_oferta_programa (
    sk_hecho                  BIGSERIAL PRIMARY KEY,
    sk_institucion            INTEGER NOT NULL REFERENCES dw.dim_institucion(sk_institucion),
    sk_ubicacion              INTEGER NOT NULL REFERENCES dw.dim_ubicacion(sk_ubicacion),
    sk_area_conocimiento      INTEGER NOT NULL REFERENCES dw.dim_area_conocimiento(sk_area_conocimiento),
    sk_modalidad              INTEGER NOT NULL REFERENCES dw.dim_modalidad(sk_modalidad),
    sk_programa               INTEGER NOT NULL REFERENCES dw.dim_programa(sk_programa),
    sk_estado_calidad         INTEGER NOT NULL REFERENCES dw.dim_estado_calidad(sk_estado_calidad),
    sk_tiempo_resolucion      INTEGER REFERENCES dw.dim_tiempo(sk_tiempo),
    sk_tiempo_ejecutoria      INTEGER REFERENCES dw.dim_tiempo(sk_tiempo),
    sk_tiempo_registro_snies  INTEGER REFERENCES dw.dim_tiempo(sk_tiempo),
    indicador_oferta          SMALLINT NOT NULL DEFAULT 1,
    creditos                  INTEGER,
    duracion_periodos         INTEGER,
    vigencia_anios            DECIMAL(4,1),
    costo_matricula           DECIMAL(12,2),
    CONSTRAINT uq_hecho_programa UNIQUE (sk_programa)
);

CREATE INDEX IF NOT EXISTS ix_hecho_institucion ON dw.hecho_oferta_programa (sk_institucion);
CREATE INDEX IF NOT EXISTS ix_hecho_ubicacion   ON dw.hecho_oferta_programa (sk_ubicacion);
CREATE INDEX IF NOT EXISTS ix_hecho_area        ON dw.hecho_oferta_programa (sk_area_conocimiento);
CREATE INDEX IF NOT EXISTS ix_hecho_modalidad   ON dw.hecho_oferta_programa (sk_modalidad);
CREATE INDEX IF NOT EXISTS ix_hecho_estado      ON dw.hecho_oferta_programa (sk_estado_calidad);

-- Fila de referencia 'NO INFORMADO' para ubicación (reglas 3.2 y 3.3)
INSERT INTO dw.dim_ubicacion (departamento, municipio, tipo_cobertura)
VALUES ('NO INFORMADO', 'NO INFORMADO', 'NO INFORMADO')
ON CONFLICT DO NOTHING;
