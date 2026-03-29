---
name: myslide
description: |
  Create professional AWS-themed PowerPoint presentations with dark gradient
  backgrounds, AWS brand colors, and rich visual elements (SVG diagrams,
  architecture diagrams, icons). Uses the official AWS reInvent 2023 template
  design system. Supports creating from scratch, editing specific slides
  conversationally, and embedding SVG visualizations for maximum design impact.
  Trigger: "myslide", "make slides", "AWS presentation", "create pptx",
  "slide deck", "AWS slides", "프레젠테이션", "슬라이드 만들어", "발표자료"
license: MIT License
metadata:
  skill-author: Jesam Kim
  version: 1.0.0
allowed-tools: [Read, Write, Edit, Bash, Grep, Glob, Agent, AskUserQuestion]
---

# MySlide - AWS-Themed Presentation Generator

Create visually compelling presentations that follow the AWS reInvent 2023 design system.
Every slide should look like it was crafted by the AWS brand team.

## Quick Start

1. Read [references/aws-theme.md](references/aws-theme.md) for colors, fonts, and spacing rules
2. Read [references/slide-patterns.md](references/slide-patterns.md) for layout templates
3. Read [references/pptxgenjs.md](references/pptxgenjs.md) for PptxGenJS creation guide
4. Read [references/editing.md](references/editing.md) for editing existing PPTX files
5. Use `scripts/create_aws_slide.py` to generate background/SVG assets
6. Official AWS service icons are in `icons/` (248 icons extracted from AWS Architecture Icon Deck)

All references and scripts are self-contained within this skill directory.
No external skill dependencies required.

## Default Presenter

When generating title/thank-you slides, use these defaults unless the user specifies otherwise:
- Korean: **김제삼**
- English: **Jesam Kim**
- Title: Solutions Architect
- Company: Amazon Web Services
- Email: jesamkim@amazon.com

## Workflow

### A. Creating a New Presentation

1. **Gather requirements**: Ask the user for topic, key messages, and target audience
2. **Plan slide structure**: Outline slide titles and content types before writing any code
3. **Generate background images**: Run the gradient background generator script
4. **Create slides**: Use PptxGenJS (Node.js) with the AWS theme constants
5. **Add SVG visuals**: Generate SVG diagrams for architecture/flow slides and embed as images
6. **QA (subagent)**: Always delegate QA to a subagent (Sonnet 4.6+) to protect the main context window from image-heavy inspection. The subagent converts slides to images, visually verifies every slide against the QA Checklist, and returns a text-only report of issues found.

**Paths (Python / `execute_code`):** The host app defines `WORKING_DIR` (the `application/` folder) and `SKILLS_DIR` = `WORKING_DIR/skills`. This skill’s script is **`os.path.join(SKILLS_DIR, "myslide", "scripts", "create_aws_slide.py")`**. Do not append extra segments like `agent-skills/application`—that duplicates the repo path and causes “file not found” even when the file exists. Do not use `os.environ.get("SKILLS_DIR", ...)` with a guessed default. Below, relative paths assume the shell cwd is this skill directory (`.../skills/myslide`).

```bash
# Step 1: Generate AWS gradient backgrounds (run from myslide/ or pass absolute script path as above)
python3 scripts/create_aws_slide.py backgrounds --output-dir /tmp/myslide-assets/

# Step 2: Run the PptxGenJS creation script (generated per presentation)
node create_presentation.js

# Step 3: QA - delegate to subagent (see "QA Subagent" section below)
```

### B. Editing an Existing Slide

When the user says "change slide 3" or "update the title slide":
1. Identify which slide(s) to modify
2. Read the current slide content (markitdown + image inspection)
3. Apply targeted changes (text, colors, layout, or visual elements)
4. Re-render and verify only the affected slides

### C. Sub-Agent Strategy for Large Decks (8+ slides)

For presentations with many slides, use parallel sub-agents to maximize throughput.
Each sub-agent handles an independent group of slides.

**Parallelization pattern:**
- Agent 1: Title + Section Header slides (structural)
- Agent 2: Content slides (odd-numbered)
- Agent 3: Content slides (even-numbered)
- Agent 4: SVG diagram generation for all visual slides

```
# Sub-agent prompt template:
Generate slides [N] through [M] for the AWS presentation.
- Use the AWS theme from references/aws-theme.md
- Background images are at: /tmp/myslide-assets/
- Save individual slide JS snippets to: /tmp/myslide-parts/slide-{N}.js
- Follow the layout pattern specified for each slide type.
```

