import permanentRecords from '../kbo_permanant_numbers.json';
import { requireSupabase } from './supabase';

const VALID_NUMBERS = [...Array(45).keys()].map((index) => index + 1);
const DAY_NAMES = ['일', '월', '화', '수', '목', '금', '토'];

function kstToday() {
  const parts = new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function parseDate(value) {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function dateString(value) {
  return value.toISOString().slice(0, 10);
}

function addDays(value, count) {
  const result = new Date(value);
  result.setUTCDate(result.getUTCDate() + count);
  return result;
}

function weekDates(today) {
  const current = parseDate(today);
  const sunday = addDays(current, -current.getUTCDay());
  return [0, 2, 3, 4, 5, 6].map((offset) => dateString(addDays(sunday, offset)));
}

function dayPayload(value, today, numbers = null, saved = false) {
  const date = parseDate(value);
  const state = value > today ? 'future' : value < today ? 'locked' : 'today';
  return {
    date: value,
    label: `${String(date.getUTCMonth() + 1).padStart(2, '0')}.${String(date.getUTCDate()).padStart(2, '0')} (${DAY_NAMES[date.getUTCDay()]})`,
    state,
    numbers,
    saved,
  };
}

function playerMap(value) {
  return new Map(Object.entries(value || {}).map(([number, name]) => [Number(number), name]));
}

function permanentMap(team) {
  return new Map((permanentRecords[team] || []).map(({ number, name }) => [number, name]));
}

function randomIndex(length) {
  const values = new Uint32Array(1);
  crypto.getRandomValues(values);
  return values[0] % length;
}

function sample(values, count) {
  const pool = [...values];
  const selected = [];
  while (selected.length < count) {
    selected.push(pool.splice(randomIndex(pool.length), 1)[0]);
  }
  return selected;
}

function createNumbers(snapshot, team, mode, includePermanent) {
  const firstPlayers = playerMap(snapshot.first_players);
  const futuresPlayers = playerMap(snapshot.futures_players);
  const permanent = includePermanent ? permanentMap(team) : new Map();
  const first = new Set([...firstPlayers.keys()].filter((number) => number <= 45));
  for (const number of permanent.keys()) if (number >= 1 && number <= 45) first.add(number);

  let selected;
  if (mode === 'all') {
    const candidates = new Set([...first, ...[...futuresPlayers.keys()].filter((number) => number >= 1 && number <= 45)]);
    if (candidates.size < 6) throw new Error('1군·퓨처스 등록 번호가 6개보다 적습니다.');
    selected = sample(candidates, 6);
  } else {
    const firstCount = Number(mode);
    const other = VALID_NUMBERS.filter((number) => !first.has(number));
    if (first.size < firstCount || other.length < 6 - firstCount) {
      throw new Error('선택한 1군 선수 수에 필요한 등번호가 부족합니다.');
    }
    selected = [...sample(first, firstCount), ...sample(other, 6 - firstCount)];
  }

  return selected.sort((a, b) => a - b).map((number) => ({
    number,
    source: first.has(number) ? 'first' : 'other',
    name: firstPlayers.get(number) || permanent.get(number) || futuresPlayers.get(number) || '1군 미등록',
  }));
}

async function currentUser() {
  const client = requireSupabase();
  const { data: sessionData, error: sessionError } = await client.auth.getSession();
  if (sessionError) throw sessionError;
  if (sessionData.session?.user) return sessionData.session.user;
  const { data, error } = await client.auth.signInAnonymously();
  if (error) throw error;
  return data.user;
}

async function latestSnapshot(team, today) {
  const client = requireSupabase();
  const { data, error } = await client
    .from('roster_snapshots')
    .select('*')
    .eq('team', team)
    .lte('roster_date', today)
    .order('roster_date', { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) throw error;
  if (!data) throw new Error('아직 이 팀의 KBO 명단이 동기화되지 않았습니다.');
  return data;
}

async function putUserDraw(user, snapshot, team, mode, includePermanent, drawDate, numbers) {
  const client = requireSupabase();
  const { error } = await client.from('user_draws').upsert({
    user_id: user.id,
    draw_date: drawDate,
    team,
    mode,
    include_permanent: includePermanent,
    numbers,
    roster_date: snapshot.roster_date,
    updated_at: new Date().toISOString(),
  }, { onConflict: 'user_id,draw_date,team,mode,include_permanent' });
  if (error) throw error;
}

export async function loadWeek(team, mode, includePermanent) {
  const client = requireSupabase();
  const user = await currentUser();
  const today = kstToday();
  const dates = weekDates(today);
  const snapshot = await latestSnapshot(team, today);

  const commonFilters = (query) => query
    .eq('team', team)
    .eq('mode', mode)
    .eq('include_permanent', includePermanent)
    .in('draw_date', dates);
  const [userResult, dailyResult, savedResult] = await Promise.all([
    commonFilters(client.from('user_draws').select('draw_date,numbers')).eq('user_id', user.id),
    commonFilters(client.from('daily_results').select('draw_date,numbers')),
    commonFilters(client.from('saved_draws').select('draw_date,numbers')).eq('user_id', user.id),
  ]);
  for (const result of [userResult, dailyResult, savedResult]) if (result.error) throw result.error;

  const userByDate = new Map(userResult.data.map((row) => [row.draw_date, row.numbers]));
  const dailyByDate = new Map(dailyResult.data.map((row) => [row.draw_date, row.numbers]));
  const savedByDate = new Map(savedResult.data.map((row) => [row.draw_date, row.numbers]));
  if (dates.includes(today) && !userByDate.has(today)) {
    const numbers = createNumbers(snapshot, team, mode, includePermanent);
    await putUserDraw(user, snapshot, team, mode, includePermanent, today, numbers);
    userByDate.set(today, numbers);
  }

  return {
    team,
    mode,
    includePermanent,
    referenceDate: snapshot.roster_date,
    weekStart: dates[0],
    weekEnd: dates[dates.length - 1],
    days: dates.map((date) => {
      const numbers = date > today ? null : userByDate.get(date) || dailyByDate.get(date) || null;
      const saved = savedByDate.has(date)
        && JSON.stringify(savedByDate.get(date)) === JSON.stringify(numbers);
      return dayPayload(date, today, numbers, saved);
    }),
  };
}

export async function redrawToday(team, mode, includePermanent) {
  const user = await currentUser();
  const today = kstToday();
  const snapshot = await latestSnapshot(team, today);
  const numbers = createNumbers(snapshot, team, mode, includePermanent);
  await putUserDraw(user, snapshot, team, mode, includePermanent, today, numbers);
  return dayPayload(today, today, numbers, false);
}

export async function saveDraw(day, team, mode, includePermanent, rosterDate) {
  if (!day.numbers) throw new Error('저장할 번호가 없습니다.');
  const client = requireSupabase();
  const user = await currentUser();
  const { error } = await client.from('saved_draws').upsert({
    user_id: user.id,
    draw_date: day.date,
    team,
    mode,
    include_permanent: includePermanent,
    numbers: day.numbers,
    roster_date: rosterDate,
    saved_at: new Date().toISOString(),
  }, { onConflict: 'user_id,draw_date,team,mode,include_permanent' });
  if (error) throw error;
}
