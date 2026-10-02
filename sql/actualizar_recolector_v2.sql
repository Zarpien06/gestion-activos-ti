alter table public.activos add column if not exists hostname text;
alter table public.activos add column if not exists usuario_windows text;
alter table public.activos add column if not exists uuid_equipo text;
alter table public.activos add column if not exists bios_serial text;
alter table public.activos add column if not exists dominio text;
alter table public.activos add column if not exists direccion_ip text;
alter table public.activos add column if not exists direccion_mac text;
alter table public.activos add column if not exists ultima_revision timestamptz;
notify pgrst, 'reload schema';
