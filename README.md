# gwanjong-mcp

AI 소셜 에이전트를 위한 **Stateful Pipeline MCP 서버**.

단순 API 래퍼가 아닌, 서버 내부에서 탐색→분석→선별을 한번에 처리하여
**최소한의 LLM 라운드트립으로 커뮤니티 활동을 수행**합니다.

## 왜 다른가

일반적인 MCP 서버는 CRUD tool을 나열하고 LLM이 하나씩 호출합니다.
댓글 하나 다는 데 10번의 tool 호출, 10번의 LLM 왕복이 필요합니다.

```
일반 MCP (14 tools, 9+ 라운드트립):
LLM → list → LLM → trending → LLM → search → LLM → analyze → LLM → get_post
→ LLM → get_comments → LLM → preview → LLM → write → LLM

gwanjong-mcp (5 tools, 3 라운드트립):
LLM → scout → LLM → draft → LLM(콘텐츠 생성) → strike → 완료
(+ setup: 최초 1회 플랫폼 온보딩)
```

### 설계 원칙

1. **Tool 수 최소화** — 5개. tool description이 매 호출마다 시스템 프롬프트에 포함되므로 적을수록 좋음
2. **서버 상태 유지** — scout 결과를 서버가 캐시. LLM이 매번 같은 정보를 반복 전달할 필요 없음
3. **서버가 소뇌** — 탐색/필터링/분석은 MCP 서버가 수행. LLM은 최종 판단과 콘텐츠 생성만
4. **반환값 압축** — 게시글 20개를 통째로 던지지 않음. 서버가 점수화해서 상위 N개 요약만 반환

## MCP Tools

| Tool | 역할 | 서버 내부에서 하는 일 |
|------|------|----------------------|
| **`gwanjong_setup`** | 온보딩 | 플랫폼 설정 상태 확인 → API 키 발급 안내 → 키 저장 + 연결 테스트 |
| **`gwanjong_scout`** | 정찰 | trending + search + 분석 + 기회 선별 → 상위 N개 압축 반환 |
| **`gwanjong_draft`** | 초안 준비 | 대상 게시글 + 댓글 트리 + 분위기 분석 → 맥락 요약 반환 |
| **`gwanjong_strike`** | 실행 | 게시글/댓글/교차게시 작성 → 결과 URL 반환 |
| **`gwanjong_status`** | 상태 조회 | 플랫폼 연결상태 + rate limit + 최근 활동 이력 |

### 온보딩 예시

```
사용자: "Reddit 연결해줘"

[1] setup(action="check")
    → {"configured": ["devto"], "not_configured": ["bluesky", "twitter", "reddit"]}

[2] setup(action="guide", platform="reddit")
    → 발급 절차 안내 (URL, 단계별 설명, 필요한 키 목록)
    → LLM이 사용자에게 안내 전달

[3] 사용자가 키를 복사해서 전달

[4] setup(action="save", platform="reddit", credentials={...})
    → .env에 저장 + 연결 테스트 → "Reddit 연결 성공"
```

### 파이프라인 예시

```
사용자: "ku-portal-mcp를 Reddit에서 자연스럽게 알려줘"

[1] scout(topic="MCP server university portal", platforms=["reddit"])
    → 서버가 r/MCP, r/ClaudeAI 등 탐색 → 점수화 → 상위 3개 기회 반환
    {
      "opportunities": [
        {"id": "opp_1", "platform": "reddit", "type": "comment",
         "title": "Best MCP servers for productivity?",
         "relevance": 0.91, "comments": 42,
         "reason": "MCP 추천 스레드, 활발한 토론 중"}
      ],
      "summary": "reddit 2건, 가장 유망: r/MCP 추천 스레드 (42 comments)"
    }

[2] draft(opportunity_id="opp_1")
    → 서버가 게시글 본문 + 상위 댓글 + 분위기 수집
    {
      "post": {"title": "...", "body_summary": "...", "tone": "technical"},
      "top_comments": ["...", "...", "..."],
      "suggested_approach": "comment",
      "avoid": ["직접 링크 - 서브레딧 규칙상 셀프 홍보 주의"]
    }
    → LLM이 이 맥락으로 자연스러운 댓글 생성 → 사용자에게 미리보기

[3] strike(opportunity_id="opp_1", action="comment", content="...")
    → 서버가 Reddit API로 댓글 작성
    {"url": "https://reddit.com/r/MCP/comments/...", "status": "posted"}
```

