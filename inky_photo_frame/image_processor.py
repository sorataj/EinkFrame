"""Image processing pipeline for e-ink display."""

import itertools
import logging
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps

from inky_photo_frame import config as _config
from inky_photo_frame.config import SPECTRA_PALETTE, WARMTH_BOOST_CONFIG

# Optional settings (add to config.py to override; defaults used otherwise)
# MAX_CROP_FRACTION: crop to fill only if <= this share of the photo would be lost,
#                    otherwise the whole photo is shown with borders. 0 = never crop.
# BORDER_COLOR: colour of the letterbox / pillarbox bars.
MAX_CROP_FRACTION = getattr(_config, 'MAX_CROP_FRACTION', 0.20)
BORDER_COLOR = getattr(_config, 'BORDER_COLOR', (255, 255, 255))
# Collage options (the border thickness/colour normally come from the web UI):
# COLLAGE_MARGIN / COLLAGE_GAP: white space (pixels) around / between photos.
# COLLAGE_MAX_CROP: photos fill their slot, cropping what does not fit. A photo that
#                   would lose more than this share is shown whole with white bars instead.
# COLLAGE_MIN_QUALITY: layouts are random, but a layout that keeps much less of the
#                   photos than the best one is skipped (0 = fully random, 1 = always best).
COLLAGE_MARGIN = getattr(_config, 'COLLAGE_MARGIN', 16)
COLLAGE_GAP = getattr(_config, 'COLLAGE_GAP', 16)
COLLAGE_BORDER_WIDTH = getattr(_config, 'COLLAGE_BORDER_WIDTH', 6)
COLLAGE_BORDER_COLOR = getattr(_config, 'COLLAGE_BORDER_COLOR', (0, 0, 0))
COLLAGE_MAX_CROP = getattr(_config, 'COLLAGE_MAX_CROP', 0.75)
COLLAGE_MIN_QUALITY = getattr(_config, 'COLLAGE_MIN_QUALITY', 0.7)


def _apply_spectra_palette(img):
    """
    Map image to calibrated Spectra 6-color palette using quantization.
    This gives more accurate colors by using the ACTUAL RGB values the e-ink can produce.
    """
    from PIL import ImageEnhance

    # Step 1: Pre-process for better palette mapping
    # Boost contrast and saturation slightly to compensate for e-ink limitations
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.2)  # +20% contrast

    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1.3)  # +30% saturation before mapping

    # Step 2: Create palette image with the 6 calibrated colors
    palette_colors = list(SPECTRA_PALETTE.values())

    # Create a palette image (must be 'P' mode)
    # PIL palette format: flat list of RGB values
    palette_data = []
    for color in palette_colors:
        palette_data.extend(color)

    # Pad palette to 256 colors (PIL requirement)
    while len(palette_data) < 768:  # 256 colors * 3 channels
        palette_data.extend([0, 0, 0])

    # Create palette image
    palette_img = Image.new('P', (1, 1))
    palette_img.putpalette(palette_data)

    # Step 3: Quantize image to our 6-color palette
    # Using Floyd-Steinberg dithering for smoother transitions
    img = img.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)

    # Convert back to RGB for display
    img = img.convert('RGB')

    return img


def _apply_warmth_boost(img):
    """
    Apply aggressive warmth boost via RGB channel adjustments.
    Boosts red, reduces blue to add warmth to skin tones.
    """
    from PIL import ImageEnhance

    # Step 1: Increase brightness
    brightness = ImageEnhance.Brightness(img)
    img = brightness.enhance(WARMTH_BOOST_CONFIG['brightness'])

    # Step 2: Channel balancing for warmth
    r, g, b = img.split()

    r_enhancer = ImageEnhance.Brightness(r)
    r = r_enhancer.enhance(WARMTH_BOOST_CONFIG['red_boost'])

    g_enhancer = ImageEnhance.Brightness(g)
    g = g_enhancer.enhance(WARMTH_BOOST_CONFIG['green_reduce'])

    b_enhancer = ImageEnhance.Brightness(b)
    b = b_enhancer.enhance(WARMTH_BOOST_CONFIG['blue_reduce'])

    img = Image.merge('RGB', (r, g, b))

    return img


