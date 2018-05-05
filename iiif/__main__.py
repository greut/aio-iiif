from aiohttp.web import run_app

from .app import make_app


def main():
    app = make_app()
    run_app(app)


if __name__ == "__main__":
    main()
