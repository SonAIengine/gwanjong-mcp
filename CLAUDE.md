# gwanjong-mcp

Stateful Pipeline MCP 서버 + 자율 소셜 에이전트. 5개 tool + EventBus 플러그인 아키텍처.

## 핵심 설계: 토큰 효율 + 느슨한 결합

- **Tool 5개** — description이 매 호출마다 시스템 프롬프트에 포함되므로 최소화
- **서버 상태 유지** — scout 결과를 GwanjongState에 캐시, ID 참조로 후속 호출
- **서버가 분석 담당** — trending/search/filter/score는 서버 내부 처리, LLM은 판단+생성만
- **EventBus** — 모듈 간 직접 import 없이 이벤트 pub/sub. 플러그인 유무와 무관하게 동작

## 프로젝트 구조

```
gwanjong_mcp/
├── __init__.py        # 패키지
├── __main__.py        # python -m gwanjong_mcp
├── server.py          # PipelineMCP + 5 tools + GwanjongState + EventBus 조립
├── events.py          # EventBus + Event + Blocked (유일한 공유 지점)
├── pipeline.py        # scout/draft/strike 코어 (registry + EventBus)
├── types.py           # Opportunity, DraftContext, ActionRecord
├── setup.py           # 플랫폼 온보딩 (registry 기반 동적 가이드)
├── browser.py         # Dev.to 브라우저 자동화
│
│  ── EventBus 플러그인 (독립 모듈) ──
│
├── safety.py          # Rate limiting + 콘텐츠 검증 (strike.before 차단)
├── memory.py          # SQLite 영속 저장소 (활동 이력 + 중복 방지)
├── tracker.py         # 답글 추적 (댓글 남긴 게시글의 새 답글 감지)
├── monitor.py         # 모니터링 데이터 집계 (SQLite → JSON)
├── dashboard.py       # 대시보드 웹서버 (aiohttp, 별도 프로세스)
├── static/index.html  # 대시보드 프론트엔드 (단일 HTML, vanilla JS)
├── persona.py         # 플랫폼별 페르소나 관리 (파일 I/O만)
├── llm.py             # 내장 LLM 댓글 생성 (anthropic SDK)
├── autonomous.py      # 자율 루프 엔진 (pipeline + events만 의존)
├── daemon.py          # gwanjong-daemon CLI 진입점
run.py                 # 직접 실행 진입점
```

## 실행 모드

```bash
# MCP 모드 (기존) — LLM이 tool 호출
gwanjong-mcp

# Daemon 모드 (자율) — 자체 사이클
gwanjong-daemon --topics "MCP,LLM" --interval 4 --max-actions 3

# 옵션
gwanjong-daemon --require-approval    # strike 전 승인 필요
gwanjong-daemon --dry-run             # scout+draft만, strike 안 함
gwanjong-daemon --model claude-haiku-4-5-20251001
gwanjong-daemon --platforms devto,bluesky
gwanjong-daemon --max-cycles 1        # 1회만 실행
```

## 아키텍처: EventBus + Registry

### EventBus (events.py)
모듈 간 직접 import 없이 이벤트 pub/sub으로 소통:
- `scout.done` — scout 완료 시 발행
- `draft.done` — draft 완료 시 발행
- `strike.before` — strike 실행 전 (핸들러가 False 반환 시 Blocked 예외로 차단)
- `strike.after` — strike 완료 후 발행

플러그인(memory, safety 등)은 `bus.on()`으로 구독만 하면 됨.
pipeline은 누가 듣고 있는지 모르고, 플러그인이 없어도 동작함.

### Registry (devhub.registry)
플랫폼 어댑터를 하드코딩 없이 동적 탐지:
- 내장 어댑터: devhub 패키지 내 DevTo, Bluesky, Twitter, Reddit
- 외부 플러그인: `devhub.adapters` entry_point 그룹으로 등록
- `get_adapter_class(platform)` → 어댑터 클래스 반환
- `get_configured_adapters()` → 환경변수 설정된 어댑터만 인스턴스화
- 각 어댑터의 `setup_guide()` → 온보딩 가이드 + allowed_actions 제공

### 모듈 독립성
각 플러그인은 events.py만 의존하고 서로 import하지 않음:
```
pipeline.py ──emit──→ EventBus ←──subscribe── safety.py
                          ↑                    memory.py
                          │                    tracker.py
                          │                    (growth 등 미래 플러그인)
                     server.py에서 조립
```

## 플러그인 상세

### safety.py (strike.before + strike.after)
- **Rate Limiter**: 플랫폼별 일일 제한, 최소 간격 (30분), 에러 쿨다운
- **Content Guard**: AI 패턴 단어 탐지, 오프너 패턴, 칭찬→경험→질문 공식, 자기홍보 비율
- SQLite `rate_log` 테이블에 활동 기록
- `DEFAULT_LIMITS`: devto 3/day, bluesky 5/day, twitter 5/day, reddit 3/day

