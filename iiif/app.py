import re

import aiohttp_jinja2
import jinja2
import pyvips
from aiohttp import ClientSession
from aiohttp.web import (Application, HTTPBadRequest, Request, StreamResponse,
                         get)

# Missing rotation: arbitrary angle
# Missing quality: bitonal (B_W) is gray still
# Extra quality: 0-100 for JPEG quality, default to 75
# Missing format: pdf
_re_url = re.compile(
    r"(?P<identifier>.+)/"
    r"(?P<region>(full|square|(pct:)?\d+,\d+,\d+,\d+))/"
    r"(?P<size>(full|max|\d+,|,\d+|pct:\d+|!\d+,\d+|\d+,\d+))/"
    r"(?P<mirroring>!?)(?P<rotation>(0|90|180|270))/"
    r"(?P<quality>(default|color|gray|bitonal|\d+))\."
    r"(?P<format>(jpg|png|tif|gif|jp2|webp))$")

_colourspaces = {
    "gray": pyvips.Interpretation.GREY16,
    "bitonal": pyvips.Interpretation.B_W,
}

_content_types = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "tif": "image/tiff",
    "gif": "image/gif",
    "pdf": "application/pdf",
    "jp2": "image/jp2",
    "webp": "image/webp",
}


@aiohttp_jinja2.template('index.html')
async def index(request: Request):
    return {}


async def image(request: Request):
    query = request.match_info.get('query')
    match = _re_url.match(query)

    if not match:
        return HTTPBadRequest()

    region = match.group("region")
    size = match.group("size")
    identifier = match.group("identifier")
    mirroring = match.group("mirroring") == "!"
    rotation = int(match.group("rotation"))
    quality = match.group("quality")
    format = match.group("format")

    async with ClientSession() as session:
        async with session.get(identifier) as resp:
            resp.raise_for_status()
            sresp = StreamResponse(
                status=resp.status,
                reason=resp.reason,
                headers={"Content-Type": _content_types[format]})
            await sresp.prepare(request)

            # Via pyvips
            image = pyvips.Image.new_from_buffer(await resp.read(), "")

            if region != "full":
                width, height = (image.width, image.height)
                l, t, w, h = 0, 0, width, height
                if region == "square":
                    if width < height:
                        h = width
                        t = (height - width) / 2
                    else:
                        w = height
                        l = (width - height) / 2
                else:
                    pct = region.startswith("pct:")
                    if pct:
                        region = region[4:]
                    l, t, w, h = (int(x) for x in region.split(",", 4))
                    if pct:
                        l = (l * width) / 100
                        t = (t * height) / 100
                        w = (w * width) / 100
                        h = (h * height) / 100

                image = image.extract_area(l, t, w, h)

            # XXX allow usage of shrink on load
            # XXX use affine/reduce/resize
            if size not in ("full", "max"):
                if size.startswith("pct:"):
                    pct = int(size[4:])
                    if 0 < pct <= 100:
                        image = image.resize(100. / pct)
                    else:
                        return HTTPBadRequest()
                else:
                    width, height = (image.width, image.height)
                    confined = False
                    if size.startswith("!"):
                        confined = True
                        size = size[1:]
                    w, h = (int(x) for x in size.split(",", 2))
                    if w:
                        wshrink = max(1., width / float(w))
                    if h:
                        hshrink = max(1., height / float(h))

                    if w and h:
                        if confined:
                            image = image.shrink(wshrink, hshrink)
                        else:
                            image = image.resize(min(1 / wshrink, 1 / hshrink))
                    elif w:
                        image = image.shrinkh(wshrink)
                    else:
                        image = image.shrinkv(hshrink)

            if mirroring:
                image = image.fliphor()

            if rotation > 0:
                image = image.rot(f"d{rotation}")

            colourspace = _colourspaces.get(quality)
            if colourspace:
                image = image.colourspace(colourspace)

            property = ""
            q = 75
            if int(quality):
                q = max(1, min(100, int(quality)))

            if format == "jpg":
                property = f"[Q={quality}]"
            buf = image.write_to_buffer(f'.{format}{property}')

            await sresp.write(buf)
            return sresp

            # XXX Do nothing if pyvips should be involved
            blob = await resp.content.read()
            while blob:
                try:
                    await sresp.write(blob)
                    await sresp.drain()
                except Exception as e:
                    break
                blob = await resp.content.read()

            return sresp


def make_app():
    app = Application()
    app.add_routes([get('/', index), get(r'/{query:https?:/.+}', image)])

    aiohttp_jinja2.setup(app, loader=jinja2.PackageLoader('iiif', 'templates'))

    return app
