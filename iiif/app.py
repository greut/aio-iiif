import re

import aiohttp_jinja2
import jinja2
import pyvips
from yarl import URL
from aiohttp import ClientSession, ClientRequest
from aiohttp.web import (Application, HTTPBadRequest, Request, Response,
        StreamResponse, json_response, get)

# Missing rotation: arbitrary angle
# Missing quality: bitonal (B_W) is gray still
# Extra quality: 0-100 for JPEG quality, default to 75
# Missing format: pdf
_re_image_request = re.compile(
    r"(?P<identifier>.+)/"
    r"(?P<region>(full|square|\d+,\d+,\d+,\d+|pct:\d+(\.\d*)?,\d+(\.\d*)?,\d+(\.\d*)?,\d+(\.\d*)?))/"
    r"(?P<size>(full|max|\d+,|,\d+|pct:\d+(\.\d*)?|!\d+,\d+|\d+,\d+))/"
    r"(?P<rotation>!?(0|90|180|270))/"
    r"(?P<quality>(default|native|color|gray|bitonal|\d+))\."
    r"(?P<format>(jpg|png|tif|gif|jp2|webp))$")

_re_image_information = re.compile(r"(?P<identifier>.+)/info.json")

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

def info(body):
    image = pyvips.Image.new_from_buffer(body, "")

    return (image.width, image.height)


def resize(body, region, size, rotation, quality, format):
    image = pyvips.Image.new_from_buffer(body, "")

    mirroring = rotation.startswith("!")
    if mirroring:
        rotation = rotation[1:]
    rotation = int(rotation)

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
            klass = int
            if pct:
                klass = float
                region = region[4:]
            l, t, w, h = (klass(x) for x in region.split(",", 4))
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
            pct = float(size[4:])
            if 0 < pct <= 100:
                image = image.shrink(100. / pct, 100. / pct)
            else:
                return HTTPBadRequest()
        else:
            width, height = (image.width, image.height)
            confined = False
            if size.startswith("!"):
                confined = True
                size = size[1:]
            w, h = (int(x) if x.isdigit() else 0
                    for x in size.split(",", 2))
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
                image = image.shrink(wshrink, wshrink)
            else:
                image = image.shrink(hshrink, hshrink)

    if mirroring:
        image = image.fliphor()

    if rotation > 0:
        image = image.rot(f"d{rotation}")

    colourspace = _colourspaces.get(quality)
    if colourspace:
        image = image.colourspace(colourspace)

    q = 75
    if quality.isdigit():
        q = max(1, min(100, int(quality)))

    property = ""
    if format == "jpg":
        property = f"[Q={q}]"

    return image.write_to_buffer(f'.{format}{property}')


async def image_information(request: Request, *, identifier: str):
    async with ClientSession() as session:
        async with session.get(identifier) as resp:
            resp.raise_for_status()
            body = await resp.read()

    width, height = await request.loop.run_in_executor(
            None,
            info, body)
    return json_response({
        "@context": "http://iiif.io/api/image/2/context.json",
        "@id": f"{request.url.scheme}://{request.url.host}:{request.url.port}/{identifier}",
        "type": "iiif:Image",
        "protocol": "http://iiif.io/api/image",
        "tiles": [{
            "scaleFactors": [1,2,4,8,16,32],
            "width": 1024,
        }],
        "width": width,
        "height": height,
        "profile": [
            "http://iiif.io/api/image/2/level2.json",
            {
                "@context": "http://iiif.io/api/image/2/context.json",
                "type": "iiif:ImageProfile",
                "formats": ["jpg", "png", "tif", "webp"],
                "qualities": ["default", "gray"],
                "supports": [
                    "jsonldMediaType",
                    "mirroring",
                    "regionByPct",
                    "regionByPx",
                    "regionSquare",
                    "rotationBy90s",
                    "sizeByConfinedWh",
                    "sizeByDistortedWh",
                    "sizeByH",
                    "sizeByPct",
                    "sizeByW",
                    "sizeByWh",
                ]
            }
        ]
    })

async def image_request(request: Request, *, identifier: str, region: str, size: str, rotation: str, quality: str, format: str):

    async with ClientSession() as session:
        async with session.get(identifier) as resp:
            resp.raise_for_status()
            body = await resp.read()

    resp = await request.loop.run_in_executor(
            None,
            resize, body, region, size, rotation, quality, format)

    return Response(
        body=resp,
        headers={"Content-Type": _content_types[format]})

async def image(request: Request):
    query = request.match_info.get('query')
    match = _re_image_request.match(query)
    if match:
        return await image_request(request, **match.groupdict())

    match = _re_image_information.match(query)
    if match:
        return await image_information(request, **match.groupdict())

    return HTTPBadRequest()


def make_app():
    app = Application()
    app.add_routes([get('/', index), get(r'/{query:https?:/.+}', image)])
    app.router.add_static('/', path='static')

    aiohttp_jinja2.setup(app, loader=jinja2.PackageLoader('iiif', 'templates'))

    return app
