#!/usr/bin/env python3
import io
import json
import os
import sys
import time
from collections import Iterable
from datetime import datetime, timedelta

import qrcode
from flask import Flask, g, make_response, render_template, request, send_file
from flask.ext.assets import Environment
from flask.ext.babel import gettext as _
from flask.ext.babel import Babel
from htmlmin import minify

from classes import Graph, Node, Position, Room, Router, UserPosition

LANGUAGES = {
    'en': 'English',
    'de': 'Deutsch'
}
short_base = 'c3nav.de/'

app = Flask('congress-route-planner')
assets = Environment(app)
babel = Babel(app)

if 'C3NAVPROJECT' in os.environ:
    project = os.environ['C3NAVPROJECT']
elif len(sys.argv) > 1:
    project = sys.argv[1]
else:
    print('Please specify project: run.py <project> or environment variable C3NAVPROJECT')
    sys.exit(1)

starttime = time.time()
graph = Graph(project, auto_connect=True, load_wifi=(not os.environ.get('ROUTEONLY')))
print('Graph loaded in %.3fs' % (time.time()-starttime))


@babel.localeselector
def get_locale():
    locale = 'en'  # request.accept_languages.best_match(LANGUAGES.keys())
    if request.cookies.get('lang') in LANGUAGES.keys():
        locale = request.cookies.get('lang')
    if request.args.get('lang') in LANGUAGES.keys():
        locale = request.args.get('lang')
    return locale


@app.before_request
def before_request():
    g.locale = get_locale()


@app.route('/', methods=['GET', 'POST'])
def main(origin=None, destination=None):
    if os.environ.get('WIFIONLY'):
        return ''

    src = request.args if request.method == 'GET' else request.form

    _('Sorry, an error occured =(')
    _('select origin…')
    _('select destination…')
    _('Edit Settings')
    _('swap')
    _('close')
    _('share')
    _('create shortcut')
    _('wifi positioning is currently not (yet) available')
    _('determining your position…')

    ctx = {
        'location_select': sorted(graph.selectable_locations.values(), key=lambda l: (0-l.priority, l.title)),
        'titles': {name: titles.get(get_locale(), name) for name, titles in graph.data['titles'].items()},
        'mobile_client': request.headers.get('User-Agent').startswith('c3navClient'),
        'fake_mobile_client': 'fakemobile' in request.args,
        'graph': graph
    }

    # Select origins

    origin = graph.get_selectable_location(src.get('o', origin))
    destination = graph.get_selectable_location(src.get('d', destination))
    ctx.update({'origin': origin, 'destination': destination})
    if request.method == 'POST':
        if origin is None:
            return 'missing origin'

        if destination is None:
            return 'missing destination'

    # Load Settings
    settingscookie = request.cookies.get('settings')
    cookie_settings = {}
    if settingscookie is not None:
        try:
            cookie_settings = json.loads(settingscookie)
        except:
            pass
        else:
            ctx['had_settings_cookie'] = True

    setsettings = {}
    for name, default_value in Router.default_settings.items():
        if not isinstance(default_value, str) and isinstance(default_value, Iterable):
            value = src.getlist(name)
            cookie_value = cookie_settings.get(name)
            if value or ('force-'+name) in src:
                setsettings[name] = value
            elif isinstance(cookie_value, list):
                setsettings[name] = cookie_value
        elif name in src:
            setsettings[name] = src.get(name)
        elif name in cookie_settings:
            cookie_value = cookie_settings.get(name)
            if (isinstance(cookie_value, Iterable) and isinstance(default_value, str) or
                    isinstance(cookie_value, int) and isinstance(default_value, int)):
                setsettings[name] = cookie_value

    router = Router(graph, setsettings)
    ctx['settings'] = router.settings

    settings_flat = sum([(sum([[(n, vv)] for vv in v], []) if isinstance(v, Iterable) else [(n, v)])
                         for n, v in router.settings.items()], [])
    ctx['settings_fields'] = [(n, v) for n, v in settings_flat if n in src]

    # parse what is avoided
    avoid = []
    for ctype in ('steps', 'stairs', 'escalators', 'elevators'):
        s = router.settings[ctype]
        if s == 'yes':
            continue
        else:
            avoid.append(ctype+{'no': '↕', 'up': '↓', 'down': '↑'}[s])
    for e in router.settings['e']:
        avoid.append(graph.titles.get(e, {}).get('en', e))
    ctx['avoid'] = avoid

    if request.method == 'GET':
        resp = make_response(minify(render_template('main.html', **ctx)))
        if 'lang' in request.cookies or 'lang' in request.args:
            resp.set_cookie('lang', g.locale, expires=datetime.now()+timedelta(days=30))
        return resp

    """
    Now lets route!
    """
    messages, route = router.get_route(origin, destination)
    if route is not None:
        route_description, has_avoided_ctypes = route.describe()
        if has_avoided_ctypes:
            messages.append(('warn', _('This route contains way types that you wanted to avoid '
                                       'because otherwise no route would be possible.')))
        total_duration = sum(rp['duration'] for rp in route_description)

        ctx.update({
            'routeparts': route_description,
            'origin_title': None if isinstance(route.points[0], Node) else route.points[0].title,
            'destination_title': None if isinstance(route.points[-1], Node) else route.points[-1].title,
            'total_distance': round(sum(rp['distance'] for rp in route_description)/100, 1),
            'total_duration': (int(total_duration/60), int(total_duration % 60)),
            'jsonfoo': json.dumps(route_description, indent=4)
        })

    ctx.update({
        'messages': messages,
        'isresult': True,
        'resultsonly': src.get('ajax') == '1'
    })

    resp = make_response(minify(render_template('main.html', **ctx)))
    if src.get('savesettings') == '1':
        resp.set_cookie('settings', json.dumps(router.settings),
                        expires=datetime.now()+timedelta(days=30))
    if 'lang' in request.cookies or 'lang' in request.args:
        resp.set_cookie('lang', g.locale, expires=datetime.now()+timedelta(days=30))
    return resp


