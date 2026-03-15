<div align="center">

# gwanjong-mcp

**AI Growth OS — 캠페인 기반 소셜 에이전트 MCP 서버**

개발자 커뮤니티에서 진정성 있는 존재감을 구축하고, 캠페인 단위로 성과를 측정하세요.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Test](https://github.com/SonAIengine/gwanjong-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/SonAIengine/gwanjong-mcp/actions/workflows/test.yml)
[![Lint](https://github.com/SonAIengine/gwanjong-mcp/actions/workflows/lint.yml/badge.svg)](https://github.com/SonAIengine/gwanjong-mcp/actions/workflows/lint.yml)
[![PyPI](https://img.shields.io/pypi/v/gwanjong-mcp)](https://pypi.org/project/gwanjong-mcp/)

[English](README.md) · **한국어**

</div>

---

## 빠른 시작

```bash
# 1. 설치
pip install "gwanjong-mcp[all]"

# 2. 플랫폼 API 키 설정 (최소 1개)
mkdir -p ~/.gwanjong
cat > ~/.gwanjong/.env << 'EOF'
DEVTO_API_KEY=your_key_here
EOF

# 3. MCP 서버 실행
gwanjong-mcp
```

**Claude Code 연동:**

```bash
claude mcp add gwanjong-mcp -- gwanjong-mcp
claude
> "MCP 관련 토론을 찾아서 도움이 되는 댓글을 남겨줘"
```

**자율 모드 (LLM 클라이언트 불필요):**

```bash
pip install "gwanjong-mcp[all,autonomous]"
gwanjong-daemon --topics "MCP,LLM" --dry-run --max-cycles 1

# 캠페인 연동
gwanjong-daemon --topics "MCP,LLM" --campaign camp_001 --auto-plan
```

전체 설정 옵션은 [`.env.example`](.env.example) 참조.

---

## 왜 만들었나

일반적인 MCP 서버는 CRUD tool을 나열하고 LLM이 전부 오케스트레이션한다. 댓글 하나 남기는 데 9번 이상의 tool 호출, 매번 전체 tool description 재전송.

```
기존 MCP (14 tools, 9+ 왕복):
LLM → list → LLM → trending → LLM → search → LLM → analyze → LLM → get_post
→ LLM → get_comments → LLM → preview → LLM → write → LLM

gwanjong-mcp (8 tools, 3 왕복):
LLM → scout → LLM → draft → LLM (콘텐츠 생성) → strike → 완료
```

### 설계 원칙

1. **최소 Tool** — 8개. Tool description은 매 호출마다 시스템 프롬프트에 포함되므로 적을수록 저렴하고 정확함
2. **서버 상태 유지** — scout 결과를 서버에 캐시. LLM이 데이터를 중계하지 않음
3. **서버가 분석** — 검색, 필터링, 점수화, 분석은 서버 내부. LLM은 판단과 생성만
4. **캠페인 기반** — 모든 활동을 캠페인 단위로 추적, UTM 태깅, 전환 측정

## 철학

**두 가지 모드, 하나의 목표: 개발자 커뮤니티에서 진정성 있는 존재감 구축.**

| 모드 | 행동 | 목표 |
|------|------|------|
| **댓글** | 남의 글에 답변 | 도움이 되는 참여로 신뢰 쌓기. **셀프 프로모션 금지.** |
| **포스트** | 원본 콘텐츠 발행 | 프로젝트 소개, 기술 글, 공지. 홍보는 여기서만. |

---

## MCP Tools (8개)

| Tool | 역할 | 내부 동작 |
|------|------|-----------|
| **`gwanjong_setup`** | 온보딩 | 플랫폼 상태 확인 → API 키 설정 안내 → 저장 + 연결 테스트 |
| **`gwanjong_campaign`** | 캠페인 관리 | 생성/조회/수정/리포트 — 모든 활동의 기준점 |
| **`gwanjong_scout`** | 정찰 | 트렌딩 + 검색 + 점수화 → 상위 N개 기회 반환 |
| **`gwanjong_draft`** | 맥락 수집 | 대상 게시글 + 댓글 트리 + 분위기 분석 → 맥락 요약 |
| **`gwanjong_strike`** | 실행 | 댓글/글/upvote 발행 → UTM 자동 태깅 + 결과 URL |
| **`gwanjong_assets`** | 에셋 관리 | 콘텐츠 저장/검색/재사용 — usage 추적 |
| **`gwanjong_schedule`** | 예약 발행 | 콘텐츠 캘린더 — 시간 도래 시 자동 실행 |
| **`_status`** | 상태 조회 | state 필드 + 사용 가능/차단된 tool (자동 생성) |

### 파이프라인 흐름

```
scout(topic, platforms)
  │  stores → opportunities
  │  서버 내부: 트렌딩 + 검색 + 점수화 + 필터링
  │  반환: 상위 N개 기회 (~200 토큰)
  ▼
draft(opportunity_id)
  │  requires → opportunities
  │  stores → contexts
  │  서버 내부: 게시글 + 댓글 + 분위기 분석
  │  반환: 맥락 요약 + 추천 접근법 (~300 토큰)
  ▼
  LLM이 맥락 기반으로 콘텐츠 생성
  ▼
strike(opportunity_id, action, content)
     requires → contexts
     서버 내부: 플랫폼 API 호출 + UTM 태깅 + 이력 기록
     반환: { url, status }
```

### 캠페인 워크플로우

```
1. campaign create  → 캠페인 생성 (목표, 토픽, 플랫폼, KPI)
2. scout + draft + strike  → 캠페인 ID 연동하여 활동
3. campaign report  → KPI 달성률, 전환, 플랫폼별 성과 확인
4. schedule add  → 예약 발행 등록
5. assets save  → 효과 좋은 콘텐츠 저장 → 재사용
```

---

## 지원 플랫폼

| 플랫폼 | 프로토콜 | 인증 |
|--------|----------|------|
| **Dev.to** | REST API (httpx) | API Key |
| **Bluesky** | AT Protocol | App Password |
| **Twitter/X** | OAuth 1.0a (tweepy) | API Key + Token |
| **Reddit** | OAuth2 (asyncpraw) | Client ID + Secret |
| **GitHub Discussions** | GraphQL (httpx) | Personal Access Token |
| **Discourse** | REST API (httpx) | API Key |

설정된 플랫폼만 활성화됨. 미설정 플랫폼은 자동 건너뜀.

---

## 아키텍처

```
MCP Tools (8개)
├── gwanjong_setup       (온보딩)
├── gwanjong_campaign    (캠페인 CRUD + 리포트)
├── gwanjong_scout       (정찰, campaign_id 연동)
├── gwanjong_draft       (초안)
├── gwanjong_strike      (실행, UTM 자동 태깅)
├── gwanjong_assets      (에셋 저장/검색/재사용)
├── gwanjong_schedule    (예약 발행/캘린더)
└── _status              (자동)

EventBus 플러그인 (느슨한 결합)
├── safety.py       — Rate limiting + 콘텐츠 검증
├── memory.py       — 활동 이력 + 중복 방지
├── tracker.py      — 답글 추적
├── conversion.py   — UTM 삽입 + 전환 기록
└── scheduler.py    — 예약 시간 도래 → 실행

독립 모듈
├── campaign.py     — Campaign CRUD
├── asset.py        — Asset library
├── message.py      — ICP별 메시지 프레임
├── measure.py      — Attribution + A/B 실험
└── strategy.py     — 주간 플랜 자동 생성
```

### 의존성 구조

```
┌─────────────────────────────────────────────────┐
│  Claude Agent                                    │
│  페르소나 · 콘텐츠 생성 · 최종 판단              │
└──────────────┬──────────────────────────────────┘
               │ 8 tools
┌──────────────▼──────────────────────────────────┐
│  gwanjong-mcp (이 프로젝트)                      │
│  scout/draft/strike 파이프라인 + Growth OS        │
│                                                  │
│  의존성:                                          │
│  ├── mcp-pipeline  — Stateful MCP 프레임워크      │
│  ├── devhub-social — 멀티 플랫폼 소셜 API         │
│  └── anthropic     — 자율 모드 LLM 생성           │
└──────────────────────────────────────────────────┘
```

| 패키지 | 역할 | 설치 |
|--------|------|------|
| [devhub-social](https://github.com/SonAIengine/devhub-social) | 6개 플랫폼 통합 async 클라이언트 | `pip install devhub-social[all]` |
| [mcp-pipeline](https://github.com/SonAIengine/mcp-pipeline) | 타입 안전 상태 + 선언적 stores/requires 체이닝 | `pip install mcp-pipeline` |

---

## 실행 모드

### MCP 모드 (기본)

LLM 클라이언트가 tool을 호출하는 표준 MCP 서버.

```bash
gwanjong-mcp
```

### 자율 데몬 모드

자체 사이클로 scout→draft→generate→strike 반복 실행.

```bash
# 기본
gwanjong-daemon --topics "MCP,LLM" --interval 4 --max-actions 3

# 옵션
gwanjong-daemon --require-approval    # strike 전 승인 필요
gwanjong-daemon --dry-run             # scout+draft만, strike 안 함
gwanjong-daemon --model claude-haiku-4-5-20251001
gwanjong-daemon --platforms devto,bluesky
gwanjong-daemon --max-cycles 1        # 1회만 실행
gwanjong-daemon --campaign camp_001   # 캠페인 연동
gwanjong-daemon --auto-plan --campaign camp_001  # 주간 플랜 자동 생성
```

### 승인 워크플로우

자율 모드에서 발행 전 사람의 검토를 거칠 수 있음.

```bash
# 즉시 발행 대신 승인 큐에 등록
gwanjong-daemon --require-approval --max-cycles 1

# 대기 항목 확인
gwanjong-approval list
gwanjong-approval show 1

# 승인 후 즉시 실행
gwanjong-approval approve 1

# 거부
gwanjong-approval reject 2
```

### 모니터링 대시보드

```bash
gwanjong-dashboard --port 8585
# http://localhost:8585
```

플랫폼별 활동, rate limit, 답글, 승인 큐, 캠페인, 예약 발행 현황 확인.

---

## 안전장치

### Rate Limiter
플랫폼별 일일 제한 + 최소 간격 (30분) 자동 적용.

| 플랫폼 | 댓글/일 | 포스트/일 | 최소 간격 |
|--------|---------|-----------|-----------|
| Dev.to | 3 | 1 | 30분 |
| Bluesky | 5 | 2 | 30분 |
| Twitter | 5 | 2 | 30분 |
| Reddit | 3 | 0 (금지) | 30분 |
| GitHub Discussions | 4 | 1 | 45분 |
| Discourse | 4 | 1 | 45분 |

### Content Guard
AI가 흔히 쓰는 패턴을 자동 감지하여 차단:
- AI 단어: "fascinating", "insightful", "game-changer" 등
- 공식적 구조: 칭찬 → 경험 → 질문
- 과도한 URL (자기 홍보 의심)

---

## 설치

```bash
# 전체 플랫폼
pip install "gwanjong-mcp[all]"

# 특정 플랫폼만
pip install "gwanjong-mcp[devto]"
pip install "gwanjong-mcp[bluesky]"
pip install "gwanjong-mcp[twitter]"
pip install "gwanjong-mcp[reddit]"

# 자율 모드 + 대시보드 포함
pip install "gwanjong-mcp[all,autonomous,dashboard]"

# 개발 환경
git clone https://github.com/SonAIengine/gwanjong-mcp.git
cd gwanjong-mcp
pip install -e ".[all,dev]"
```

## 환경 변수

`.env.example`을 `~/.gwanjong/.env`로 복사 후 사용할 플랫폼 키를 입력:

```env
# Dev.to — https://dev.to/settings/extensions
DEVTO_API_KEY=

# Bluesky — https://bsky.app/settings → App Passwords
BLUESKY_HANDLE=your.handle.bsky.social
BLUESKY_APP_PASSWORD=

# Twitter/X — https://developer.x.com/en/portal/dashboard
TWITTER_BEARER_TOKEN=

# Reddit — https://www.reddit.com/prefs/apps
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USERNAME=
REDDIT_PASSWORD=

# 자율 모드
ANTHROPIC_API_KEY=
```

## 개발

```bash
# 로컬 개발 (mcp-pipeline, devhub도 로컬 설치)
uv pip install -e ../mcp-pipeline -e "../devhub-social[all]" -e ".[all,dev]"

# 테스트
pytest tests/ -v

# Lint
ruff check gwanjong_mcp/
ruff format --check gwanjong_mcp/
```

## 라이선스

[MIT](LICENSE)
