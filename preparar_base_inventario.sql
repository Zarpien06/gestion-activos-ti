-- EJECUTAR UNA VEZ ANTES DE importar_inventario_completo.py
-- Este bloque conserva usuarios, roles, modulos y permisos.

alter table public.areas
add column if not exists nombre_normalizado text;
alter table public.areas
add column if not exists activo boolean default true;
create unique index if not exists areas_nombre_normalizado_unique
on public.areas(nombre_normalizado);

alter table public.personas
add column if not exists nombre_normalizado text;
alter table public.personas
add column if not exists documento text;
alter table public.personas
add column if not exists correo text;
alter table public.personas
add column if not exists cargo text;
alter table public.personas
add column if not exists area text;
alter table public.personas
add column if not exists estado text default 'Activo';
create unique index if not exists personas_nombre_normalizado_unique
on public.personas(nombre_normalizado);

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
create unique index if not exists activos_codigo_unique
on public.activos(codigo);

alter table public.movimientos add column if not exists activo_id bigint;
alter table public.movimientos add column if not exists persona_id bigint;
alter table public.movimientos add column if not exists accion text;
alter table public.movimientos add column if not exists observacion text;
alter table public.movimientos add column if not exists fecha timestamp with time zone default now();

alter table public.historial add column if not exists activo_id bigint;
alter table public.historial add column if not exists accion text;
alter table public.historial add column if not exists detalle text;
alter table public.historial add column if not exists fecha timestamp with time zone default now();

alter table public.tickets add column if not exists activo_id bigint;
alter table public.actas add column if not exists persona_id bigint;

-- Las relaciones se agregan solo si todavia no existen.
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'activos_persona_id_fkey'
    ) then
        alter table public.activos
        add constraint activos_persona_id_fkey
        foreign key (persona_id) references public.personas(id)
        on delete set null;
    end if;
end $$;

notify pgrst, 'reload schema';
