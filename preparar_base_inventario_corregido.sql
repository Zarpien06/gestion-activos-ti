-- Preparacion de la base para importar Inventario.xlsx
-- Conserva usuarios, roles, modulos y permisos.

begin;

-- AREAS
alter table public.areas add column if not exists nombre text;
alter table public.areas add column if not exists nombre_normalizado text;
alter table public.areas add column if not exists activo boolean default true;

-- PERSONAS
alter table public.personas add column if not exists nombre text;
alter table public.personas add column if not exists nombre_normalizado text;
alter table public.personas add column if not exists documento text;
alter table public.personas add column if not exists correo text;
alter table public.personas add column if not exists cargo text;
alter table public.personas add column if not exists area text;
alter table public.personas add column if not exists estado text default 'Activo';

-- ACTIVOS
alter table public.activos add column if not exists codigo text;
alter table public.activos add column if not exists tipo text;
alter table public.activos add column if not exists marca text;
alter table public.activos add column if not exists modelo text;
alter table public.activos add column if not exists serial text;
alter table public.activos add column if not exists procesador text;
alter table public.activos add column if not exists ram_gb numeric;
alter table public.activos add column if not exists disco_gb numeric;
alter table public.activos add column if not exists sistema_operativo text;
alter table public.activos add column if not exists estado text;
alter table public.activos add column if not exists disponibilidad text;
alter table public.activos add column if not exists persona_id bigint;
alter table public.activos add column if not exists asignado_a text;
alter table public.activos add column if not exists area text;
alter table public.activos add column if not exists fecha_asignacion date;
alter table public.activos add column if not exists fecha_compra date;
alter table public.activos add column if not exists observaciones text;

-- MOVIMIENTOS
alter table public.movimientos add column if not exists activo_id bigint;
alter table public.movimientos add column if not exists persona_id bigint;
alter table public.movimientos add column if not exists accion text;
alter table public.movimientos add column if not exists observacion text;
alter table public.movimientos add column if not exists fecha timestamp with time zone default now();

-- HISTORIAL
alter table public.historial add column if not exists activo_id bigint;
alter table public.historial add column if not exists accion text;
alter table public.historial add column if not exists detalle text;
alter table public.historial add column if not exists fecha timestamp with time zone default now();

-- TICKETS Y ACTAS
alter table public.tickets add column if not exists activo_id bigint;
alter table public.actas add column if not exists persona_id bigint;

-- VALORES POR DEFECTO
update public.areas set activo = true where activo is null;
update public.personas set estado = 'Activo' where estado is null or btrim(estado) = '';

commit;

-- INDICES: se ejecutan despues del commit principal
create unique index if not exists areas_nombre_normalizado_unique
on public.areas (nombre_normalizado);

create unique index if not exists personas_nombre_normalizado_unique
on public.personas (nombre_normalizado);

create unique index if not exists activos_codigo_unique
on public.activos (codigo);

-- RELACION ACTIVO-PERSONA SIN BLOQUE DO
alter table public.activos
    drop constraint if exists activos_persona_id_fkey;

alter table public.activos
    add constraint activos_persona_id_fkey
    foreign key (persona_id)
    references public.personas(id)
    on delete set null;

-- RECARGAR CACHE POSTGREST
notify pgrst, 'reload schema';
