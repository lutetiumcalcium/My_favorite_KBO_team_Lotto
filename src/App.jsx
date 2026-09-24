import React, { useCallback, useEffect, useRef, useState } from 'react';
import teamColors from '../kbo_team_colors.json';
import { loadWeek, redrawToday, saveDraw } from './cloudApi';

const logoUrls = import.meta.glob('../logos/*.png', { eager: true, query: '?url', import: 'default' });
const logoFor = (name) => logoUrls[`../logos/${name}.png`];
const teams = Object.keys(teamColors).filter((name) => name !== 'KBO');
const modes = [...Array(7).keys()].map((count) => ({ value: String(count), label: `${count}명` }));
modes.push({ value: 'all', label: '전체' });

function formatDate(value) {
  return value?.replaceAll('-', '.') ?? '';
}

function modeDescription(mode, includePermanent) {
  let description;
  if (mode === 'all') description = '1군과 퓨처스 등록 번호를 합쳐 6개를 뽑습니다.';
  const first = Number(mode);
  if (mode !== 'all' && first === 0) description = '1군에 등록되지 않은 번호에서 6개를 뽑습니다.';
  if (mode !== 'all' && first === 6) description = '1군 등록 번호에서만 6개를 뽑습니다.';
  if (mode !== 'all' && first > 0 && first < 6) {
    description = `1군 등록 번호 ${first}개와 1군 미등록 번호 ${6 - first}개를 뽑습니다.`;
  }
  return `${description} 영구결번은 1군 후보에 ${includePermanent ? '포함합니다' : '포함하지 않습니다'}.`;
}

function DayCard({ day, onSave, saving }) {
  const label = day.state === 'future'
    ? '예정 · 당일 생성'
    : day.state === 'locked'
      ? '지난 번호 고정'
      : '오늘 · 다시 뽑기 가능';
  return (
    <article className="day-card">
      <div className="day-main">
        <div className="day-date-block">
          <strong className="day-date">{day.label}</strong>
          <span className={`day-status ${day.state}`}>{label}</span>
        </div>
        <div className="number-list" aria-label={`${day.label} 추천 번호`}>
          {day.numbers
            ? day.numbers.map(({ number, source, name }) => (
                <span className="number-item" key={number}>
                  <span className={`number-ball ${source === 'first' ? 'first' : 'other'}`}>
                    {String(number).padStart(2, '0')}
                  </span>
                  {name && name !== '1군 미등록' && (
                    <span className="player-name" title={name}>{name}</span>
                  )}
                </span>
              ))
            : [...Array(6).keys()].map((index) => <span className="number-ball pending" key={index}>–</span>)}
        </div>
        <button
          className={`day-save-button ${day.saved ? 'saved' : ''}`}
          type="button"
          disabled={!day.numbers || day.saved || saving}
          aria-label={`${day.label} 번호 ${day.saved ? '저장됨' : '저장'}`}
          onClick={() => onSave(day)}
        >
          {saving ? '저장 중…' : day.saved ? '저장됨' : '저장'}
        </button>
      </div>
    </article>
  );
}