## 지원 플랫폼

| 플랫폼 | 프로토콜 | 인증 |
|--------|----------|------|
| **Dev.to** | REST API (httpx) | API Key |
| **Bluesky** | AT Protocol | App Password |
| **Twitter/X** | OAuth 1.0a (tweepy) | API Key + Token |
| **Reddit** | OAuth2 (asyncpraw) | Client ID + Secret |

API key가 설정된 플랫폼만 자동 활성화. 나머지는 무시.

## 설치

```bash
# 전체 플랫폼
pip install "gwanjong-mcp[all]"

# 특정 플랫폼만
pip install "gwanjong-mcp[devto]"
pip install "gwanjong-mcp[bluesky]"
pip install "gwanjong-mcp[twitter]"
pip install "gwanjong-mcp[reddit]"

# 개발용
git clone https://github.com/SonAIengine/gwanjong-mcp.git
cd gwanjong-mcp
pip install -e ".[all,dev]"
```

## 환경 변수

`.env.example`을 `.env`로 복사 후 사용할 플랫폼 키만 입력:

```env
# Dev.to — https://dev.to/settings/extensions
DEVTO_API_KEY=

# Bluesky — https://bsky.app/settings → App Passwords
BLUESKY_HANDLE=your.handle.bsky.social
BLUESKY_APP_PASSWORD=

# Twitter/X — https://developer.x.com/en/portal/dashboard
TWITTER_API_KEY=
TWITTER_API_SECRET=
TWITTER_ACCESS_TOKEN=
TWITTER_ACCESS_SECRET=

# Reddit — https://www.reddit.com/prefs/apps
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USERNAME=
REDDIT_PASSWORD=
```

## Claude Code 연동

```bash
# MCP 서버 등록
claude mcp add gwanjong-mcp -- gwanjong-mcp

# 관종 에이전트와 사용 (~/.claude/agents/gwanjong.md 필요)
claude agent gwanjong
> "ku-portal-mcp를 Reddit에서 자연스럽게 홍보해줘"
```

## 아키텍처

```
gwanjong-mcp/
├── pyproject.toml
├── run.py                         # 직접 실행 진입점
└── gwanjong_mcp/
    ├── __init__.py
    ├── __main__.py                # python -m gwanjong_mcp
    ├── server.py                  # PipelineMCP + 5개 tool 등록
    ├── setup.py                   # 플랫폼 온보딩 (안내/저장/테스트)
    └── pipeline.py                # scout/draft/strike 파이프라인 로직
```

### 의존 구조

gwanjong-mcp는 자체 코드를 최소화하고, 범용 라이브러리를 조합합니다:

```
┌─────────────────────────────────────────────────┐
│  Claude Agent (gwanjong.md)                     │
│  페르소나 · 콘텐츠 생성 · 최종 판단              │
└──────────────┬──────────────────────────────────┘
               │ 5 tools
┌──────────────▼──────────────────────────────────┐
│  gwanjong-mcp (이 프로젝트)                      │
│  scout/draft/strike 파이프라인 로직               │
│                                                  │
│  의존:                                           │
│  ├── mcp-pipeline  — Stateful MCP 프레임워크     │
│  ├── devhub        — 멀티 플랫폼 소셜 클라이언트 │
│  └── graph-tool-call — 콘텐츠 그래프 검색 엔진   │
└──────────────────────────────────────────────────┘
```

| 패키지 | 역할 | 링크 |
|--------|------|------|
| [devhub](https://github.com/SonAIengine/devhub) | Dev.to, Bluesky, Twitter, Reddit 통합 API 클라이언트 | `pip install devhub[all]` |
| [mcp-pipeline](https://github.com/SonAIengine/mcp-pipeline) | 타입 안전 상태 관리 + stores/requires 선언적 tool 체이닝 | `pip install mcp-pipeline` |
| [graph-tool-call](https://github.com/SonAIengine/graph-tool-call) | BM25 + 그래프 확장 + wRRF 기반 콘텐츠 검색/점수화 | `pip install graph-tool-call` |

## 개발

```bash
pytest                    # 테스트
mypy gwanjong_mcp/        # 타입 체크
ruff check gwanjong_mcp/  # 린트
python run.py             # 로컬 서버 실행
```

## 라이선스

MIT — [LICENSE](LICENSE)
