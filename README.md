# KBO 리그 최애팀 주간 등번호 로또

KBO 10개 팀의 1군·퓨처스 선수 등번호를 이용해 주간 로또 번호를 추천하는 React 페이지입니다. 팀 색상과 로고, 1군 선수 수 0~6명·전체, 영구결번 포함 여부를 선택할 수 있습니다.

## 배포 파일 분류

| 경로 | 역할 | GitHub 업로드 |
| --- | --- | --- |
| `src/`, `logos/`, `index.html`, `package*.json`, `vite.config.js` | GitHub Pages에 빌드할 React 화면 | 예 |
| `kbo_team_colors.json`, `kbo_permanant_numbers.json` | 팀 색상·영구결번 데이터 | 예 |
| `supabase/schema.sql` | Supabase 테이블·RLS 정책 | 예. 대시보드 SQL Editor에서 실행 |
| `automation/` | KBO 명단 수집 및 전날 160개 조건 고정 | 예. GitHub Actions가 실행 |
| `.github/workflows/` | Pages 배포와 3시간 주기 명단 동기화 | 예 |
| `.env.example` | 로컬 환경 변수 형식 | 예 |
| `.env.local`, `*.db`, `node_modules/`, `dist/` | 비밀 값·로컬 결과·생성 파일 | 아니오. `.gitignore`에서 제외 |

## 1. Supabase 준비

1. [Supabase 프로젝트](https://supabase.com/dashboard/project/ziejjlfyrrrmwrxjxzaz)의 **SQL Editor**를 엽니다.
2. [`supabase/schema.sql`](supabase/schema.sql)의 전체 내용을 실행합니다.
3. **Authentication → Providers → Anonymous Sign-Ins**를 활성화합니다.
4. **Project Settings → API Keys**에서 publishable key와 secret key를 확인합니다.

브라우저는 publishable key만 사용합니다. `sb_secret_...` 또는 기존 `service_role` 키는 파일에 적거나 `VITE_` 환경 변수로 만들면 안 됩니다.

## 2. GitHub Secrets 등록

GitHub 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서 다음 두 값을 만듭니다.

| Secret 이름 | 값 |
| --- | --- |
| `SUPABASE_PUBLISHABLE_KEY` | Supabase publishable key |
| `SUPABASE_SECRET_KEY` | Supabase secret key 또는 기존 service_role key |

## 3. GitHub Pages 배포

1. 이 폴더의 파일을 저장소 `python` 브랜치에 commit/push합니다.
2. GitHub 저장소의 **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 선택합니다.
3. **Actions**에서 `Sync KBO data`를 한 번 수동 실행해 명단을 채웁니다.
4. `Deploy GitHub Pages` 작업이 끝나면 Pages 주소를 엽니다.

예약 동기화는 한국 시간 기준 매일 00:17부터 3시간마다 실행됩니다. 전날 결과는 `daily_results`의 기존 행을 덮어쓰지 않으므로 고정됩니다. 월요일 결과는 만들지 않습니다.

## 로컬 실행

Node.js 22 이상과 Python 3.12 이상을 권장합니다.

```powershell
Copy-Item .env.example .env.local
# .env.local의 VITE_SUPABASE_PUBLISHABLE_KEY를 실제 publishable key로 수정
npm install
npm run dev
```

KBO 동기화 작업을 로컬에서 시험할 때만 현재 PowerShell 세션에 secret key를 넣습니다.

```powershell
python -m pip install -r automation/requirements.txt
$env:SUPABASE_SECRET_KEY = "본인의 secret key"
python automation/sync_supabase.py
```

## 저장 방식

- 첫 방문 때 Supabase 익명 계정을 만들고 브라우저에 세션을 보관합니다.
- 오늘 번호와 다시 뽑은 결과는 `user_draws`에 선수 이름까지 저장합니다.
- 브라우저 저장 데이터를 지우거나 다른 기기를 사용하면 기존 익명 계정에 다시 접근할 수 없습니다.
- IP 주소는 사용자 식별자로 사용하지 않습니다.

상세 설계는 [`docs/GITHUB_PAGES_DB_PLAN.md`](docs/GITHUB_PAGES_DB_PLAN.md)를 참고하세요.
