-- 기존 Supabase 프로젝트에 토요일 20:00(한국시간) 고정 정책을 적용합니다.
drop policy if exists "owners can insert todays user draws" on public.user_draws;
create policy "owners can insert todays user draws"
  on public.user_draws for insert
  to authenticated
  with check (
    auth.uid() = user_id
    and draw_date = timezone('Asia/Seoul', now())::date
    and not (
      extract(isodow from timezone('Asia/Seoul', now())) = 6
      and timezone('Asia/Seoul', now())::time >= time '20:00'
    )
  );

drop policy if exists "owners can update todays user draws" on public.user_draws;
create policy "owners can update todays user draws"
  on public.user_draws for update
  to authenticated
  using (
    auth.uid() = user_id
    and draw_date = timezone('Asia/Seoul', now())::date
    and not (
      extract(isodow from timezone('Asia/Seoul', now())) = 6
      and timezone('Asia/Seoul', now())::time >= time '20:00'
    )
  )
  with check (
    auth.uid() = user_id
    and draw_date = timezone('Asia/Seoul', now())::date
    and not (
      extract(isodow from timezone('Asia/Seoul', now())) = 6
      and timezone('Asia/Seoul', now())::time >= time '20:00'
    )
  );
