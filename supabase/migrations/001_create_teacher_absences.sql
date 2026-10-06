create table public.teacher_absences (
    id uuid primary key default gen_random_uuid(),
    date date not null,
    synced_at timestamptz not null,
    teacher text not null,
    periods_impacted text not null
);

create index teacher_absences_date_idx
    on public.teacher_absences (date);

create index teacher_absences_date_synced_at_idx
    on public.teacher_absences (date, synced_at);

alter table public.teacher_absences enable row level security;

create policy "Public can read teacher absences"
on public.teacher_absences
for select
to anon, authenticated
using (true);