@app.route('/qr/<path:path>')
def qr_code(path):
    if os.environ.get('WIFIONLY'):
        return ''

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(short_base+path)
    qr.make(fit=True)
    img = io.BytesIO()
    qr.make_image().save(img, 'PNG')
    img.seek(0)
    return send_file(img, mimetype='image/png')


@app.route('/mapdata/<name>')
def mapdata(name):
    if os.environ.get('WIFIONLY'):
        return ''

    location = graph.get_selectable_location(name)
    return render_template('mapdata.html', location=location, name=graph.name)


@app.route('/link/<path:path>')
def link_for_noscript(path):
    if os.environ.get('WIFIONLY'):
        return ''

    return render_template('link.html', path=path, short_base=short_base)


@app.route('/o<location>')
def short_origin(location):
    if os.environ.get('WIFIONLY'):
        return ''

    return main(origin=location)


@app.route('/d<location>')
def short_destination(location):
    if os.environ.get('WIFIONLY'):
        return ''

    return main(destination=location)


@app.route('/n<int:level>:<int:x>:<int:y>')
def get_location_title(level, x, y):
    if os.environ.get('WIFIONLY'):
        return ''

    pos = UserPosition(level, x, y)
    graph.connect_position(pos)
    return json.dumps({
        'name': '%d:%d:%d' % (level, x, y),
        'title': pos.title
    })


@app.route('/locate', methods=['POST'])
def locate():
    if os.environ.get('ROUTEONLY'):
        return ''

    result = graph.wifi.locate(json.loads(request.form.get('stations')))
    if result is not None:
        position, score, matched_stations = result
        result = {
            'name': position.name,
            'title': position.title,
            'level': position.level,
            'x': position.x,
            'y': position.y,
            'score': score,
            'known_stations': matched_stations
        }
    return json.dumps(result)


if 'gunicorn' not in os.environ.get('SERVER_SOFTWARE', ''):
    app.run(threaded=True, debug=('debug' in sys.argv),
            port=(4999 if os.environ.get('WIFIONLY') else 5000))
