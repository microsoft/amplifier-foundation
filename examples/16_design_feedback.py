#!/usr/bin/env python3
"""
Example 16: Design Feedback Assistant
=====================================

VALUE PROPOSITION:
Shows Amplifier works beyond code! Get AI-powered feedback on design decisions,
accessibility, and best practices. Perfect for designers and PMs who want
structured design review.

WHAT YOU'LL LEARN:
- Design domain expertise with AI
- Structured feedback generation
- Non-code use case for Amplifier
- Prompting patterns for design analysis
- Cross-functional AI applications

AUDIENCE:
Designers, PMs, and anyone working with UI/UX design

NOTE:
This example demonstrates the prompting pattern for design analysis.
For actual image analysis with vision models, the same pattern applies
with image input support.
"""

import asyncio
import os
from pathlib import Path

from amplifier_foundation import load_bundle


# =============================================================================
# SECTION 1: Design Analysis
# =============================================================================

async def analyze_design_from_description(design_description: str) -> str:
    """Analyze a design based on text description.
    
    Args:
        design_description: Text description of the design
        
    Returns:
        Structured feedback on the design
    """
    # Load foundation and provider
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))
    
    composed = foundation.compose(provider)
    
    print("⏳ Preparing session...")
    prepared = await composed.prepare()
    session = await prepared.create_session()
    
    # Create analysis prompt
    prompt = f"""Analyze this design and provide structured feedback:

{design_description}

Review for:

1. **Accessibility**
   - Color contrast ratios (WCAG standards: 4.5:1 for normal text, 3:1 for large text)
   - Font sizes (minimum 16px for body text, 44px touch targets)
   - Readability and legibility

2. **Visual Consistency**
   - Spacing patterns (should follow a consistent scale like 4px, 8px, 16px)
   - Alignment and grid usage
   - Typography consistency

3. **Best Practices**
   - Mobile-first considerations
   - Visual hierarchy
   - Call-to-action clarity
   - White space usage

4. **Specific Recommendations**
   - Provide actionable improvements
   - Explain the rationale
   - Prioritize fixes (high/medium/low)

Format your response with:
- ✓ for things done well
- ⚠️ for areas needing attention
- Clear, actionable recommendations"""

    print("🔍 Analyzing design...")
    
    async with session:
        response = await session.execute(prompt)
        return response


# =============================================================================
# SECTION 2: Sample Design Scenarios
# =============================================================================

SAMPLE_LOGIN_PAGE = """
Login Page Design:
- Background: White (#FFFFFF)
- Primary button: Blue (#2196F3) with white text (#FFFFFF)
- Button size: 32px height, 120px width
- Font sizes: 
  - Heading: 24px
  - Body text: 14px
  - Button text: 14px
- Input fields: 40px height, 14px font
- Spacing: 12px between elements
- Mobile viewport: 375px width
- Form positioned in center of screen
"""

SAMPLE_DASHBOARD = """
Dashboard Design:
- Header: Dark navy (#1A237E) with white text
- Cards: White background, 2px border, 8px border-radius
- Card spacing: 16px gaps in grid layout
- Typography:
  - H1: 32px, bold
  - H2: 24px, semibold  
  - Body: 16px, regular
  - Small text: 12px
- Action buttons: 44px height (touch-friendly)
- Color palette: Primary blue, secondary green, danger red
- Responsive: 3-column on desktop, 2-column on tablet, 1-column on mobile
"""


# =============================================================================
# SECTION 3: Interactive Mode
# =============================================================================

async def interactive_design_analysis():
    """Interactive mode for custom design descriptions."""
    
    print("\n" + "="*60)
    print("Interactive Design Analysis")
    print("="*60)
    print("\nDescribe your design (or press Ctrl+D to use sample):")
    print("-"*60)
    
    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass
    
    if not lines:
        print("\nUsing sample design...")
        design = SAMPLE_LOGIN_PAGE
    else:
        design = "\n".join(lines)
    
    # Analyze
    feedback = await analyze_design_from_description(design)
    
    print("\n" + "="*60)
    print("📊 Design Feedback")
    print("="*60)
    print(feedback)


# =============================================================================
# SECTION 4: Main Entry Point
# =============================================================================

async def main():
    """Main entry point."""
    
    print("🎨 Design Feedback Assistant")
    print("="*60)
    print("\nVALUE: AI-powered design review for accessibility and best practices")
    print("AUDIENCE: Designers, PMs, and anyone working with UI/UX")
    print("\nWhat this demonstrates:")
    print("  - Design domain expertise with AI")
    print("  - Structured feedback generation")
    print("  - Amplifier works beyond code!")
    print("  - Cross-functional AI applications")
    
    # Check prerequisites
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 16_design_feedback.py")
        return
    
    # Demo with sample design
    print("\n" + "="*60)
    print("Demo: Analyzing Sample Login Page")
    print("="*60)
    print("\nDesign specification:")
    print("-"*60)
    print(SAMPLE_LOGIN_PAGE)
    print("-"*60)
    
    # Analyze
    feedback = await analyze_design_from_description(SAMPLE_LOGIN_PAGE)
    
    print("\n" + "="*60)
    print("📊 Design Feedback")
    print("="*60)
    print(feedback)
    
    # Summary
    print("\n" + "="*60)
    print("📚 WHAT YOU LEARNED:")
    print("="*60)
    print("  ✓ AI can provide structured design feedback")
    print("  ✓ Analyze accessibility, consistency, best practices")
    print("  ✓ Get actionable recommendations with rationale")
    print("  ✓ Amplifier works for non-code domains")
    
    print("\n💡 VISION MODEL EXTENSION:")
    print("="*60)
    print("""
This example uses text descriptions. For actual screenshot analysis:

1. Use a vision-capable provider (Claude Sonnet/Opus, GPT-4V)
2. Encode image to base64:
   import base64
   with open('screenshot.png', 'rb') as f:
       image_data = base64.b64encode(f.read()).decode()

3. Send image with prompt (provider-specific API)
4. Same prompting pattern applies!

The core Amplifier pattern (load, compose, prepare, execute) 
stays the same - just add image handling at the provider level.
""")
    
    print("\n🎯 USE CASES:")
    print("  - Design review automation")
    print("  - Accessibility audits")
    print("  - Design system compliance checking")
    print("  - UI/UX best practices validation")
    print("  - Iterative design feedback loops")


if __name__ == "__main__":
    asyncio.run(main())
