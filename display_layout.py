"""Validated logical layout and single-output HUB75 image packing (no GPIO imports)."""


def default_layout():
    return layout_grid(64, 32, 3, 2, 2)


def layout_grid(width, height, columns, rows, sides):
    return dict(width=width, height=height, columns=columns, rows=rows, sides=sides,
                panels=[dict(chain=s * columns * rows + y * columns + x + 1,
                             side=s + 1, x=x, y=y, rotation=0)
                        for s in range(sides) for y in range(rows) for x in range(columns)])


def validate_layout(value):
    if not isinstance(value, dict):
        raise ValueError('Choose a display layout.')
    limits = dict(width=(32, 128), height=(16, 64), columns=(1, 16), rows=(1, 16), sides=(1, 2))
    out = {}
    for key, (low, high) in limits.items():
        n = value.get(key)
        if type(n) is not int or not low <= n <= high:
            raise ValueError('Enter valid panel dimensions, rows, columns and sides.')
        out[key] = n
    if out['width'] % 8 or out['height'] not in (16, 32, 64):
        raise ValueError('Panel width must be a multiple of 8; height must be 16, 32 or 64.')
    count = out['columns'] * out['rows'] * out['sides']
    if count > 64:
        raise ValueError('Use no more than 64 panels on one output.')
    panels = value.get('panels')
    if not isinstance(panels, list) or len(panels) != count:
        raise ValueError('Assign every panel exactly once.')
    chains, positions, clean = set(), set(), []
    for p in panels:
        if not isinstance(p, dict) or any(type(p.get(k)) is not int for k in ('chain', 'side', 'x', 'y', 'rotation')):
            raise ValueError('Every panel needs a chain position, side, position and orientation.')
        c, s, x, y, r = (p[k] for k in ('chain', 'side', 'x', 'y', 'rotation'))
        if not (1 <= c <= count and 1 <= s <= out['sides'] and 0 <= x < out['columns'] and 0 <= y < out['rows']):
            raise ValueError('A panel assignment is outside the display.')
        if r not in (0, 90, 180, 270) or (r in (90, 270) and out['width'] != out['height']):
            raise ValueError('Use 0° or 180° for rectangular panels; square panels also support 90° and 270°.')
        if c in chains or (s, x, y) in positions:
            raise ValueError('Chain positions and display positions must be unique.')
        chains.add(c); positions.add((s, x, y))
        clean.append(dict(chain=c, side=s, x=x, y=y, rotation=r))
    out['panels'] = sorted(clean, key=lambda p: p['chain'])
    return out


def saved_layout(conf):
    if 'display_layout' in conf:
        return validate_layout(conf['display_layout'])
    # Preserve legacy configured geometry until the operator chooses a new layout.
    return validate_layout(layout_grid(conf.get('led_cols', 64), conf.get('led_rows', 64),
                                      conf.get('led_chain', 2), conf.get('led_parallel', 1), 1))


def pack_image(image, layout, test=False):
    from PIL import Image, ImageDraw
    w, h = layout['width'], layout['height']
    output = Image.new('RGB', (w * len(layout['panels']), h))
    for p in layout['panels']:
        if test:
            tile = Image.new('RGB', (w, h), (0, 0, 30))
            d = ImageDraw.Draw(tile)
            d.rectangle((0, 0, w-1, h-1), outline='white')
            d.text((3, 2), f"{p['chain']} S{p['side']}", fill='white')
            d.line((w//2, h-4, w//2, h//2), fill='yellow', width=2)
            d.polygon([(w//2, h//2-4), (w//2-4, h//2+2), (w//2+4, h//2+2)], fill='yellow')
            d.rectangle((1, 1, 3, 3), fill='red')
        else:
            x, y = p['x'] * w, p['y'] * h
            tile = image.crop((x, y, x+w, y+h)).convert('RGB')
        # Rotation describes the installed panel's clockwise orientation.
        tile = tile.rotate(p['rotation'], expand=False)
        output.paste(tile, ((p['chain']-1)*w, 0))
    return output
