# AIO-IIIF

An in-progress IIIF server written in Python using `asyncio`, [`aiohttp`](https://github.com/aio-libs/aiohttp) and [`pyvips`](https://github.com/jcupitt/pyvips).

## Prerequisites

- `libvips`

## Setup

```
$ pipenv --site-packages
$ pipenv install
```

## Running

```
$ pipenv shell
(aio-iiif) $ python -m iiif
```

And then visit <http://localhost:8080>.