def _fit(img, width, height, max_crop, bg):
    """Fit img to width x height. Returns (image, (x, y, w, h)) where the box is
    the area the photo itself covers (the whole image when it was cropped to fill)."""
    iw, ih = img.size
    fill = max(width / iw, height / ih)   # scale that covers the screen
    fit = min(width / iw, height / ih)    # scale that fits inside the screen

    if 1 - fit / fill <= max_crop:
        nw, nh = max(width, round(iw * fill)), max(height, round(ih * fill))
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        left, top = (nw - width) // 2, (nh - height) // 2
        return img.crop((left, top, left + width, top + height)), (0, 0, width, height)

    nw, nh = min(width, round(iw * fit)), min(height, round(ih * fit))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (width, height), bg)
    x, y = (width - nw) // 2, (height - nh) // 2
    canvas.paste(img, (x, y))
    return canvas, (x, y, nw, nh)


def autoformat(img, width, height, max_crop=MAX_CROP_FRACTION, bg=BORDER_COLOR):
    """Fit img to width x height.

    If cropping to fill would lose <= max_crop of the photo, centre-crop it.
    Otherwise scale it to fit entirely and add borders (letterbox/pillarbox).
    """
    return _fit(img, width, height, max_crop, bg)[0]


def _apply_color_mode(img, color_mode, is_spectra):
    """Apply the selected colour processing to a finished, display-sized image."""
    if color_mode == 'pimoroni':
        # Pimoroni default: NO processing, let Inky library handle everything
        # This matches official Pimoroni behavior (just saturation=0.5 in set_image)
        logging.debug('Applied Pimoroni default (no processing)')

    elif color_mode == 'spectra_palette' and is_spectra:
        # Spectra palette mode: map to calibrated 6-color palette
        img = _apply_spectra_palette(img)
        logging.info('✨ Applied calibrated Spectra 6-color palette mapping')

    elif color_mode == 'warmth_boost' and is_spectra:
        # Aggressive warmth boost mode
        img = _apply_warmth_boost(img)
        logging.info('🔥 Applied aggressive warmth boost')

    else:
        # Fallback: no processing (same as pimoroni mode)
        logging.debug('No color processing applied (fallback)')

    return img


def process_image(image_path, width, height, color_mode, is_spectra, rotation=0):
    """Process image for e-ink display with smart cropping and color correction.

    Args:
        image_path: str or Path to the image file
        width: int, display width in pixels
        height: int, display height in pixels
        color_mode: str, one of 'pimoroni', 'spectra_palette', 'warmth_boost'
        is_spectra: bool, whether the display is a Spectra model
        rotation: 0, 90, 180 or 270. For a frame mounted on its side (or upside down)
            the picture is laid out the right way up and then turned to match the panel.
    """
    logging.info(f'Processing: {Path(image_path).name}')
    if rotation in (90, 270):
        width, height = height, width   # lay the picture out in the upright orientation
    img = Image.open(image_path)
    # Let the JPEG decoder pre-shrink huge photos (fast + low RAM on a Pi 3B+)
    img.draft('RGB', (width * 2, height * 2))
    # Apply phone/camera rotation so portrait photos are not sideways
    img = ImageOps.exif_transpose(img)

    # No color profile manipulation - let PIL handle it natively

    # Convert to RGB
    if img.mode != 'RGB':
        if img.mode == 'RGBA':
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        else:
            img = img.convert('RGB')

    # Fit to display (crop if loss is small, otherwise letterbox/pillarbox)
    img = autoformat(img, width, height)

    img = _apply_color_mode(img, color_mode, is_spectra)
    if rotation:
        img = img.rotate(rotation, expand=True)   # exact for multiples of 90
    return img


# --------------------------------------------------------------------------
# Collage: 2 or 3 photos on one screen
# --------------------------------------------------------------------------

def _to_rgb(img):
    """Convert to RGB, flattening any transparency onto white."""
    if img.mode == 'RGB':
        return img
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGBA')
        flat = Image.new('RGB', img.size, (255, 255, 255))
        flat.paste(img, mask=img.getchannel('A'))
        return flat
    return img.convert('RGB')


def _oriented_size(img):
    """Size of a photo as it will look after applying its EXIF rotation."""
    w, h = img.size
    if img.getexif().get(0x0112, 1) in (5, 6, 7, 8):
        w, h = h, w
    return w, h


