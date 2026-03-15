# Changelog

## [0.4.1] — 2026-03-16

### 운영 콘솔 + 에이전트 캐릭터 시스템 + 실전 운영 안정화

v0.4.0 출시 후 실제 운영하면서 발견된 20+ 이슈 수정. 대시보드를 마케팅 운영 콘솔로 고도화.

#### 운영 콘솔 (대시보드 고도화)
- **탭 구조**: 현황판 / 에이전트 / 캠페인 3탭
- **전체 한국어화**: 탭, 카드, 모달, 상태 메시지, 시간 표현 ("5분 전", "방금")
- **Daemon 제어**: subprocess로 에이전트 시작/중지, 실시간 로그
- **Campaign UI**: 캠페인 생성 모달 + KPI 리포트 (진행률 바, 플랫폼/액션 차트)
- **승인 큐 개선**: 원본 글 링크 + 댓글 내용 미리보기 + 전체 승인/거부 버튼
- **실패 항목**: 기본 숨김 (접이식 "재시도 대기 N건"), 자동 재시도 안내

#### 에이전트 캐릭터 시스템
- **팀원 관리**: 이름, 성격, 담당 토픽/플랫폼, 말투 설정
- **DiceBear 아바타**: 6종 스타일 (로봇, 이모지, 픽셀, 모험가 등) + 실시간 미리보기
- **카드 UI**: 아바타 + 상태 인디케이터(실행/대기) + 실적(오늘/주간/누적) + 출근/수정/해고
- **personality → LLM 반영**: 환경변수로 daemon에 전달 → 시스템 프롬프트에 CHARACTER 섹션
- **승인/자율 모드**: 팀원별 독립 설정, 플랫폼 체크박스 선택
- **에이전트별 독립 subprocess**: 각자 다른 토픽/플랫폼으로 병렬 실행
- `agents` SQLite 테이블 + CRUD API (GET/POST/PATCH/DELETE + start/stop/logs)

#### 중복 방지 (3중 차단)
- **URL 정규화**: `#comments` fragment 제거하여 일관된 비교
- **seen_posts acted 매칭**: strike 성공 + 승인 큐 등록 시 acted=1 기록
- **author 분산**: 같은 저자 최대 1건 + 이전 활동 저자 제외 (같은 사람 6번 댓글 방지)

#### 안전장치 강화
- **자율 모드 post 차단**: comment만 허용 (내 계정에 남의 서비스 홍보 트윗 사고 방지)
- **연속 실패 자동 차단**: 3회 연속 실패 → 해당 플랫폼 24시간 자동 차단 + 자동 해제
- **strike.failed 이벤트**: 실패 추적용 신규 EventBus 이벤트
- **strike 실패 시 actions 미기록**: result.success=False면 strike.after 이벤트 미발행

#### Twitter 대응
- **twikit→tweepy fallback**: 읽기 메서드 5개에 try/except 자동 전환 (devhub-social)
- **독립 트윗 방지**: write_comment 후 in_reply_to_user_id 검증, 독립이면 삭제 + 실패 반환
- **URL 자동 생성**: write_comment 응답에 URL 없으면 post_id로 fallback
- **twikit 크레덴셜 비활성화**: 서버 Cloudflare 차단 회피, tweepy Bearer Token 전용

#### 기타 개선
- **자동 대댓글**: 답글 감지 시 원본 댓글 + 상대방 답글을 맥락으로 LLM 대댓글 생성
- **쿨다운 완화**: 30분→5분, 일일 한도 상향 (devto 3→5, bluesky/twitter 5→8)
- **승인 실패 자동 재시도**: 매 사이클마다 failed 항목 자동 retry
- **미니어처 게임 스팸 필터**: Marvel Crisis Protocol(MCP 동명이의) 키워드 추가
- **서버 재시작 시 에이전트 상태 복구**: on_startup에서 running→idle 리셋
- **Blocked 에러 처리**: 500 대신 JSON 응답 + failed→reject 허용

#### 배포 구조 변경
- Docker → **systemd user service** (`gwanjong-dashboard.service`)
- 이유: Docker 컨테이너에 claude CLI 없어서 LLM 생성 불가
- `.env` + `memory.db` 로컬 직접 접근
- `systemctl --user enable gwanjong-dashboard` (부팅 시 자동 시작)

#### 플랫폼 설정
- GitHub Discussions: 토큰 + 6개 repo 설정 완료 (MCP spec, langchain, autogen 등)
- 한국어 README 추가 (README.ko.md)

---

## [0.4.0] — 2026-03-15

### AI Growth OS 확장 — 채널 자동화에서 캠페인 기반 GTM 운영 시스템으로

