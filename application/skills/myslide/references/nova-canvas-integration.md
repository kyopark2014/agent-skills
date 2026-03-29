# Nova Canvas Integration Guide for MySlide

## Overview

This guide covers how to use the `nova-canvas` skill to generate images for
PowerPoint presentations created by the `myslide` skill. Nova Canvas generates
studio-quality images via Amazon Bedrock that integrate seamlessly with the
AWS dark theme design system.

## Finding the Nova Canvas Script

```bash
# Dynamically locate the nova-canvas skill script
NOVA_CANVAS_SCRIPT=$(find ~/.claude/plugins -path "*/nova-canvas/scripts/generate_image.py" 2>/dev/null | head -1)

# Verify it exists
if [ -z "$NOVA_CANVAS_SCRIPT" ]; then
  echo "nova-canvas skill not found. Install it first."
  exit 1
fi
```

## Slide-Optimized Prompt Recipes

### Title Slide Hero Images

**AI/ML Topic:**
```bash
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Futuristic artificial intelligence visualization, interconnected neural network nodes with glowing orange and cyan pathways, deep dark navy background, volumetric lighting, cinematic wide angle composition, professional technology illustration, ultra detailed 3D render" \
  --negative-text "text, watermarks, logos, people, bright white background, cartoon, cluttered, busy" \
  --width 1280 --height 720 --quality standard --cfg-scale 6.5 \
  --output-dir /tmp/myslide-assets/ --region us-east-1
```

**Cloud/Infrastructure Topic:**
```bash
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Abstract cloud computing infrastructure, floating translucent server racks connected by light beams, dark cosmic background with subtle blue nebula, minimal clean composition, professional futuristic visualization, cinematic lighting" \
  --negative-text "text, watermarks, people, cartoon, bright colors, white background, cluttered" \
  --width 1280 --height 720 --quality standard --cfg-scale 6.5 \
  --output-dir /tmp/myslide-assets/ --region us-east-1
```

**Security Topic:**
```bash
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Digital security shield concept, translucent protective barrier with hexagonal grid pattern, glowing cyan and orange edges, dark environment with subtle particle effects, professional 3D render, centered composition, cybersecurity visualization" \
  --negative-text "text, watermarks, people, cartoon, padlock icon, key icon, bright background" \
  --width 1280 --height 720 --quality standard --cfg-scale 6.5 \
  --output-dir /tmp/myslide-assets/ --region us-east-1
```

**Data/Analytics Topic:**
```bash
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Abstract data visualization flowing through space, luminous data streams and holographic charts, deep dark background with blue and purple tones, minimal futuristic composition, professional technology illustration, cinematic depth of field" \
  --negative-text "text, numbers, watermarks, people, bright white, cartoon, cluttered" \
  --width 1280 --height 720 --quality standard --cfg-scale 6.5 \
  --output-dir /tmp/myslide-assets/ --region us-east-1
```

### Content Card Illustrations (Square)

**AI Agent Concept:**
```bash
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Minimalist AI agent visualization, glowing humanoid silhouette made of circuit patterns, dark navy background, single orange accent glow, centered composition, clean professional 3D render" \
  --negative-text "text, busy background, multiple figures, bright colors" \
  --width 512 --height 512 --quality standard --cfg-scale 6.5 \
  --output-dir /tmp/myslide-assets/ --region us-east-1
```

**Knowledge Base / RAG:**
```bash
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Digital knowledge library, floating holographic books and documents connected by light threads, dark background, blue and orange glow accents, minimalist 3D render, centered composition" \
  --negative-text "text, people, cartoon, bright background, cluttered" \
  --width 512 --height 512 --quality standard --cfg-scale 6.5 \
  --output-dir /tmp/myslide-assets/ --region us-east-1
```

### Abstract Backgrounds

**Cosmic/Space Theme:**
```bash
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Very dark cosmic nebula, deep space with subtle blue and purple gas wisps, scattered tiny stars, extremely dark overall tone, minimal clean atmosphere, suitable as dark presentation background" \
  --negative-text "text, planets, bright colors, people, objects, busy details, white, light areas" \
  --width 1280 --height 720 --quality standard --cfg-scale 7.0 \
  --output-dir /tmp/myslide-assets/ --region us-east-1
```

**Geometric/Tech Theme:**
```bash
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Abstract dark geometric pattern, interconnected hexagons and triangles with subtle glowing edges, deep navy to black gradient, very minimal and clean, low contrast, suitable as dark presentation background" \
  --negative-text "text, bright colors, people, objects, high contrast, white, busy" \
  --width 1280 --height 720 --quality standard --cfg-scale 7.0 \
  --output-dir /tmp/myslide-assets/ --region us-east-1
```

## Color Matching with AWS Theme

Nova Canvas prompts should include color references that match the AWS dark theme:

| AWS Theme Color | Hex | Nova Canvas Description |
|---|---|---|
| Background base | `0A0E14` | "very dark navy", "near-black" |
| Dark navy | `161E2D` | "deep dark navy", "midnight blue" |
| Orange accent | `F66C02` | "glowing orange accents", "warm amber glow" |
| Magenta accent | `C91F8A` | "magenta highlights", "pink-purple glow" |
| Slate gray | `8D99AE` | "subtle gray tones", "muted steel" |
| White | `FFFFFF` | "bright white accents", "luminous white" |

**Key principle**: Generated images should be predominantly dark (matching slide backgrounds)
with accent glows that complement the orange/magenta highlight system.

## Advanced Techniques

### Seed-Based Consistency

When generating multiple images for the same deck, use related seeds for visual consistency:

```bash
# Generate 3 related images with sequential seeds
for i in 1 2 3; do
  python3 "$NOVA_CANVAS_SCRIPT" \
    --task text_to_image \
    --prompt "..." \
    --seed $((42000 + i)) \
    --output-dir /tmp/myslide-assets/ --region us-east-1
done
```

### Background Removal for Object Isolation

When you need a specific object on a transparent background (for overlay on slides):

```bash
# Step 1: Generate the object image
python3 "$NOVA_CANVAS_SCRIPT" \
  --task text_to_image \
  --prompt "Glowing AI brain visualization, neural pathways, solid dark background" \
  --width 512 --height 512 \
  --output-dir /tmp/myslide-assets/ --region us-east-1

# Step 2: Remove background
python3 "$NOVA_CANVAS_SCRIPT" \
  --task background_removal \
  --image /tmp/myslide-assets/text_to_image_1.png \
  --output-dir /tmp/myslide-assets/ --region us-east-1

# Result: transparent PNG ready for slide overlay
```

### Image as Card Grid Thumbnails

For Multi-Card Grid slides, generate matching set of small illustrations:

```bash
TOPICS=("orchestration workflow" "knowledge database" "safety guardrail" "code execution")
SEED_BASE=55000

for i in "${!TOPICS[@]}"; do
  python3 "$NOVA_CANVAS_SCRIPT" \
    --task text_to_image \
    --prompt "Minimalist ${TOPICS[$i]} icon visualization, glowing on dark background, simple centered composition, professional 3D render" \
    --negative-text "text, busy, cluttered, bright background" \
    --width 384 --height 384 --quality standard \
    --seed $((SEED_BASE + i)) \
    --output-dir /tmp/myslide-assets/ --region us-east-1
done
```

## PptxGenJS Embedding Patterns

### Base64 Embedding (Recommended)

```javascript
const fs = require('fs');

function loadImageBase64(filePath) {
  const data = fs.readFileSync(filePath);
  return 'image/png;base64,' + data.toString('base64');
}

// Usage
const heroBase64 = loadImageBase64('/tmp/myslide-assets/text_to_image_1.png');
slide.addImage({ data: heroBase64, x: 5.0, y: 0, w: 8.33, h: 7.5 });
```

### Aspect Ratio Preservation

```javascript
function fitImage(origW, origH, maxW, maxH) {
  const scale = Math.min(maxW / origW, maxH / origH);
  return {
    w: origW * scale,
    h: origH * scale,
    x: (13.33 - origW * scale) / 2  // center horizontally
  };
}

// For 1280x720 image in 8x5 area:
const dims = fitImage(1280, 720, 8, 5);
slide.addImage({ data: heroBase64, x: dims.x, y: 1.5, w: dims.w, h: dims.h });
```

### Dark Overlay for Text on Images

```javascript
// Gradient-like overlay: stack multiple rectangles with decreasing transparency
// Left side darker (for text), right side lighter (show image)
function addGradientOverlay(slide, direction = 'left-to-right') {
  if (direction === 'left-to-right') {
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 5.0, h: 7.5,
      fill: { color: "0A0E14", transparency: 5 }
    });
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 4.0, y: 0, w: 3.0, h: 7.5,
      fill: { color: "0A0E14", transparency: 40 }
    });
  } else {
    // Full uniform overlay
    slide.addShape(pres.shapes.RECTANGLE, {
      x: 0, y: 0, w: 13.33, h: 7.5,
      fill: { color: "000000", transparency: 50 }
    });
  }
}
```

## Decision Flowchart

When deciding whether to use nova-canvas for a slide:

1. Does the slide need a **visual element** beyond text and shapes? -> If NO, skip
2. Can the visual be expressed as an **AWS architecture diagram**? -> Use SVG/aws-diagram
3. Can the visual be expressed as a **simple geometric shape or icon**? -> Use PptxGenJS shapes
4. Does the visual need a **conceptual illustration, photo, or atmosphere**? -> Use nova-canvas
5. Is the visual a **recurring pattern** (e.g., background)? -> Generate once, reuse with seed

## Cost Optimization

- Generate backgrounds once per deck, not per slide
- Reuse the same hero image across related sections
- Use `standard` quality (not `premium`) for slide images -- they compress well in PPTX
- Small illustrations (384x384, 512x512) cost less than full-slide images
- Save seeds for reproducibility -- regeneration is free if you have the same seed