After all sub-agents complete, combine the JS snippets into one PptxGenJS script and execute.

## Slide Types

Each presentation should use a MIX of these layouts. Never repeat the same layout more than twice in a row.

| Type | When to Use | Reference |
|------|-------------|-----------|
| **Title** | First slide (single speaker) | slide-patterns.md > Title Slide |
| **Title (Two Speakers)** | First slide (co-presentation) | slide-patterns.md > Two-Speaker Title |
| **Agenda** | Second slide, table of contents | slide-patterns.md > Agenda Slide |
| **Section Header** | Chapter dividers (01, 02...) | slide-patterns.md > Section Header |
| **Content Card** | Key points with icon | slide-patterns.md > Content Card |
| **Two Column Card** | Side-by-side options/concepts | slide-patterns.md > Two Column Card |
| **Three Column** | Comparisons, 3 options | slide-patterns.md > Three Column |
| **Process Flow** | User scenarios, step-by-step flows | slide-patterns.md > Process Flow |
| **Comparison Table** | Option comparisons, feature matrices | slide-patterns.md > Comparison Table |
| **Architecture** | System diagrams | slide-patterns.md > Architecture |
| **Venn/Comparison** | Overlapping concepts | slide-patterns.md > Venn Diagram |
| **Screenshot+Text** | Demo/console walkthrough | slide-patterns.md > Screenshot |
| **Summary Grid** | 2x2 key takeaways | slide-patterns.md > Summary Grid |
| **Evolution/Progression** | Maturity stages, AI evolution | slide-patterns.md > Evolution |
| **Multi-Card Grid** | 2x2 or 3x2 feature cards | slide-patterns.md > Multi-Card Grid |
| **Gradient Border Cards** | Light cards with colored borders on dark bg | slide-patterns.md > Gradient Border Cards |
| **Image Hero** | Topic intro with generated visual | slide-patterns.md > Image Hero |
| **Image + Text Split** | Concept with illustration | slide-patterns.md > Image + Text Split |
| **Full Image Background** | Impactful quotes, key messages | slide-patterns.md > Full Image Background |
| **Thank You** | Last slide | slide-patterns.md > Thank You |

## SVG Visual Elements

For slides that need diagrams, architecture visuals, or flowcharts, generate inline SVG and
convert to PNG for embedding. This dramatically improves visual quality over plain text slides.

**When to generate SVGs:**
- Architecture or system flow explanations
- Process/workflow diagrams
- Comparison charts or feature matrices
- Any slide where the content is inherently visual

**SVG generation approach:**
```bash
# Generate SVG with official AWS service icons, convert to PNG for embedding
python3 scripts/create_aws_slide.py svg-diagram \
  --type architecture \
  --elements "CloudFront,API Gateway,Lambda,Bedrock,S3" \
  --output /tmp/myslide-assets/arch-diagram.png
```

**Available icon names** (use these as element names for automatic icon embedding):
Lambda, EC2, S3, DynamoDB, API Gateway, CloudFront, Bedrock, ECS, EKS, RDS,
Aurora, VPC, ELB, Route 53, CloudWatch, IAM, Cognito, SNS, SQS, Step Functions,
EventBridge, Kinesis, SageMaker, Redshift, Athena, Glue, KMS, WAF, Shield,
Fargate, ECR, AppSync, OpenSearch, ElastiCache, Secrets Manager, ACM, and 200+ more.
See `icons/` directory for the full list of available service icons.

Alternatively, craft SVG inline in the Node.js script and use `sharp` to convert to PNG base64.

### Diagram Type Selection for Presentations

Presentations almost always need **High-Level diagrams** (logical grouping), not Infrastructure diagrams (VPC/Subnet).

| Presentation Context | Diagram Type | Key Characteristics |
|---------------------|-------------|-------------------|
| Executive briefing, sales pitch | **High-Level** | Hub-spoke layout, generic containers, no VPC/Subnet |
| Technical deep-dive, Well-Architected review | **Infrastructure** | VPC/AZ/Subnet nesting, security boundaries |
| Platform overview (e.g., AgentCore, EKS) | **High-Level (Hub-Spoke)** | Central runtime + radiating spoke services |
| Event-driven / microservices overview | **High-Level** | EventBridge as central bus, producers left, consumers right |

