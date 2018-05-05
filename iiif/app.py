import aiohttp_jinja2
import jinja2
import pyvips
from aiohttp import ClientSession
from aiohttp.web import Application, Request, StreamResponse, get

@aiohttp_jinja2.template('index.html')
async def index(request: Request):
    return {}

async def image(request: Request):
    identifier = request.match_info.get('identifier')
    async with ClientSession() as session:
        async with session.get(identifier) as resp:
            resp.raise_for_status()
            sresp = StreamResponse(status=resp.status,
                                   reason=resp.reason,
                                   headers={"Content-Type": resp.headers["Content-Type"]})
            await sresp.prepare(request)
            # Via pyvips
            image = pyvips.Image.new_from_buffer(await resp.read(), "")
            buf = image.write_to_buffer('.jpg[Q=95]')
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
    app.add_routes([get('/', index),
        get(r'/{identifier:https?:/.+}', image)])

    aiohttp_jinja2.setup(
        app, loader=jinja2.PackageLoader('iiif', 'templates'))

    return app
