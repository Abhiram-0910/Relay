# SKILLS.md — Global Skills Reference

All skills below are available globally via ~/.claude/skills/
Activate by mentioning the skill name or trigger phrase in your prompt.

---

## UI/UX Skills (auto-activates on UI requests)

### ui-ux-pro-max
**Trigger:** any UI/UX task — "build", "design", "create", "implement", "review", "fix" UI
**What it does:** 50+ styles, 161 color palettes, 57 font pairings, 161 product types, 99 UX guidelines
**Stacks:** React, Next.js, Vue, Tailwind, shadcn, Flutter, SwiftUI, React Native, Angular, Laravel
**Commands:** just describe what you want naturally

### design-system
**Trigger:** "create a design system", "consistent styling", "design tokens"
**What it does:** Generates complete design systems with tokens, colors, typography

### ui-styling
**Trigger:** "style this component", "improve the CSS", "make this look better"

### brand
**Trigger:** "brand identity", "logo guidelines", "brand colors"

### slides
**Trigger:** "create a presentation", "make slides"

---

## Code Quality Skills

### Ponytail (plugin — always active)
**Trigger:** automatic on every session
**Commands:**
- `/ponytail` — review current task for over-engineering
- `/ponytail-review` — review a diff/PR for complexity
- `/ponytail-audit` — whole-repo over-engineering scan
- `/ponytail-debt` — list all `ponytail:` deferred items
**Philosophy:** Delete > Reuse > Stdlib > Library > Write new

---

## Recommended Workflow Skills (add to project CLAUDE.md)

### grill-me
**What it does:** Before starting a task, Claude asks hard clarifying questions to expose ambiguity
**How to activate:** Add to project CLAUDE.md: "Use grill-me before starting any new feature"
**When to use:** Complex features, ambiguous requirements, new projects

### TDD (Test-Driven Development)
**What it does:** Write failing test first, then minimum code to pass, then refactor
**How to activate:** Add to project CLAUDE.md: "Always follow TDD — write tests before implementation"
**When to use:** Any feature that needs reliability

### improve-codebase-architecture
**What it does:** Analyzes existing code and proposes structural improvements
**How to activate:** "Review and improve the architecture of this codebase"
**When to use:** After MVP, before scaling, legacy code cleanup

### session-handoff
**What it does:** Structured start/end of session protocol
**Start:** Read AGENTS.md + TODO.md, summarize current state, confirm task scope
**End:** Update AGENTS.md with what changed, update TODO.md with remaining work

---

## Magic MCP (UI Component Generation)

**Trigger:** `/ui <description>` in Claude Code
**Examples:**
- `/ui dark hero section with glassmorphism cards and gradient text orange to amber`
- `/ui sticky navbar blur background scroll, dark theme, CTA button right`
- `/ui pricing table 3 columns, middle highlighted, gradient border on recommended`
- `/ui 3 feature cards glow border hover, glassmorphism, icons and descriptions`

**Pattern for best results:** style + effect + color direction + theme
Always specify dark/light — never leave it unspecified.

---

## rtk Commands Reference (Token Savings)

```bash
# Files
rtk read file.py              # smart file reading
rtk read file.py -l aggressive # signatures only
rtk grep "pattern" .          # grouped search
rtk ls .                      # compact directory tree
rtk find "*.py" .             # compact find
rtk diff file1 file2          # condensed diff

# Git
rtk git status
rtk git log -n 10
rtk git diff
rtk git add / commit / push / pull

# Tests
rtk pytest / rtk go test / rtk cargo test
rtk err <command>             # errors only from any command

# Stats
rtk gain                      # see token savings for session
```