### High-Level Architecture Pattern (Hub-Spoke)

For presentation slides, the hub-spoke pattern is most effective:
- **Central service** (Bedrock, EventBridge, EKS) at the center of the diagram
- **Spoke services** radiate outward with clear directional arrows
- **Logical grouping** using `generic` container (dashed border) instead of VPC/Subnet
- **Observability** (CloudWatch, X-Ray) at the bottom, separated from main flow
- **No AWS Cloud outer container** -- cleaner for slides
- **Multi-directional arrows** -- not just left-to-right; use top/bottom/left/right freely
- **Descriptive arrow labels** -- "Streamable HTTP", "IdP integration", "Metrics & logs"

### Arrow-Icon Collision Rules

- **Arrows must NEVER pass through another service box.** This is the #1 architecture
  diagram defect. Before drawing each arrow, trace the path from source to target and
  verify no intermediate box lies in the way. If one does:
  1. **Route around**: Use two LINE segments (L-shape or Z-shape) to go around the box
  2. **Move the blocking box** to a row/column that clears the path
  3. **Connect via the intermediate box** if there's a logical flow through it
- **Arrow segments must maintain >= 10px clearance** from any icon bounding box
- **In hub-spoke layouts, radial arrows must not cross sibling spokes** -- stagger spoke y-positions
- **Validation**: After all elements are placed, mentally trace every arrow and confirm
  it does not cross any box. See `slide-patterns.md > Arrow Routing Rules` for code examples.

### aws-diagram Skill Integration

For **complex architecture diagrams** (VPC nesting, orthogonal arrows, 8+ services), use the
`aws-diagram` skill instead of `create_aws_slide.py`. It produces native PPTX architecture slides
that can be merged into myslide decks:
```bash
# 1. Generate architecture diagram with aws-diagram skill
python3 /path/to/aws-diagram/scripts/generate_diagram.py \
  -i diagram.json -o /tmp/arch.svg --png --pptx /tmp/arch-slide.pptx

# 2. Merge into myslide deck using add_slide.py
python3 scripts/add_slide.py --deck output.pptx --insert /tmp/arch-slide.pptx --position 4
```

For High-Level diagrams in aws-diagram JSON, use `"type": "generic"` containers:
```json
{"id": "platform", "type": "generic", "label": "Amazon Bedrock AgentCore",
 "children": ["runtime", "code-interp", "identity", "memory"]}
```

**SVG layer order rule** (applies to all SVG diagram generation):
Icons must render ABOVE arrows. Render order: background > containers > arrows > callouts > icons.

## Nova Canvas Image Generation (Optional)

When a slide needs a **conceptual illustration, hero image, or visual metaphor** that cannot
be expressed with SVG diagrams or AWS icons, use the `nova-canvas` skill to generate images
via Amazon Bedrock Nova Canvas.

**Requires**: The `nova-canvas` skill must be installed in the Claude Code environment.
Script path: `${NOVA_CANVAS_SKILL_PATH}/scripts/generate_image.py`

### When to Use Nova Canvas vs SVG Diagrams

| Content Type | Use SVG/Icons | Use Nova Canvas |
|---|---|---|
| AWS architecture diagrams | Yes | No |
| Process flows, step diagrams | Yes | No |
| Conceptual hero images (AI brain, cloud, etc.) | No | Yes |
| Abstract background visuals | No | Yes |
| Product/scenario illustrations | No | Yes |
| Screenshot placeholders | No | Yes |
| Data flow with service icons | Yes | No |

### Slide-Optimized Image Sizes

All dimensions are divisible by 16 (Nova Canvas requirement).

| Use Case | Width | Height | Ratio | Slide Coverage |
|---|---|---|---|---|
| Full-slide background | 1280 | 720 | 16:9 | Entire slide behind text |
| Hero image (title slide) | 1280 | 720 | 16:9 | Right 60-70% of slide |
| Half-slide (left or right) | 640 | 720 | ~9:10 | Left/right half |
| Card illustration | 512 | 512 | 1:1 | Inside a content card |
| Small icon/illustration | 384 | 384 | 1:1 | Thumbnail in card grid |
| Banner (wide strip) | 1280 | 384 | 10:3 | Top or bottom strip |

### Integration Workflow

