# gwanjong-mcp

Stateful Pipeline MCP 서버. 4개 tool로 개발자 커뮤니티 활동 전체를 커버.

## 핵심 설계: 토큰 효율

- **Tool 4개** — description이 매 호출마다 시스템 프롬프트에 포함되므로 최소화
- **서버 상태 유지** — scout 결과를 SessionState에 캐시, ID 참조로 후속 호출
- **서버가 분석 담당** — trending/search/filter/score는 서버 내부 처리, LLM은 판단+생성만
- **반환값 압축** — 원본 데이터 전체가 아닌, 점수화된 요약만 반환

## 프로젝트 구조

```
gwanjong_mcp/
├── server.py          # FastMCP + 4 tools (gwanjong_scout/draft/strike/status)
├── types.py           # PostContent, PlatformPost, Opportunity, PostResult, DraftContext
├── state.py           # SessionState — 기회 캐시, 맥락 캐시, 활동 이력
├── pipeline.py        # scout/draft/strike 파이프라인 로직 (어댑터 조합)
└── platforms/
    ├── __init__.py    # PlatformAdapter ABC + get_adapter() registry
    ├── devto.py       # Dev.to (httpx, REST API)
    ├── bluesky.py     # Bluesky (atproto SDK)
    ├── twitter.py     # Twitter/X (tweepy)
    └── reddit.py      # Reddit (asyncpraw)
```

## Tool → Pipeline → Adapter 흐름

```python
# server.py: Tool은 얇은 진입점
@server.tool()
async def gwanjong_scout(topic: str, platforms: list[str] | None = None) -> dict:
    """설명."""
    return await pipeline.scout(topic, platforms)

# pipeline.py: 여러 어댑터를 조합하여 파이프라인 실행
async def scout(topic, platforms):
    results = []
    for adapter in get_active_adapters(platforms):
        trending = await adapter.get_trending()
        searched = await adapter.search(topic)
        results.extend(trending + searched)
    scored = _score_opportunities(results, topic)
    top = scored[:5]
    state.store_opportunities(top)          # 캐시
    return _compress(top)                    # 압축 반환

# platforms/devto.py: 단일 플랫폼 API 래핑
class DevtoAdapter(PlatformAdapter):
    async def get_trending(self, limit=20):
        resp = await self.client.get("/articles", params={"top": 7})
        return [PlatformPost(...) for item in resp.json()]
```

## SessionState 패턴

```python
# state.py
class SessionState:
    opportunities: dict[str, Opportunity]   # "opp_1" → 데이터
    contexts: dict[str, DraftContext]        # "opp_1" → 맥락
    history: list[ActionRecord]             # 실행 이력
    _counter: int                            # ID 생성용

    def store_opportunities(self, opps):     # scout 결과 저장
    def get_opportunity(self, opp_id):       # draft/strike에서 ID로 조회
    def store_context(self, opp_id, ctx):    # draft 결과 저장
    def record_action(self, action):         # strike 결과 기록
```

- 모듈 레벨 싱글턴 (`_state = SessionState()`)
- MCP 서버 프로세스 수명 동안 유지
- 직렬화 불필요 (프로세스 내 메모리)

## Platform Adapter ABC

```python
class PlatformAdapter(ABC):
    name: str
    async def get_trending(self, limit=20) -> list[PlatformPost]
    async def search(self, query: str, limit=10) -> list[PlatformPost]
    async def get_post(self, post_id: str) -> PlatformPost
    async def get_comments(self, post_id: str) -> list[Comment]
    async def write_post(self, content: PostContent) -> PostResult
    async def write_comment(self, post_id: str, body: str) -> PostResult
    async def upvote(self, post_id: str) -> PostResult
    def is_configured(self) -> bool          # 환경변수 존재 여부
```

- `get_adapter(name)` → 싱글턴, 환경변수 없으면 None 반환
- `get_active_adapters(filter)` → 설정된 어댑터만 리스트

## 코드 스타일

- Python 3.10+, async/await
- 타입 힌트 필수 (`dict[str, Any]`, `list[str]` 소문자 generic)
- dataclass로 내부 모델, dict로 외부 반환
- logging → stderr (stdout은 MCP 프로토콜 전용)

## 빌드/실행

```bash
pip install -e ".[all,dev]"     # 로컬 개발
python run.py                    # 직접 실행
gwanjong-mcp                     # CLI (설치 후)
python -m gwanjong_mcp           # 모듈 실행
pytest                           # 테스트
```

## 의존성

- **코어**: mcp[cli], httpx, python-dotenv
- **Bluesky**: atproto
- **Twitter**: tweepy
- **Reddit**: asyncpraw
- **개발**: pytest, pytest-asyncio, mypy, ruff
