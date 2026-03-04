# Coordinator Guidance: When to Delegate to Design Agents

This guidance helps the coordinator recognize design work and delegate appropriately.

## Recognition Patterns

### Direct Design Mentions
- User says: "design", "UI", "UX", "interface", "visual", "aesthetic"
- User describes: components, layouts, pages, screens, forms, navigation
- User asks: "what should this look like?", "how should this feel?", "what's the best layout?"

### Implicit Design Work
- Building user-facing features
- Creating component libraries or design systems
- Improving user experience
- Brand or visual identity work
- Content strategy and messaging

### Design System Work
- Creating or refining design tokens
- Building component libraries
- Establishing design patterns
- Documenting design standards

## Delegation Matrix

| User Intent | Delegate To | Why |
|-------------|-------------|-----|
| "What aesthetic direction fits?" | `design-intelligence:art-director` | Visual strategy and brand personality |
| "Design a component library" | `design-intelligence:design-system-architect` | System-wide patterns and token architecture |
| "Build this button component" | `design-intelligence:component-designer` | Individual component design and variants |
| "Layout this landing page" | `design-intelligence:layout-architect` | Page structure and information architecture |
| "How should this animate?" | `design-intelligence:animation-choreographer` | Motion design and transitions |
| "Make this responsive" | `design-intelligence:responsive-strategist` | Breakpoint strategy and device adaptation |
| "Write the microcopy" | `design-intelligence:voice-strategist` | Tone, messaging, and content personality |
| "Find design inspiration" | `design-intelligence:research-runner` | Trend research and site analysis |
| "Generate design tokens" | `design-intelligence:token-generator` | Token creation (colors, typography, spacing) |
| "Create component specs" | `design-intelligence:spec-writer` | Component specifications for implementation |

## Multi-Agent Patterns

### Pattern 1: System to Component to Implementation
```
User: "Build a design system for my healthcare app"

1. design-intelligence:art-director
   -> Aesthetic direction, brand personality

2. design-intelligence:design-system-architect
   -> Token architecture, system patterns

3. design-intelligence:component-designer
   -> Individual component designs

4. design-intelligence:token-generator
   -> Generate actual tokens

5. foundation:modular-builder
   -> Implement the design system in code
```

### Pattern 2: Research to Design to Generate
```
User: "Design a modern landing page"

1. design-intelligence:research-runner
   -> Current trends in landing page design

2. design-intelligence:layout-architect
   -> Page structure and information architecture

3. design-intelligence:component-designer
   -> Hero, CTA, feature cards design

4. design-intelligence:spec-writer
   -> Component specifications

5. foundation:modular-builder
   -> Implementation
```

### Pattern 3: Parallel Perspective Gathering
```
User: "I need design direction for my fintech app"

Parallel delegation:
- design-intelligence:art-director (aesthetic strategy)
- design-intelligence:voice-strategist (tone and messaging)
- design-intelligence:responsive-strategist (device strategy)

Then synthesize their perspectives into cohesive direction.
```

## Natural Language Recognition

### High Confidence Design Work
- "design a [thing]"
- "how should [UI element] look/feel?"
- "what's the best layout for [context]?"
- "I need design help with [feature]"
- "make this [adjective]" (beautiful, modern, professional, etc.)

### Medium Confidence (Clarify First)
- "build a [user-facing feature]" - Could be design or implementation
- "improve [existing feature]" - Could be UX, performance, or code
- "create [component]" - Clarify if design or just implementation

**Response pattern:**
```
"I can help you [build that feature]. Would you like me to:
1. Start with design direction (aesthetics, layout, UX)
2. Go straight to implementation with sensible defaults
3. Both - design first, then implement"
```

### Low Confidence (Don't Assume)
- "fix this bug" - Implementation/debugging, not design
- "refactor this code" - Code architecture, not UI design
- "write tests" - Testing, not design

## Proactive Design Delegation

Don't wait for explicit design requests. Proactively delegate when:

1. **User describes aesthetic goals**
   - "I want this to feel professional"
   - "It should be modern and clean"
   - Delegate to art-director for aesthetic strategy

2. **User mentions user experience**
   - "Users need to find this quickly"
   - "This should be intuitive"
   - Delegate to layout-architect or component-designer

3. **User describes visual elements**
   - "A hero section with a CTA"
   - "Cards displaying features"
   - Delegate to component-designer

4. **User is building user-facing features**
   - Even if they don't mention design
   - Offer design guidance as an option

## Context Handoff to Design Agents

When delegating, provide:

1. **User's Vision** - Their aesthetic goals, preferences, vibes
2. **Constraints** - Brand guidelines, accessibility needs, technical limits
3. **Context** - What this is for, who will use it, why it matters
4. **Prior Work** - What other agents have already designed/decided

## The Golden Rule

**When in doubt, ask if design guidance would be helpful.**

Users may not realize design intelligence is available. Offering it proactively improves final product quality, reduces iteration cycles, and builds trust in the system's capabilities.