def _layouts(n, width, height, margin, gap):
    """All candidate slot arrangements for n photos, as lists of (x, y, w, h)."""
    x, y = margin, margin
    w, h = width - 2 * margin, height - 2 * margin

    def side_by_side(parts, rx, ry, rw, rh):
        sw = (rw - gap * (parts - 1)) // parts
        return [(rx + i * (sw + gap), ry, sw, rh) for i in range(parts)]

    def stacked(parts, rx, ry, rw, rh):
        sh = (rh - gap * (parts - 1)) // parts
        return [(rx, ry + i * (sh + gap), rw, sh) for i in range(parts)]

    if n == 2:
        return [side_by_side(2, x, y, w, h), stacked(2, x, y, w, h)]

    half_w, half_h = (w - gap) // 2, (h - gap) // 2
    return [
        side_by_side(3, x, y, w, h),
        stacked(3, x, y, w, h),
        [(x, y, half_w, h)] + stacked(2, x + half_w + gap, y, w - half_w - gap, h),      # big left
        [(x + w - half_w, y, half_w, h)] + stacked(2, x, y, w - half_w - gap, h),        # big right
        [(x, y, w, half_h)] + side_by_side(2, x, y + half_h + gap, w, h - half_h - gap),  # big top
        [(x, y + h - half_h, w, half_h)] + side_by_side(2, x, y, w, h - half_h - gap),    # big bottom
    ]


def _arrange(layout, sizes, rng):
    """Put each photo in the slot that keeps most of it (e.g. portraits in tall slots).

    Photos fill their slot, so a slot's quality is the share of the photo that stays
    visible (1.0 = nothing cropped). Returns (score, order); the score weights each
    slot by its area, and exact ties are broken randomly."""
    orders = list(itertools.permutations(range(len(sizes))))
    rng.shuffle(orders)
    best = None
    for order in orders:
        score = 0.0
        for (_, _, sw, sh), i in zip(layout, order):
            iw, ih = sizes[i]
            a, b = sw / iw, sh / ih
            score += sw * sh * (min(a, b) / max(a, b))
        if best is None or score > best[0] + 1:
            best = (score, order)
    return best


def _pick_layout(sizes, width, height, rng):
    """Pick a random layout, skipping ones that would crop the photos too much."""
    options = []
    for layout in _layouts(len(sizes), width, height, COLLAGE_MARGIN, COLLAGE_GAP):
        score, order = _arrange(layout, sizes, rng)
        options.append((score, layout, order))
    top = max(o[0] for o in options)
    good = [o for o in options if o[0] >= COLLAGE_MIN_QUALITY * top]
    _, layout, order = rng.choice(good)
    return layout, order


def process_collage(image_paths, width, height, color_mode, is_spectra, seed=None,
                    rotation=0, border_width=None, border_color=None):
    """Compose 2-3 photos onto one display-sized image, then apply colour processing.

    The layout is picked at random; the same `seed` always gives the same layout,
    so a redraw of the same screen looks identical. Photos fill their slots (what
    does not fit is cropped) so the screen is used as fully as possible.
    border_width / border_color: frame drawn around each photo (width 0 = none).
    rotation: 0, 90, 180 or 270, as for process_image.
    Falls back to a normal single photo if only one of the photos can be read.
    """
    if border_width is None:
        border_width = COLLAGE_BORDER_WIDTH
    if border_color is None:
        border_color = COLLAGE_BORDER_COLOR

    opened, paths = [], []
    for path in image_paths[:3]:
        try:
            img = Image.open(path)
            _oriented_size(img)
            opened.append(img)
            paths.append(path)
        except Exception as e:
            logging.warning(f'Collage: skipping unreadable photo {Path(path).name}: {e}')
    if not opened:
        raise ValueError('Collage: none of the photos could be read')
    if len(opened) == 1:
        opened[0].close()
        return process_image(paths[0], width, height, color_mode, is_spectra, rotation)

    logging.info('Collage: ' + ', '.join(Path(p).name for p in paths))
    cw, ch = (height, width) if rotation in (90, 270) else (width, height)   # upright canvas
    rng = random.Random(seed)
    try:
        sizes = [_oriented_size(im) for im in opened]
        layout, order = _pick_layout(sizes, cw, ch, rng)
        canvas = Image.new('RGB', (cw, ch), BORDER_COLOR)
        draw = ImageDraw.Draw(canvas)
        for (sx, sy, sw, sh), i in zip(layout, order):
            img = opened[i]
            img.draft('RGB', (max(sw, sh), max(sw, sh)))   # fast, low-RAM decode
            img = _to_rgb(ImageOps.exif_transpose(img))
            tile, (bx, by, bw, bh) = _fit(img, sw, sh, COLLAGE_MAX_CROP, BORDER_COLOR)
            canvas.paste(tile, (sx, sy))
            if border_width > 0:
                draw.rectangle((sx + bx, sy + by, sx + bx + bw - 1, sy + by + bh - 1),
                               outline=border_color, width=border_width)
    finally:
        for im in opened:
            im.close()

    canvas = _apply_color_mode(canvas, color_mode, is_spectra)
    if rotation:
        canvas = canvas.rotate(rotation, expand=True)
    return canvas