v0.3.0의 커뮤니티 댓글/포스트 자동화 엔진을 **마케팅팀을 대체할 수 있는 AI Growth OS**로 확장.
MCP Tool 5→8개, 테스트 61→100개, 코드 4,375→8,473줄.

#### Phase 1: Campaign 모델
- `gwanjong_campaign` tool 추가 (create/list/get/update/report)
- `CampaignManager`: SQLite 기반 CRUD + KPI 달성률 리포트
- `Campaign` dataclass: objective, topics, platforms, ICP, CTA, kpi_target
- actions 테이블에 `campaign_id` 컬럼 추가 — 모든 활동을 캠페인 단위로 추적
- daemon `--campaign` 옵션으로 자율 모드에서 캠페인 연동

#### Phase 2: Conversion Tracking
- `ConversionTracker` EventBus 플러그인 (strike.before/after 구독)
- `strike.before`: 콘텐츠 내 URL에 UTM 파라미터 자동 삽입
- `strike.after`: conversions 테이블에 전환 이벤트 기록
- UTM 형식: `?utm_source={platform}&utm_medium={action}&utm_campaign={campaign_id}`
- 기존 UTM이 있는 URL은 건너뛰기 (중복 방지)
- campaign report에 전환 데이터 통합

#### Phase 3: Asset Library + Message Framework
- `gwanjong_assets` tool 추가 (save/search/list/use)
- `AssetLibrary`: 콘텐츠 에셋 저장/검색/재사용 + usage_count 추적
- `MessageFramework`: ICP별 메시지 프레임 관리
  - persona_segment별 value_prop, proof_points, objections, hooks
  - `select_hook()`: 캠페인 프레임에서 hook 선택
  - `get_objection_response()`: 반론 매칭

#### Phase 4: Content Calendar + Scheduler
- `gwanjong_schedule` tool 추가 (add/list/cancel/check)
- `Scheduler` EventBus 플러그인: 예약 시간 도래 시 pipeline.strike 자동 실행
- autonomous run_cycle에 scheduler.process_due() 통합 — 매 사이클마다 due 항목 처리
- schedule 테이블: pending/published/failed/cancelled 상태 관리

#### Phase 5: Measurement 고도화
- `Measurement` 클래스:
  - `campaign_attribution()`: 플랫폼/액션별 기여도 분석
  - `action_performance()`: reply rate 등 engagement 지표
  - `weekly_report()`: 주간 활동/전환/일별 추이 리포트
  - `best_performing()`: 최고 성과 채널/액션 식별
- A/B 실험: `ab_create()` → `ab_result()` → `ab_conclude()`
- experiments 테이블: variant A/B, metric, status, result

#### Phase 6: 전략 자동화
- `StrategyEngine`:
  - `generate_weekly_plan()`: 지난주 성과 기반 다음 주 플랜 생성
  - `auto_approve_low_risk()`: comment만 자동 스케줄 (post는 수동)
  - `suggest_topic_rotation()` / `suggest_platform_allocation()`
- daemon `--auto-plan` 옵션: 첫 사이클에서 주간 플랜 자동 생성

#### 인프라 변경
- `storage.py`: 6개 ensure 함수 추가, `_ensure_column()` 테이블 미존재 시 안전하게 건너뛰기
- `server.py`: GwanjongState에 campaigns/assets 필드, ConversionTracker bus 연결
- `pipeline.py`: scout/strike에 campaign_id 전파 (이벤트 데이터)
- `memory.py`: actions INSERT에 campaign_id/utm_url 포함
- `dashboard.py`: `/api/campaigns`, `/api/conversions`, `/api/schedule` endpoint 추가
- SQLite 테이블 6→12개 (+campaigns, conversions, assets, message_frames, schedule, experiments)

#### 테스트
- 39개 테스트 추가 (총 100개, 5 deselected는 integration marker)
- test_campaign.py (8), test_conversion.py (7), test_asset.py (7)
- test_message.py (6), test_scheduler.py (5), test_measure.py (6), test_strategy.py (6)

---

## [0.3.0] — 2026-03-13

오픈소스 채택성 대폭 개선 — Docker, CI, 환경변수 경로, i18n, Quick Start.

## [0.2.1] — 2026-03-12

Approval workflow + dashboard controls.

## [0.2.0] — 2026-03-11

자율 에이전트 아키텍처 — EventBus, 플러그인, Twitter 스크래핑, 대시보드.

## [0.1.0] — 2026-03-09

최초 릴리즈 — 5 MCP tools, 6 플랫폼, scout→draft→strike pipeline.