```bash
# 1. Generate image with nova-canvas (find skill path dynamically)
NOVA_CANVAS_SCRIPT=$(find ~/.claude/plugins -path "*/nova-canvas/scripts/generate_image.py" 2>/dev/null | head -1)

python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Abstract dark gradient with glowing neural network connections, deep navy and purple tones, futuristic technology atmosphere, minimal clean composition" \
  --negative-text "text, watermarks, logos, people, bright colors, white background" \
  --width 1280 --height 720 \
  --quality standard \
  --cfg-scale 6.5 \
  --output-dir /tmp/myslide-assets/ \
  --region us-east-1

# 2. Read the generated image path from JSON output
# Result: {"task": "text_to_image", "seed": 12345, "images": ["/tmp/myslide-assets/text_to_image_1.png"]}

# 3. Embed in PptxGenJS using base64 or file path
```

### Prompt Guidelines for Presentation Images

Nova Canvas prompts for slides should follow specific conventions to match the AWS dark theme:

**Style keywords to include:**
- "dark background", "deep navy", "dark gradient" (matches AWS theme)
- "minimal", "clean composition" (professional look)
- "glowing", "luminous accents" (matches orange/magenta highlights)
- "futuristic", "technology", "digital" (AWS tech context)
- "professional", "corporate" (presentation-appropriate)

**Standard negative prompt for all slide images:**
```
"text, watermarks, logos, signatures, bright white background, oversaturated, cartoon, childish, cluttered, busy composition, low quality, blurry"
```

**Prompt templates by slide type:**

| Slide Type | Prompt Pattern |
|---|---|
| Title hero | "[Topic concept] visualization, dark futuristic background, glowing [accent color] accents, cinematic wide shot, professional technology illustration" |
| Content illustration | "[Concept] depicted as [visual metaphor], dark navy background, clean minimal style, soft ambient lighting, 3D render" |
| Background overlay | "Abstract [theme] pattern, dark gradient from deep navy to black, subtle glowing particles, seamless texture, minimal" |
| Card thumbnail | "[Subject icon/symbol], centered on dark background, simple flat design with glow effect, single color accent, square composition" |

### Embedding Generated Images in PptxGenJS

```javascript
const fs = require('fs');

// Read nova-canvas output as base64
const heroImage = fs.readFileSync('/tmp/myslide-assets/text_to_image_1.png');
const heroBase64 = 'image/png;base64,' + heroImage.toString('base64');

// Full-slide background image
slide.background = { data: heroBase64 };

// Or position as a visual element
slide.addImage({
  data: heroBase64,
  x: 5.5, y: 0, w: 7.83, h: 7.5,  // Right 60% of slide
});

// Semi-transparent overlay on top of image (for text readability)
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0, y: 0, w: 13.33, h: 7.5,
  fill: { color: "000000", transparency: 50 }
});
```

### Cost Awareness

- Standard quality 1024x1024: ~$0.04/image
- Standard quality 1280x720: ~$0.04/image
- A typical 10-slide deck with 3-4 generated images: ~$0.16
- Use `--seed` parameter to reproduce exact images when iterating

See [references/nova-canvas-integration.md](references/nova-canvas-integration.md) for detailed
prompt examples and advanced techniques.

## Rounded Rectangle Default

All card-like shapes (content cards, agenda items, grid cells, tag badges, summary bars)
MUST use `ROUNDED_RECTANGLE` with `rectRadius` instead of plain `RECTANGLE`.
This gives a modern, polished look consistent with contemporary UI design.

### Radius Guidelines

| Element | rectRadius | Notes |
|---------|-----------|-------|
| Large cards (content, 2-col, 3-col) | 0.10 - 0.12 | Main content containers |
| Inner boxes (code blocks, option cards) | 0.06 - 0.08 | Nested within larger cards |
| Tag badges (effort/impact labels) | 0.15 | Pill-shaped badges |
| Progress bars | 0.10 | Pill-shaped bars |
| Summary/footer bars | 0.08 | Horizontal wide bars |

### Accent Styling with Rounded Corners

Do NOT overlay thin rectangular accent bars on ROUNDED_RECTANGLE shapes (they won't
align with corners). Instead, use `line` border property for accent colors:

