# 관종 에이전트 활용 가이드

## 한 줄 요약

**"홍보하지 마라. 도와주다 보면 알려진다."**

관종 에이전트는 스팸봇이 아니다. 개발자 커뮤니티에서 진짜 가치를 제공하면서,
자연스럽게 내 프로젝트와 프로필이 노출되도록 하는 도구다.

---

## 시작하기

### 1. 플랫폼 설정

Claude Code에서:
```
> gwanjong_setup action="check"
```
설정 안 된 플랫폼이 있으면:
```
> gwanjong_setup action="guide" platform="devto"
```
안내에 따라 API 키 발급 후:
```
> gwanjong_setup action="save" platform="devto" credentials={"DEVTO_API_KEY": "..."}
```

### 2. 기본 워크플로우

```
나: "MCP 관련 글에서 활동해줘"

에이전트:
1. scout("MCP server", platforms=["devto", "twitter"]) → 기회 발견
2. draft("opp_0") → 맥락 분석
3. 댓글 초안 작성 → 사용자 승인 요청
4. strike("opp_0", action="comment", content="...") → 게시
```

---

## 활동 전략

### 전략 1: 질문에 답변하기 (가장 효과적)

누군가 "MCP 서버 추천해주세요", "대학교 포털 자동화 어떻게 하나요" 같은 질문을 올리면
**진심으로 답변하면서** 내 프로젝트를 자연스럽게 언급한다.

```
scout 키워드 예시:
- "MCP server recommendation"
- "university portal automation"
- "developer productivity tools"
- "social media API integration"
```

좋은 댓글:
> "저도 비슷한 니즈가 있어서 ku-portal-mcp를 만들었습니다.
> 건국대 포털을 MCP 서버로 감싸서 Claude Code에서 수강신청, 성적 조회 등을
> 자연어로 할 수 있게 했어요. AT Protocol 기반이라 확장도 쉽습니다.
> 혹시 관심 있으시면 GitHub에 올려뒀습니다: (링크)"

나쁜 댓글:
> "제 프로젝트 ku-portal-mcp를 사용해보세요! 최고입니다! ⭐ 부탁드립니다!"

### 전략 2: 기술 인사이트 공유

트렌딩 게시글에 **내 경험에서 우러나온 기술적 의견**을 남긴다.
링크는 넣지 않아도 된다 — 프로필에 GitHub이 있으니 관심 있는 사람은 찾아온다.

```
scout 키워드 예시:
- "LLM tool calling"
- "async Python"
- "MCP protocol"
- "developer community"
```

좋은 댓글:
> "MCP 서버 개발하면서 느낀 건데, tool 개수를 최소화하는 게 정말 중요합니다.
> tool description이 매 호출마다 시스템 프롬프트에 포함되거든요.
> 저는 14개 → 5개로 줄이고 stores/requires 패턴으로 상태 체이닝했더니
> 라운드트립이 9번 → 3번으로 줄었습니다."

### 전략 3: 블로그 글 교차 게시

sonblog에 쓴 기술 글을 Dev.to에 교차 게시한다.
canonical_url을 설정해서 SEO 중복을 피한다.

```
strike action="post" 사용
- Dev.to: 긴 기술 글 (마크다운 지원)
- Twitter: 핵심 한 줄 + 블로그 링크
- Bluesky: 요약 + 블로그 링크
```

### 전략 4: 주간 루틴

| 요일 | 활동 | 플랫폼 |
|------|------|--------|
| 월 | 트렌딩 체크 + 답변 2-3개 | Dev.to |
| 수 | 기술 스레드 참여 | Twitter, Bluesky |
| 금 | 블로그 글 교차 게시 | Dev.to → Twitter → Bluesky |

---

## 플랫폼별 톤 & 규칙

### Dev.to
- **톤**: 친근하지만 기술적. 코드 블록 적극 활용.
- **길이**: 댓글 3-5문장. 글은 제한 없음.
- **팁**: 태그(#python, #mcp, #ai)를 잘 걸면 검색 노출 극대화.
- **금지**: 제목 낚시, 내용 없는 홍보 글.

### Twitter/X
- **톤**: 간결하고 임팩트 있게. 280자.
- **길이**: 1-2문장 + 링크 또는 스레드.
- **팁**: 해시태그 2-3개. 인용 RT로 의견 추가.
- **금지**: 같은 링크 반복 트윗, 무차별 멘션.

### Bluesky
- **톤**: Twitter보다 캐주얼. 개발자 커뮤니티 분위기.
- **길이**: 300자 제한. 핵심만.
- **팁**: 초기 플랫폼이라 팔로워 확보 기회 큼.
- **금지**: 공격적 홍보. 커뮤니티가 작아서 빠르게 눈에 띈다.

### Reddit
- **톤**: 서브레딧마다 다름. 룰 먼저 확인.
- **길이**: 충분히 상세하게. 저퀄 댓글은 다운보트.
- **팁**: r/MCP, r/ClaudeAI, r/Python, r/MachineLearning 등.
- **금지**: 셀프 홍보 비율 10% 이하 권장 (Reddit 공식 정책). 링크만 던지기 금지.

---

## 홍보할 프로젝트

### 메인 프로젝트
| 프로젝트 | 한 줄 소개 | 대상 커뮤니티 |
|----------|-----------|--------------|
| **ku-portal-mcp** | 건국대 포털을 MCP 서버로 — Claude에서 수강/성적/도서관 | r/MCP, MCP 관련 글 |
| **devhub** | Dev.to/Bluesky/Twitter/Reddit 통합 async 클라이언트 | Python/API 관련 글 |
| **mcp-pipeline** | Stateful MCP 프레임워크 (stores/requires 체이닝) | MCP 개발자 |
| **gwanjong-mcp** | AI 소셜 에이전트 MCP 서버 | AI 에이전트 관련 글 |

### 개인 브랜드
| 항목 | 내용 |
|------|------|
| 이름 | Son Seong Joon |
| GitHub | https://github.com/SonAIengine |
| 블로그 | https://sonblog.pages.dev |
| 포지셔닝 | AI/LLM 인프라 엔지니어, MCP 생태계 기여자 |
| 전문 분야 | MCP 서버 개발, 멀티 플랫폼 API 통합, K8s/MSA 인프라 |

---

## 성과 측정

에이전트가 활동한 결과를 추적:

- **팔로워 증가**: GitHub stars, Dev.to followers, Twitter followers
- **트래픽**: 블로그 방문자 (Cloudflare Analytics)
- **참여**: 댓글 반응 (좋아요, 답글)
- **전환**: GitHub repo clone/fork 수

---

## 주의사항

1. **하루 활동량 제한** — 플랫폼당 댓글 3개, 글 1개 이하. 그 이상은 스팸.
2. **동일 내용 복붙 금지** — 같은 댓글을 여러 글에 붙이면 바로 신고당함.
3. **부정적 토론 회피** — 불꽃 논쟁에 끼어들지 않음. draft의 tone이 negative면 스킵.
4. **가짜 계정 금지** — 하나의 진짜 계정으로만 활동.
5. **strike 전 반드시 승인** — 에이전트가 쓴 내용을 사용자가 확인 후 게시.