export default function App() {
  const [team, setTeam] = useState('');
  const [mode, setMode] = useState('3');
  const [includePermanent, setIncludePermanent] = useState(false);
  const [week, setWeek] = useState(null);
  const [loading, setLoading] = useState(false);
  const [redrawing, setRedrawing] = useState(false);
  const [savingDate, setSavingDate] = useState('');
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [modeOpen, setModeOpen] = useState(false);
  const modeRef = useRef(null);
  const modeButtonRef = useRef(null);
  const teamColor = team ? teamColors[team] : teamColors.KBO;
  const logoName = team || 'KBO';

  useEffect(() => {
    let timer;
    const scheduleRefresh = () => {
      const now = Date.now();
      const kstOffset = 9 * 60 * 60 * 1000;
      const nextMidnight = (Math.floor((now + kstOffset) / 86400000) + 1) * 86400000 - kstOffset;
      timer = window.setTimeout(() => {
        setRefreshKey((current) => current + 1);
        scheduleRefresh();
      }, nextMidnight - now + 250);
    };
    scheduleRefresh();
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!modeOpen) return undefined;
    const closeOutside = (event) => {
      if (!modeRef.current?.contains(event.target)) setModeOpen(false);
    };
    const closeEscape = (event) => {
      if (event.key === 'Escape') {
        setModeOpen(false);
        modeButtonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeEscape);
    };
  }, [modeOpen]);

  useEffect(() => {
    if (!team) {
      setWeek(null);
      setError('');
      setLoading(false);
      return undefined;
    }
    let active = true;
    setWeek(null);
    setError('');
    setLoading(true);
    loadWeek(team, mode, includePermanent)
      .then((payload) => {
        if (active) setWeek(payload);
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [team, mode, includePermanent, refreshKey]);

  const redraw = useCallback(async () => {
    if (!team || !week || redrawing) return;
    setRedrawing(true);
    setError('');
    try {
      const day = await redrawToday(team, mode, includePermanent);
      setWeek((current) => ({
        ...current,
        days: current.days.map((item) => item.date === day.date ? day : item),
      }));
    } catch (cause) {
      setError(cause.message);
    } finally {
      setRedrawing(false);
    }
  }, [team, mode, includePermanent, redrawing, week]);

  const save = useCallback(async (day) => {
    if (!week || !day.numbers || day.saved || savingDate) return;
    setSavingDate(day.date);
    setError('');
    try {
      await saveDraw(day, team, mode, includePermanent, week.referenceDate);
      setWeek((current) => ({
        ...current,
        days: current.days.map((item) => item.date === day.date ? { ...item, saved: true } : item),
      }));
    } catch (cause) {
      setError(cause.message);
    } finally {
      setSavingDate('');
    }
  }, [week, team, mode, includePermanent, savingDate]);

  const today = week?.days.find((day) => day.state === 'today');

  return (
    <div className="app-shell" style={{ '--team-color': teamColor }}>
      <header className="hero">
        <div className="hero-inner">
          <div className={`brand-mark ${team ? 'team-logo' : 'kbo-logo'}`}>
            <img src={logoFor(logoName)} alt={`${logoName} 로고`} />
          </div>
          <p className="eyebrow">KOREA BASEBALL ORGANIZATION</p>
          <h1>KBO 주간 등번호 로또</h1>
          <p className="subtitle">좋아하는 팀의 등번호로 이번 주 여섯 숫자를 만나보세요.</p>
        </div>
      </header>

      <main className="page">
        <section className="selection-panel" aria-label="추첨 조건">
          <div className="selection-heading">
            <div>
              <p className="section-kicker">DRAW SETTINGS</p>
              <h2>추첨 조건 선택</h2>
            </div>
            {team && <span className="selected-team-mark"><i style={{ background: teamColor }} />{team}</span>}
          </div>
          <div className="select-row">
            <label className="select-field">
              <span>팀 선택</span>
              <select value={team} onChange={(event) => setTeam(event.target.value)}>
                <option value="">팀을 선택해 주세요</option>
                {teams.map((name) => <option value={name} key={name}>{name}</option>)}
              </select>
            </label>
            <div className="select-field mode-field" ref={modeRef}>
              <span id="mode-label">1군 선수 수</span>
              <button
                ref={modeButtonRef}
                className="mode-button"
                type="button"
                aria-labelledby="mode-label"
                aria-haspopup="listbox"
                aria-expanded={modeOpen}
                onClick={() => setModeOpen((open) => !open)}
              >
                {modes.find((item) => item.value === mode)?.label}
                <span aria-hidden="true">⌄</span>
              </button>
              {modeOpen && (
                <div className="mode-menu" role="listbox" aria-labelledby="mode-label">
                  {modes.map(({ value, label }) => (
                    <button
                      className={`mode-option ${mode === value ? 'active' : ''}`}
                      type="button"
                      role="option"
                      aria-selected={mode === value}
                      key={value}
                      onClick={() => {
                        setMode(value);
                        setModeOpen(false);
                        modeButtonRef.current?.focus();
                      }}
                    >
                      {label}<span aria-hidden="true">{mode === value ? '✓' : ''}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="select-field permanent-field">
              <span id="permanent-label">영구결번 1군 포함 여부</span>
              <div className="permanent-toggle" role="group" aria-labelledby="permanent-label">
                <button
                  type="button"
                  className={includePermanent ? 'active' : ''}
                  aria-pressed={includePermanent}
                  onClick={() => setIncludePermanent(true)}
                >예</button>
                <button
                  type="button"
                  className={!includePermanent ? 'active' : ''}
                  aria-pressed={!includePermanent}
                  onClick={() => setIncludePermanent(false)}
                >아니오</button>
              </div>
            </div>
          </div>
          <div className="rule-note"><span className="rule-icon">i</span>{modeDescription(mode, includePermanent)}</div>
        </section>

        {!team ? (
          <section className="empty-panel" aria-live="polite">
            <div className="empty-symbol" aria-hidden="true">⚾</div>
            <h2>팀을 선택해 주세요</h2>
            <p>선택한 팀의 주간 등번호 로또가 여기에 표시됩니다.</p>
          </section>
        ) : (
          <section className="results-panel" aria-live="polite">
            <div className="results-top">
              <div>
                <p className="section-kicker">THIS WEEK&apos;S NUMBERS</p>
                <h2>{week ? `${formatDate(week.weekStart)} ~ ${formatDate(week.weekEnd)}` : '주간 번호'}</h2>
                {week && <p className="reference-date">KBO 기준 {formatDate(week.referenceDate)}</p>}
              </div>
              <div className="legend">
                <span><i className="legend-ball first" />{includePermanent ? '1군·영구결번' : '1군 선수 번호'}</span>
                <span><i className="legend-ball other" />{mode === 'all' ? '퓨처스 선수 번호' : '1군 미등록 번호'}</span>
              </div>
            </div>
            {loading && <div className="message-panel">KBO 등록 명단과 저장된 번호를 불러오는 중입니다…</div>}
            {!loading && error && <div className="message-panel error" role="alert">{error}<button onClick={() => setRefreshKey((value) => value + 1)}>다시 시도</button></div>}
            {!loading && week && (
              <>
                <div className="day-list">{week.days.map((day) => (
                  <DayCard day={day} key={day.date} onSave={save} saving={savingDate === day.date} />
                ))}</div>
                <p className="save-feature-note">저장한 번호와 선수 이름은 내 익명 계정에 보관됩니다.</p>
                <div className="actions">
                  <button className="primary-button" onClick={redraw} disabled={!today || redrawing}>
                    {redrawing ? '뽑는 중…' : '오늘 번호 다시 뽑기'}
                  </button>
                  <button className="secondary-button" onClick={() => setRefreshKey((value) => value + 1)}>
                    주간 데이터 새로고침
                  </button>
                </div>
              </>
            )}
          </section>
        )}

        <p className="source-note">
          <a href="https://www.koreabaseball.com/Player/RegisterAll.aspx" target="_blank" rel="noreferrer">KBO 1군 등록 현황</a>
          <span>·</span>
          <a href="https://www.koreabaseball.com/Futures/Player/Register.aspx" target="_blank" rel="noreferrer">퓨처스 선수 등록 현황</a>
          <span>·</span>
          1~45 등번호 사용, 0으로 시작하는 번호 제외
        </p>
      </main>
    </div>
  );
}