```javascript
// ✅ CORRECT: Rounded card with colored border accent
slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
  x: 0.5, y: 1.2, w: 5.8, h: 4.5, rectRadius: 0.12,
  fill: { color: "161E2D" },
  line: { color: "C91F8A", width: 1.5 },  // accent color as border
  shadow: mkShadow()
});

// ❌ WRONG: Overlay accent bar on rounded corners
slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { ... });
slide.addShape(pres.shapes.RECTANGLE, { x: 0.5, y: 1.2, w: 0.08, h: 4.5, fill: { color: "C91F8A" } });
```

### When to Keep RECTANGLE

Only use plain `RECTANGLE` for:
- Full-slide overlays (dark transparency over hero images)
- Table cell backgrounds (must tile without gaps)
- Decorative gradient accent bars at slide edges

## Color Discipline (CRITICAL)

Slides look more professional with fewer, well-chosen colors. Too many colors
create visual noise and distract from the message. Limit each slide to **5 core
colors** maximum. A restrained palette feels intentional and polished; a rainbow
of colors feels chaotic.

### The 5 Core Colors

| Role | Color | HEX | When to use |
|------|-------|-----|-------------|
| **Background** | Deep Purple-Black | `09051B` | Slide background (via gradient image) |
| **Primary Text** | White | `FFFFFF` | All headings, body text, bullets |
| **Emphasis** | Orange | `F66C02` | Key terms, highlights, numbered badges |
| **Container** | Dark Navy | `161E2D` | Card fills, table cells, box backgrounds |
| **Secondary** | Slate Gray | `5A6B86` | Muted text, separators, captions |

### Allowed Accent (sparingly)

- `C91F8A` (Magenta) — card border lines only, max 1-2 per slide
- `5600C2` (Purple) — already in the gradient background, no need to add separately

### Colors to AVOID in regular slides

These colors exist in the theme file for special cases but should NOT appear in
normal content slides. Using them creates a cluttered, inconsistent look:

- `FF28EF` (Neon Pink) — only for section header numbers
- `ABABE3` (Lavender), `FF9EA2` (Salmon Pink) — only for complex architecture diagrams
- `00A0C8` (Teal), `69AE35` (Green) — only when semantically meaningful (e.g., success/info)
- `010135`, `02043B` — only in multi-layer architecture diagrams

**Rule of thumb**: If you're about to use a 6th color, stop and ask whether one
of the 5 core colors can serve the same purpose.

### Gradient Fills for Cards and Containers

PptxGenJS does not support native gradient fills on shapes. To create gradient
card backgrounds (e.g., a subtle dark-to-darker gradient inside a card), render
an SVG gradient rectangle and convert to PNG:

```javascript
const sharp = require('sharp');

// Create a gradient card background (e.g., darkNavy to bgBase)
async function createGradientCard(w, h, colors = ['161E2D', '09051B'], direction = 'vertical') {
  const pxW = Math.round(w * 96);  // inches to pixels at 96dpi
  const pxH = Math.round(h * 96);
  const [c1, c2] = colors;
  const gradDir = direction === 'vertical'
    ? 'x1="0" y1="0" x2="0" y2="1"'
    : 'x1="0" y1="0" x2="1" y2="0"';

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${pxW}" height="${pxH}">
    <defs>
      <linearGradient id="g" ${gradDir}>
        <stop offset="0%" stop-color="#${c1}"/>
        <stop offset="100%" stop-color="#${c2}"/>
      </linearGradient>
    </defs>
    <rect width="${pxW}" height="${pxH}" rx="12" fill="url(#g)"/>
  </svg>`;

  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  return 'image/png;base64,' + png.toString('base64');
}

// Usage: gradient card as image background
const gradBg = await createGradientCard(12.5, 4.84, ['161E2D', '0D1117']);
slide.addImage({ data: gradBg, x: 0.42, y: 1.41, w: 12.5, h: 4.84 });

