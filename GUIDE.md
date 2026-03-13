# gwanjong Agent Guide

## TL;DR

**"Don't promote. Help people, and they'll find you."**

gwanjong is not a spam bot. It's a tool for providing genuine value in developer communities while naturally increasing your project and profile visibility.

---

## Getting Started

### 1. Platform Setup

In Claude Code:
```
> gwanjong_setup action="check"
```
If a platform isn't configured:
```
> gwanjong_setup action="guide" platform="devto"
```
Follow the guide to get an API key, then:
```
> gwanjong_setup action="save" platform="devto" credentials={"DEVTO_API_KEY": "..."}
```

### 2. Basic Workflow

```
You: "Engage with MCP-related posts"

Agent:
1. scout("MCP server", platforms=["devto", "twitter"]) → find opportunities
2. draft("opp_0") → analyze context
3. Draft a comment → ask for your approval
4. strike("opp_0", action="comment", content="...") → post it
```

In autonomous mode, you can use the approval queue:

```bash
gwanjong-daemon --require-approval --max-cycles 1
gwanjong-approval list
gwanjong-approval show 1
gwanjong-approval approve 1
gwanjong-approval reject 2
```

Run the dashboard (`gwanjong-dashboard`) to review and approve/reject pending items from a web UI.

---

## Engagement Strategies

### Strategy 1: Answering Questions (Most Effective)

When someone asks "recommend an MCP server" or "how do I automate a university portal", **answer genuinely** and mention your project naturally.

```
Scout keywords:
- "MCP server recommendation"
- "university portal automation"
- "developer productivity tools"
- "social media API integration"
```

Good comment:
> "I had a similar need, so I built an MCP server for it.
> I wrapped a university portal in an MCP server so Claude Code can handle
> enrollment, grades, etc. via natural language.
> If you're interested, it's on GitHub: (link)"

Bad comment:
> "Check out my project ku-portal-mcp! It's the best! Please star it! ⭐"

### Strategy 2: Sharing Technical Insights

Leave **experience-driven technical opinions** on trending posts.
No link needed — your GitHub is on your profile. Interested people will find it.

```
Scout keywords:
- "LLM tool calling"
- "async Python"
- "MCP protocol"
- "developer community"
```

Good comment:
> "From building MCP servers, I learned that minimizing the number of tools is critical.
> Tool descriptions are included in every system prompt.
> I went from 14 tools → 5 using a stores/requires state chaining pattern,
> which cut round trips from 9 → 3."

### Strategy 3: Cross-Posting Blog Articles

Cross-post technical blog articles to Dev.to.
Set `canonical_url` to avoid SEO duplication.

```
Use strike action="post":
- Dev.to: long-form technical articles (markdown)
- Twitter: one-liner + blog link
- Bluesky: summary + blog link
```

### Strategy 4: Weekly Routine

| Day | Activity | Platform |
|-----|----------|----------|
| Mon | Check trending + reply to 2-3 posts | Dev.to |
| Wed | Join technical threads | Twitter, Bluesky |
| Fri | Cross-post blog article | Dev.to → Twitter → Bluesky |

---

## Platform Tone & Rules

### Dev.to
- **Tone**: Friendly but technical. Use code blocks often.
- **Length**: Comments 3-5 sentences. Articles unlimited.
- **Tip**: Good tags (#python, #mcp, #ai) maximize search visibility.
- **Don't**: Clickbait titles, empty promotional posts.

### Twitter/X
- **Tone**: Concise and impactful. 280 chars.
- **Length**: 1-2 sentences + link, or threads.
- **Tip**: 2-3 hashtags. Quote RT to add your take.
- **Don't**: Repeat the same link, mass-mention people.

### Bluesky
- **Tone**: More casual than Twitter. Dev community vibe.
- **Length**: 300 char limit. Keep it focused.
- **Tip**: Early-stage platform = good opportunity to build a following.
- **Don't**: Aggressive promotion. Small community = you'll get noticed fast.

### Reddit
- **Tone**: Varies by subreddit. Read the rules first.
- **Length**: Be thorough. Low-effort comments get downvoted.
- **Tip**: r/MCP, r/ClaudeAI, r/Python, r/MachineLearning, etc.
- **Don't**: Self-promo should be <10% of activity (Reddit official policy). No link-only posts.

---

## Projects to Promote

Loaded from `~/.gwanjong/profile.json`. Manages project list, personal brand info, and target keywords.

```json
{
  "name": "...",
  "github": "...",
  "blog": "...",
  "projects": [
    {"name": "my-project", "description": "...", "keywords": ["..."]}
  ]
}
```

---

## Measuring Results

Track the outcomes of agent activity:

- **Follower growth**: GitHub stars, Dev.to followers, Twitter followers
- **Traffic**: Blog visitors (Cloudflare Analytics)
- **Engagement**: Comment reactions (likes, replies)
- **Conversion**: GitHub repo clones/forks

---

## Safety Rules

1. **Daily activity limits** — Max 3 comments, 1 post per platform per day. More than that is spam.
2. **No copy-paste** — Posting the same comment on multiple posts will get you reported.
3. **Avoid negative threads** — Don't enter flame wars. If draft's tone is negative, skip it.
4. **No fake accounts** — Only use one real account per platform.
5. **Approve before posting** — In autonomous mode, always review via approval queue or dashboard before publishing.
