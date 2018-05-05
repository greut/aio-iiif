import re

import aiohttp_jinja2
import jinja2
import pyvips
from aiohttp import ClientSession
from aiohttp.web import (Application, HTTPBadRequest, Request, StreamResponse,
                         get)

# Missing quality: bitonal (B_W) still is gray
# Missing format: pdf
_re_url = re.compile(r"(?P<identifier>.+)/"
                     r"(?P<quality>(default|color|gray|bitonal))\."
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

    identifier = match.group("identifier")
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

            colourspace = _colourspaces.get(quality)
            if colourspace:
                image = image.colourspace(colourspace)

            property = ""
            if format == "jpg":
                property = "[Q=95]"
            buf = image.write_to_buffer(f'.{format}{property}')

            await sresp.write(buf)
            return sresp

            # Do nothing
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
