"""Generate gauge/donut chart images for the MBR slides."""

import math
from PIL import Image, ImageDraw
from io import BytesIO


def make_gauge_png(pct: float, size: int = 300, line_width: int = 28) -> bytes:
    """Create a donut/ring gauge chart as PNG bytes.

    Args:
        pct: Value as decimal (0.0 to 1.0+). Capped at 1.0 for display.
        size: Image size in pixels (square).
        line_width: Width of the ring stroke.

    Returns:
        PNG image bytes.
    """
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Colors matching Moxie design
    purple = (77, 23, 81)  # #4D1751
    track_color = (210, 203, 196)  # light cream/gray track

    margin = line_width // 2 + 4
    bbox = [margin, margin, size - margin, size - margin]

    # Draw track (full circle)
    draw.arc(bbox, 0, 360, fill=track_color, width=line_width)

    # Draw filled arc (start from top = -90 degrees)
    display_pct = min(pct, 1.0)
    if display_pct > 0:
        sweep = display_pct * 360
        start_angle = -90
        end_angle = start_angle + sweep
        draw.arc(bbox, start_angle, end_angle, fill=purple, width=line_width)

        # Draw rounded end caps
        cx, cy = size / 2, size / 2
        r = (size - 2 * margin) / 2
        cap_r = line_width / 2

        # Start cap (top center)
        sx = cx + r * math.cos(math.radians(start_angle))
        sy = cy + r * math.sin(math.radians(start_angle))
        draw.ellipse([sx - cap_r, sy - cap_r, sx + cap_r, sy + cap_r], fill=purple)

        # End cap
        ex = cx + r * math.cos(math.radians(end_angle))
        ey = cy + r * math.sin(math.radians(end_angle))
        draw.ellipse([ex - cap_r, ey - cap_r, ex + cap_r, ey + cap_r], fill=purple)

        # Also add cap at track end if not full
        if display_pct < 1.0:
            # Track start cap (at 12 o'clock, since we draw track full)
            # Just add a cap where the purple arc ends to separate it
            pass

    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