// Then add text/shapes on top of the gradient card
slide.addText("Title", { x: 0.8, y: 1.8, w: 11.5, h: 0.5, ... });
```

**Gradient presets for common use cases:**

| Use Case | From | To | Direction |
|----------|------|----|-----------|
| Card background | `161E2D` | `0D1117` | vertical (top→bottom) |
| Header bar | `161E2D` | `09051B` | horizontal (left→right) |
| Highlight card | `1A0B3D` | `161E2D` | vertical |
| Summary footer | `0D1117` | `161E2D` | horizontal |

Use gradient fills sparingly — they add visual depth but overuse diminishes the effect.
One or two gradient cards per presentation is ideal.

### Gradient Borders (for any shape)

Gradient borders work on any card — single Content Card, Multi-Card Grid,
or the Gradient Border Cards layout. The technique: render an SVG with a
gradient-filled rectangle and a smaller inner rectangle in the fill color,
creating a border effect.

```javascript
// Reusable gradient border generator — works for any card size
async function createGradientBorder(w, h, borderColors, fillColor = 'F2F4F4', borderWidth = 3) {
  const pxW = Math.round(w * 96);
  const pxH = Math.round(h * 96);
  const [c1, c2] = borderColors;
  const r = 12;  // corner radius

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${pxW}" height="${pxH}">
    <defs>
      <linearGradient id="b" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#${c1}"/>
        <stop offset="100%" stop-color="#${c2}"/>
      </linearGradient>
    </defs>
    <rect width="${pxW}" height="${pxH}" rx="${r}" fill="url(#b)"/>
    <rect x="${borderWidth}" y="${borderWidth}"
          width="${pxW - borderWidth * 2}" height="${pxH - borderWidth * 2}"
          rx="${r - 1}" fill="#${fillColor}"/>
  </svg>`;

  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  return 'image/png;base64,' + png.toString('base64');
}

// Example: Single Content Card with orange→magenta gradient border
const cardFrame = await createGradientBorder(12.5, 4.84, ['F66C02', 'C91F8A'], '161E2D');
slide.addImage({ data: cardFrame, x: 0.42, y: 1.41, w: 12.5, h: 4.84 });
// Then add text on top...

// Example: Light card with purple→blue gradient border
const lightCard = await createGradientBorder(5.8, 4.5, ['5600C2', '2D7CFB'], 'F2F4F4');
slide.addImage({ data: lightCard, x: 1.0, y: 1.5, w: 5.8, h: 4.5 });
```

**Common gradient border presets:**

| Style | From | To | Fill | Effect |
|-------|------|----|------|--------|
| Warm accent | `F66C02` | `C91F8A` | `161E2D` | Orange→Magenta on dark card |
| Cool accent | `5600C2` | `2D7CFB` | `F2F4F4` | Purple→Blue on light card |
| Brand gradient | `C91F8A` | `5600C2` | `161E2D` | Magenta→Purple on dark card |
| Subtle | `5A6B86` | `161E2D` | `161E2D` | Gray fade, barely visible |

See `slide-patterns.md > Gradient Border Cards` for the full multi-card layout pattern.

## Typography Size Rules (CRITICAL)

Body text must NEVER be smaller than 15pt. This is the #1 visual quality issue.
Table cells, comparison columns, and process flow card text are NOT exceptions —
they must also be 15pt minimum. Only footer copyright and slide-number captions
may go below 15pt.

### Minimum Font Size by Element

| Element | Min Size (pt) | Recommended (pt) | Weight |
|---------|--------------|-------------------|--------|
| Slide title | 36 | 36-44 | Bold/Heavy |
| Section number (01, 02) | 36 | 36 | Bold |
| Card title / Sub-header | 20 | 20-24 | Bold |
| **Body text** | **15** | **16** | Regular |
| **Bullet items** | **15** | **15-16** | Regular |
| **Table cell text** | **15** | **15-16** | Regular |
| **Process flow card text** | **15** | **15-16** | Regular |
| **Three-column body** | **15** | **15-16** | Regular |
| Caption / Footer / Copyright | 8 | 8-10 | Light |

**The only elements allowed below 15pt are:**
- Footer copyright text (8pt)
- Slide number labels (8-10pt)
- Service name labels under icons in architecture diagrams (10-12pt)

Everything else — including table cells, comparison columns, process flow
descriptions, multi-card grid body text — MUST be 15pt or higher. If content
overflows at 15pt, reduce the text content rather than shrinking the font.

### Card-to-Text Balance Rule

Text should fill **at least 60%** of the card's vertical space. If a card has excessive
bottom whitespace (>30% empty), apply one or more of these fixes:

1. **Increase font size** (first choice) — bump body text to 16-18pt
2. **Increase line spacing** — use `lineSpacingMultiple: 1.3` to `1.5`
3. **Reduce card height** — shrink the card to fit the content snugly
4. **Add vertical centering** — use `valign: "middle"` on text boxes

```javascript
// WRONG: Small text in large card = excessive whitespace
slide.addText(bodyText, {
  x: 1, y: 2.5, w: 5, h: 3.5,
  fontSize: 12, // TOO SMALL
});

// CORRECT: Properly sized text filling the card
slide.addText(bodyText, {
  x: 1, y: 2.5, w: 5, h: 3.5,
  fontSize: 16,
  lineSpacingMultiple: 1.3,
  valign: "top",
});
```

### Grid/Multi-Card Slides

For 2x2 grids and 3-column layouts where space is tighter:
- Card title: 18-20pt bold
- Body text: **15pt minimum** (never drop to 12-14pt)
- Table cells: **15pt minimum** (not 13-14pt — reduce column count or text instead)
- Process flow card text: **15pt minimum**
- If content overflows, reduce text content rather than font size
- Use `paraSpaceAfter: 6` instead of `paraSpaceAfter: 10` to reclaim space

## Text Emphasis Rules

The AWS template uses specific color highlighting for emphasis:
- **Orange (`F66C02`)**: Primary emphasis for key terms, important concepts
- **Magenta (`C91F8A`)**: Secondary emphasis, links, interactive elements
- **White bold**: Section headers within content areas
- **Regular white**: Body text

Use rich text arrays in PptxGenJS to apply selective highlighting:
```javascript
slide.addText([
  { text: "Customer data is ", options: { color: "FFFFFF", fontSize: 14 } },
  { text: "never used for training", options: { color: "F66C02", fontSize: 14, bold: true } },
  { text: " any model.", options: { color: "FFFFFF", fontSize: 14 } }
], { x: 1, y: 2, w: 8, h: 1 });
```

### Gradient Text

For large impact numbers or hero text that needs a gradient color effect (e.g., "80%" with
orange-to-pink gradient), PptxGenJS doesn't support gradient fills on text natively.
Use the SVG workaround documented in `references/pptxgenjs.md` (section "Gradient Text").
The approach renders gradient text as SVG, converts to PNG via sharp, and embeds as an image.

## Conversational Editing

The user may request changes like:
- "3번 슬라이드 제목을 바꿔줘" → Read slide 3, modify title text
- "아키텍처 다이어그램을 추가해줘" → Generate SVG + add new slide
- "색상을 좀 더 밝게" → Adjust fill/text colors
- "순서를 바꿔줘" → Reorder slides in presentation.xml

For each edit request:
1. Show the user what the current slide looks like (render to image)
2. Describe the proposed change
3. Apply the change
4. Show the result for confirmation

## Team-Up Strategy (Large Presentations, 15+ slides)

For very large or complex presentations, use the team-assemble pattern to coordinate
multiple agents working in parallel. This is more powerful than simple sub-agents because
it enables inter-agent communication, shared task tracking, and phased execution.

**When to use Team-Up instead of Sub-Agents:**
- 15+ slides with diverse content types
- Presentations requiring research + design + implementation phases
- When SVG diagram generation is complex and needs dedicated attention

**Team Composition:**

| Agent | Role | Model | Responsibility |
|-------|------|-------|----------------|
| **lead** | Orchestrator | opus | Plan structure, assign tasks, final QA |
| **designer** | Visual Design | sonnet | Generate backgrounds, SVG diagrams, icons |
| **writer-1** | Content (slides 1-N/2) | sonnet | Write first half slide code |
| **writer-2** | Content (slides N/2+1-N) | sonnet | Write second half slide code |
| **qa** | Quality Assurance | sonnet | Render slides to images, check visual quality (image-heavy — needs Sonnet 4.6+) |

**Team Workflow:**
```
1. lead: Analyze topic, create slide outline, assign tasks
2. designer + writer-1 + writer-2: Work in parallel
   - designer generates all background/SVG assets
   - writers create PptxGenJS code for assigned slides
3. lead: Combine all parts into single presentation script
4. qa: Render and inspect every slide, report issues
5. lead: Apply fixes, finalize
```

**Task Assignment Pattern:**
```
TaskCreate: "Generate AWS gradient backgrounds and SVG diagrams"
  -> owner: designer
TaskCreate: "Write slides 1-8 (Title, Section, Content)"
  -> owner: writer-1, blockedBy: [designer task]
TaskCreate: "Write slides 9-15 (Architecture, Summary, Thank You)"
  -> owner: writer-2, blockedBy: [designer task]
TaskCreate: "QA all rendered slides"
  -> owner: qa, blockedBy: [writer-1 task, writer-2 task]
```

## Bundled Scripts Reference

All scripts are self-contained within `scripts/`:

| Script / Directory | Purpose |
|--------|---------|
| `icons/` | 248 official AWS service icons (SVG, from AWS Architecture Icon Deck) |
| `scripts/create_aws_slide.py` | Generate AWS gradient backgrounds, SVG diagrams with official icons, logo |
| `scripts/office/soffice.py` | Convert PPTX to PDF via LibreOffice |
| `scripts/office/unpack.py` | Unpack PPTX to XML for direct editing |
| `scripts/office/pack.py` | Repack edited XML back to PPTX |
| `scripts/thumbnail.py` | Generate thumbnail grid for visual overview |
| `scripts/clean.py` | Clean PPTX XML (remove unused elements) |
| `scripts/add_slide.py` | Add slides to existing PPTX |
| `references/nova-canvas-integration.md` | Nova Canvas image generation guide for slides |

**QA Subagent (ALWAYS use — protects main context from image token cost):**

QA is image-heavy and consumes significant context window tokens. Always spawn a
dedicated subagent (Sonnet 4.6 or higher) for visual inspection. The subagent runs
the conversion commands, reads the rendered slide images, checks every item in the
QA Checklist, and returns a **text-only** summary of pass/fail results and specific
issues found. The main agent never reads the slide images directly.

Subagent prompt template:
```
You are a QA inspector for AWS-themed PowerPoint presentations.

1. Convert PPTX to images:
   python3 {skill_path}/scripts/office/soffice.py --headless --convert-to pdf {pptx_path}
   pdftoppm -jpeg -r 150 {pdf_path} {output_prefix}

2. Read each slide image and verify against this checklist:
   [paste QA Checklist items]

3. Return a text-only report:
   - Per-slide: PASS or FAIL with specific issues
   - Summary: total pass/fail count and critical issues to fix

Do NOT return images. Only return text findings.
```

**Editing workflow (unpack/repack):**
```bash
python3 scripts/office/unpack.py input.pptx unpacked/
# ... edit files in unpacked/ ...
python3 scripts/clean.py unpacked/
python3 scripts/office/pack.py unpacked/ output.pptx
```

## QA Checklist

After generating any presentation:
- [ ] Every slide has a visual element (no text-only slides)
- [ ] Color contrast is sufficient (white text on dark backgrounds)
- [ ] Orange highlights are used sparingly (max 2-3 per slide)
- [ ] Layouts vary across the deck (no 3+ identical patterns in a row)
- [ ] Presenter info matches defaults or user-provided values
- [ ] All SVG diagrams render cleanly without text overlap
- [ ] SVG layer order correct: icons render above arrows (not obscured)
- [ ] No arrow lines cross over service icons (>= 10px clearance)
- [ ] Architecture diagrams use appropriate type (High-Level for presentations, Infrastructure for tech deep-dives)
- [ ] Hub-spoke diagrams have staggered spoke y-positions (no radial arrow crossings)
- [ ] Arrow markers use Open Arrow style (stroke, not filled polygon)
- [ ] All card/container shapes use ROUNDED_RECTANGLE (not plain RECTANGLE)
- [ ] Accent colors use line border, not overlay bars
- [ ] 슬라이드당 5색 이내 (bgBase, white, orange, darkNavy, slateGray + magenta 테두리)
- [ ] 불필요한 색상 사용 없음 (lavender, salmon, teal, green 등 특수 색상 금지)
- [ ] Body text fontSize >= 15pt (NEVER 12-14pt) — includes table cells, process flow cards, column text
- [ ] Card whitespace ratio: text fills >= 60% of card height (no excessive bottom gaps)
- [ ] Grid/multi-card slides use 15pt+ body text (not smaller to "fit")
- [ ] Thank You slide: 영문/한글 텍스트가 겹치지 않음 (별도 줄에 충분한 간격)
- [ ] Architecture diagram: 모든 서비스 박스에 AWS 서비스 아이콘이 포함됨 (빈 박스 금지)
- [ ] Footer with AWS logo placement is consistent
- [ ] Nova Canvas images match AWS dark theme (predominantly dark with accent glows)
- [ ] Text overlay on generated images has sufficient contrast (dark overlay applied)
- [ ] Generated image dimensions match target slide area (no stretching/distortion)