### memory.py (scout.done + strike.after)
- SQLite `~/.gwanjong/memory.db`
- `actions` 테이블: 모든 활동 이력 영속화
- `seen_posts` 테이블: 중복 활동 방지
- `filter_unseen()`: 이미 활동한 기회 필터링

### persona.py (독립, EventBus 불필요)
- `~/.gwanjong/persona.json`에서 플랫폼별 톤/스타일/길이 로드
- 없으면 내장 기본값 사용
- `Persona.to_system_prompt()` → LLM 시스템 프롬프트 생성

### llm.py (persona 선택적 의존)
- anthropic SDK로 댓글 생성 (자율 모드용)
- 기본 모델: claude-haiku-4-5-20251001 (비용 최적화)
- pipeline의 `_build_writing_guide()` + `WRITING_AVOID` 재사용

### tracker.py (strike.after 구독, reply.detected 발행)
- memory.db `actions` 테이블에서 댓글 이력 조회
- 해당 게시글의 댓글 트리에서 gwanjong 댓글에 대한 답글 감지
- `replies` 테이블에 감지된 답글 저장 (중복 방지)
- Dev.to: DEVTO_USERNAME 없으면 `/users/me` API로 자동 조회
- `get_pending_replies()`: 미응답 답글 조회
- `mark_responded()`: 응답 완료 표시

### autonomous.py (pipeline + events만 의존)
- `run_cycle(topic)`: scout→draft→generate→strike→reply scan 한 사이클
- `run_daemon()`: 주기적 사이클 (기본 4시간)
- safety/memory가 bus에 있으면 자동 개입, 없어도 동작
- `track_replies=True`: 사이클 끝에 답글 스캔 (기본 활성)

## Tool 5개

### gwanjong_setup (온보딩)
- `action="check"` → 플랫폼별 설정 상태 반환
- `action="guide"` → API 키 발급 단계별 안내
- `action="save"` → ~/.gwanjong/.env에 키 upsert + 연결 테스트

### gwanjong_scout (stores="opportunities")
- devhub Hub로 trending + search 병렬 실행
- _score_relevance()로 점수화 → 상위 N개 Opportunity 생성
- state.opportunities에 캐시

### gwanjong_draft (stores="contexts", requires="opportunities")
- Opportunity의 post_id로 게시글 + 댓글 조회
- 분위기 분석 + 접근 방식 추천
- state.contexts에 캐시

### gwanjong_strike (requires="contexts")
- DraftContext 기반으로 댓글/게시글/upvote 실행
- state.history에 이력 기록

### _status (자동 생성)
- PipelineMCP가 자동 등록
- state 필드 상태 + available/blocked tools

## 설정 파일

- **~/.gwanjong/.env** — 플랫폼 API 키 (setup.py가 관리)
- **~/.gwanjong/persona.json** — 페르소나 설정 (선택)
- **~/.gwanjong/memory.db** — SQLite 영속 저장소 (자동 생성)

## 의존성 구조

```
gwanjong-mcp
├── mcp-pipeline   — PipelineMCP, State (stores/requires 선언적 체이닝)
├── devhub[all]    — Hub, registry, PlatformAdapter (플랫폼 클라이언트 + 플러그인)
├── python-dotenv  — ~/.gwanjong/.env 로드
├── anthropic      — [autonomous] extra, LLM 댓글 생성용
└── aiohttp        — [dashboard] extra, 모니터링 웹서버
```

## 코드 스타일

- Python 3.10+, async/await
- 타입 힌트 필수 (`dict[str, Any]`, `list[str]` 소문자 generic)
- dataclass로 내부 모델, dict로 외부 반환
- logging → stderr (stdout은 MCP 프로토콜 전용)

## 빌드/실행

```bash
# 로컬 개발 (mcp-pipeline, devhub도 로컬 설치)
uv pip install -e ../mcp-pipeline -e "../devhub[all]" -e ".[all,dev]"
gwanjong-mcp                     # MCP 서버
gwanjong-daemon -t "MCP,LLM"     # 자율 데몬
gwanjong-dashboard --port 8585   # 모니터링 대시보드
python -m gwanjong_mcp           # 모듈 실행
.venv/bin/python -m pytest tests/ -v  # 테스트
```

## 알려진 이슈

- devhub bluesky.get_trending: ✅ whats-hot 피드로 수정 완료
- devhub twitter: ✅ Bearer Token 지원 완료 (TWITTER_BEARER_TOKEN 환경변수)
- twikit: 서버에서 Cloudflare 차단 → tweepy + Bearer Token으로 대체
- Dev.to API: DELETE 미지원 (unpublish만 가능)
