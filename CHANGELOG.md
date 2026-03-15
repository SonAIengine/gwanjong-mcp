# Changelog

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
