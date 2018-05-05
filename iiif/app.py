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
    r"(?P<region>(full|square|(pct:)?\d+,\d+,\d+,\d+))/"
    r"(?P<size>(full|max|\d+,|,\d+|pct:\d+|!\d+,\d+|\d+,\d+))/"
    r"(?P<rotation>!?(0|90|180|270))/"
    r"(?P<quality>(default|color|gray|bitonal|\d+))\."
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


class AsyncDownload:
    def __init__(self, url):
        self.url = URL(url)

    def __enter__(self):
        raise TypeError("Use async instead")

    def __exit__(self, *exc_details):
        pass

    async def __aenter__(self):
        self.session = ClientSession()
        self.response = await self.session._request("GET", self.url)
        return self.response

    async def __aexit__(self, *exc_details):
        await self.response.release()
        await self.session.close()


@aiohttp_jinja2.template('index.html')
async def index(request: Request):
    return {}


async def image_information(request: Request, *, identifier: str):
    async with AsyncDownload(identifier) as resp:
        resp.raise_for_status()

        # Via pyvips
        image = pyvips.Image.new_from_buffer(await resp.read(), "")

        return json_response({
            "context": "http://iiif.io/api/image/2/context.json",
            "id": f"{request.url.scheme}://{request.url.host}:{request.url.port}/{identifier}",
            "type": "iiif:Image",
            "protocol": "http://iiif.io/api.image",
            "width": image.width,
            "height": image.height,
            "profile": [
                "http://iiif.io/api/image/2/level2.json",
                {
                    "context": "http://iiif.io/api/image/2/context.json",
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
    mirroring = rotation.startswith("!")
    if mirroring:
        rotation = rotation[1:]
    rotation = int(rotation)

    async with AsyncDownload(identifier) as resp:
        resp.raise_for_status()

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

        return Response(
            body=buf,
            headers={"Content-Type": _content_types[format]})

        # XXX Do nothing if pyvips should be involved
        sresp = StreamResponse(
            headers={"Content-Type": _content_types[format]})
        await sresp.prepare(request)

        blob = await resp.content.read()
        while blob:
            try:
                await sresp.write(blob)
                await sresp.drain()
            except Exception as e:
                break
            blob = await resp.content.read()

        return sresp


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

    aiohttp_jinja2.setup(app, loader=jinja2.PackageLoader('iiif', 'templates'))

    return app
