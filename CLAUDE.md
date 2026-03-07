# gwanjong-mcp

Stateful Pipeline MCP 서버. 5개 tool로 개발자 커뮤니티 활동 전체를 커버.

## 핵심 설계: 토큰 효율

- **Tool 5개** — description이 매 호출마다 시스템 프롬프트에 포함되므로 최소화
- **서버 상태 유지** — scout 결과를 GwanjongState에 캐시, ID 참조로 후속 호출
- **서버가 분석 담당** — trending/search/filter/score는 서버 내부 처리, LLM은 판단+생성만
- **반환값 압축** — 원본 데이터 전체가 아닌, 점수화된 요약만 반환

## 프로젝트 구조

```
gwanjong_mcp/
├── __init__.py        # 패키지
├── __main__.py        # python -m gwanjong_mcp
├── server.py          # PipelineMCP + 5 tools + GwanjongState 정의
├── setup.py           # 플랫폼 온보딩 (check/guide/save + 연결 테스트)
├── pipeline.py        # scout/draft/strike 파이프라인 로직 (devhub Hub 사용)
├── types.py           # Opportunity, DraftContext, ActionRecord
run.py                 # 직접 실행 진입점
```

## 의존성 구조

```
gwanjong-mcp
├── mcp-pipeline   — PipelineMCP, State (stores/requires 선언적 체이닝)
├── devhub[all]    — Hub, DevTo, Bluesky, Twitter, Reddit (플랫폼 클라이언트)
└── python-dotenv  — ~/.gwanjong/.env 로드
```

platforms/ 디렉토리 없음 — devhub를 직접 import하여 사용.

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

## GwanjongState (mcp_pipeline.State)

```python
class GwanjongState(State):
    opportunities: dict[str, Any] = {}  # scout 결과 (opp_id → Opportunity)
    contexts: dict[str, Any] = {}       # draft 결과 (opp_id → {context, post_id})
    history: list[dict] = []            # strike 이력
```

## 설정 파일

- **~/.gwanjong/.env** — 플랫폼 API 키 저장 (setup.py가 관리)
- 서버 시작 시 자동 로드 (`dotenv.load_dotenv`)

## 코드 스타일

- Python 3.10+, async/await
- 타입 힌트 필수 (`dict[str, Any]`, `list[str]` 소문자 generic)
- dataclass로 내부 모델, dict로 외부 반환
- logging → stderr (stdout은 MCP 프로토콜 전용)

## 빌드/실행

```bash
# 로컬 개발 (mcp-pipeline, devhub도 로컬 설치)
uv pip install -e ../mcp-pipeline -e "../devhub[all]" -e ".[dev]"
python run.py                    # 직접 실행
gwanjong-mcp                     # CLI (설치 후)
python -m gwanjong_mcp           # 모듈 실행
.venv/bin/python -m pytest tests/ -v  # 테스트
```

## 알려진 이슈

- devhub bluesky.get_trending: q="*" 타임아웃 → search로 대체 필요
- devhub twitter: Bearer Token 미지원 → 검색 시 401 (어댑터 수정 필요)
- twikit: 서버에서 Cloudflare 차단 → tweepy + Bearer Token으로 대체
- Dev.to API: DELETE 미지원 (unpublish만 가능)
