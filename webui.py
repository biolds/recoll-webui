#!env python
#{{{ imports
import os
import bottle
import time
import sys

try:
    from recoll import recoll
    from recoll import rclextract
    hasrclextract = True
except:
    import recoll
    hasrclextract = False
    
import datetime
import glob
import hashlib
import json
import csv
import StringIO
import ConfigParser
import string
import shlex
import urllib
from pprint import pprint
#}}}
#{{{ settings
# recoll settings
RECOLL_CONFS = [ '$RECOLL_CONFDIR', '~/.recoll', '/usr/share/recoll/examples' ]

# settings defaults
DEFAULTS = {
    'context': 30,
    'stem': 1,
    'timefmt': '%c',
    'dirdepth': 3,
    'maxchars': 500,
    'maxresults': 0,
    'perpage': 25,
}

# sort fields/labels
SORTS = [
    ("relevancyrating", "Relevancy"),
    ("mtime", "Date",),
    ("url", "Path"),
    ("filename", "Filename"),
    ("fbytes", "Size"),
    ("author", "Author"),
]

# doc fields
FIELDS = [
    # exposed by python api
    'ipath',
    'filename',
    'title',
    'author',
    'fbytes',
    'dbytes',
    'size',
    'fmtime',
    'dmtime',
    'mtime',
    'mtype',
    'origcharset',
    'sig',
    'relevancyrating',
    'url',
    'abstract',
    'keywords',
    # calculated
    'time',
    'snippet',
    'label',
]
#}}}
#{{{  functions
#{{{  helpers
def select(ls, invalid=[None]):
    for value in ls:
        if value not in invalid:
            return value

def timestr(secs, fmt):
    t = time.gmtime(int(secs))
    return time.strftime(fmt, t)

def normalise_filename(fn):
    valid_chars = "_-%s%s" % (string.ascii_letters, string.digits)
    out = ""
    for i in range(0,len(fn)):
        if fn[i] in valid_chars:
            out += fn[i]
        else:
            out += "_"
    return out
#}}}
#{{{ recoll_get_config
def recoll_get_config():
    # find recoll.conf
    for d in RECOLL_CONFS:
        d = os.path.expanduser(d)
        d = os.path.expandvars(d)
        if os.path.isdir(d):
            confdir = d
            break
    # read recoll.conf
    rc_ini_str = '[main]\n' + open(confdir + '/recoll.conf', 'r').read().replace('\\\n', '')
    rc_ini_fp = StringIO.StringIO(rc_ini_str)
    rc_ini = ConfigParser.RawConfigParser()
    rc_ini.readfp(rc_ini_fp)
    # parse recoll.conf
    rc = {}
    for s in rc_ini.sections():
        rc[s] = {}
        for k, v in rc_ini.items(s):
            rc[s][k] = v
    return confdir, rc
#}}}
#{{{ get_config
def get_config():
    config = {}
    # get useful things from recoll.conf
    config['confdir'], rc = recoll_get_config()
    config['dirs'] = shlex.split(rc['main']['topdirs'])
    # get config from cookies or defaults
    for k, v in DEFAULTS.items():
        value = select([bottle.request.get_cookie(k), v])
        config[k] = type(v)(value)
    # get mountpoints
    config['mounts'] = {}
    for d in config['dirs']:
        name = 'mount_%s' % urllib.quote(d,'')
        config['mounts'][d] = select([bottle.request.get_cookie(name), 'file://%s' % d], [None, ''])
    return config
#}}}
#{{{ get_dirs
def get_dirs(tops, depth):
    v = []
    for top in tops:
        dirs = [top]
        for d in range(1, depth+1):
            dirs = dirs + glob.glob(top + '/*' * d)
        dirs = filter(lambda f: os.path.isdir(f), dirs)
        top_path = top.rsplit('/', 1)[0]
        dirs = [w.replace(top_path+'/', '') for w in dirs]
        v = v + dirs
    return ['<all>'] + v
#}}}
#{{{ get_query
def get_query():
    query = {
        'query': select([bottle.request.query.get('query'), '']),
        'before': select([bottle.request.query.get('before'), '']),
        'after': select([bottle.request.query.get('after'), '']),
        'dir': select([bottle.request.query.get('dir'), '', '<all>'], [None, '']),
        'sort': select([bottle.request.query.get('sort'), SORTS[0][0]]),
        'ascending': int(select([bottle.request.query.get('ascending'), 0])),
        'page': int(select([bottle.request.query.get('page'), 0])),
    }
    return query
#}}}
#{{{ query_to_recoll_string
def query_to_recoll_string(q):
    qs = q['query'].decode('utf-8')
    if len(q['after']) > 0 or len(q['before']) > 0:
        qs += " date:%s/%s" % (q['after'], q['before'])
    if q['dir'] != '<all>':
        qs += " dir:\"%s\" " % q['dir']
    return qs
#}}}
#{{{ recoll_initsearch
def recoll_initsearch(q):
    config = get_config()
    db = recoll.connect(config['confdir'])
    db.setAbstractParams(config['maxchars'], config['context'])
    query = db.query()
    query.sortby(q['sort'], q['ascending'])
    try:
        qs = query_to_recoll_string(q)
        query.execute(qs, config['stem'])
    except:
        pass
    return query
#}}}
#{{{ recoll_search
def recoll_search(q):
    config = get_config()
    tstart = datetime.datetime.now()
    results = []
    query = recoll_initsearch(q)
    nres = query.rowcount

    if config['maxresults'] == 0:
        config['maxresults'] = nres
    if nres > config['maxresults']:
        nres = config['maxresults']
    if config['perpage'] == 0 or q['page'] == 0:
        config['perpage'] = nres
        q['page'] = 1
    offset = (q['page'] - 1) * config['perpage']

    if query.rowcount > 0:
        if type(query.next) == int:
            query.next = offset
        else:
            query.scroll(offset, mode='absolute')

    for i in range(config['perpage']):
        try:
            doc = query.fetchone()
        except:
            break
        d = {}
        for f in FIELDS:
            v = getattr(doc, f)
            if v is not None:
                d[f] = v.encode('utf-8')
            else:
                d[f] = ''
        d['label'] = select([d['title'], d['filename'], '?'], [None, ''])
        d['sha'] = hashlib.sha1(d['url']+d['ipath']).hexdigest()
        d['time'] = timestr(d['mtime'], config['timefmt'])
        d['snippet'] = query.makedocabstract(doc).encode('utf-8')
        results.append(d)
    tend = datetime.datetime.now()
    return results, nres, tend - tstart
#}}}
#}}}
#{{{ routes
#{{{ static
@bottle.route('/static/:path#.+#')
def server_static(path):
    return bottle.static_file(path, root='./static')
#}}}
#{{{ main
@bottle.route('/')
@bottle.view('main')
def main():
    config = get_config()
    return { 'dirs': get_dirs(config['dirs'], config['dirdepth']),
            'query': get_query(), 'sorts': SORTS }
#}}}
#{{{ results
@bottle.route('/results')
@bottle.view('results')
def results():
    config = get_config()
    query = get_query()
    qs = query_to_recoll_string(query)
    res, nres, timer = recoll_search(query)
    if config['maxresults'] == 0:
        config['maxresults'] = nres
    if config['perpage'] == 0:
        config['perpage'] = nres
    return { 'res': res, 'time': timer, 'query': query, 'dirs':
            get_dirs(config['dirs'], config['dirdepth']),
             'qs': qs, 'sorts': SORTS, 'config': config,
            'query_string': bottle.request.query_string, 'nres': nres,
             'hasrclextract': hasrclextract }
#}}}
#{{{ preview
@bottle.route('/preview/<resnum:int>')
def preview(resnum):
    if not hasrclextract:
        return 'Sorry, needs recoll version 1.19 or later'
    query = get_query()
    qs = query_to_recoll_string(query)
    rclq = recoll_initsearch(query)
    if resnum > rclq.rowcount - 1:
        return 'Bad result index %d' % resnum
    rclq.scroll(resnum)
    doc = rclq.fetchone()
    xt = rclextract.Extractor(doc)
    tdoc = xt.textextract(doc.ipath)
    if tdoc.mimetype == 'text/html':
        bottle.response.content_type = 'text/html; charset=utf-8'
    else:
        bottle.response.content_type = 'text/plain; charset=utf-8'
    return tdoc.text
#}}}
#{{{ edit
@bottle.route('/edit/<resnum:int>')
def edit(resnum):
    if not hasrclextract:
        return 'Sorry, needs recoll version 1.19 or later'
    query = get_query()
    qs = query_to_recoll_string(query)
    rclq = recoll_initsearch(query)
    if resnum > rclq.rowcount - 1:
        return 'Bad result index %d' % resnum
    rclq.scroll(resnum)
    doc = rclq.fetchone()
    bottle.response.content_type = doc.mimetype
    bottle.response.headers['Content-Disposition'] = \
        'attachment; filename=%s' % doc.filename
    # If ipath is null, we can just return the file
    pathismine = False
    if doc.ipath == '':
        path = doc.url.replace('file://','')
    else:
        xt = rclextract.Extractor(doc)
        path = xt.idoctofile(doc.ipath, doc.mimetype)
        pathismine = True
    print >> sys.stderr, "Sending %s with mimetype %s" % (path, doc.mimetype)
    f = open(path, 'r')
    if pathismine:
        os.unlink(path)
    return f
#}}}
#{{{ json
@bottle.route('/json')
def get_json():
    query = get_query()
    query['page'] = 0
    qs = query_to_recoll_string(query)
    bottle.response.headers['Content-Type'] = 'application/json'
    bottle.response.headers['Content-Disposition'] = 'attachment; filename=recoll-%s.json' % normalise_filename(qs)
    res, nres, timer = recoll_search(query)

    return json.dumps({ 'query': query, 'results': res })
#}}}
#{{{ csv
@bottle.route('/csv')
def get_csv():
    query = get_query()
    query['page'] = 0
    qs = query_to_recoll_string(query)
    bottle.response.headers['Content-Type'] = 'text/csv'
    bottle.response.headers['Content-Disposition'] = 'attachment; filename=recoll-%s.csv' % normalise_filename(qs)
    res, nres, timer = recoll_search(query)
    si = StringIO.StringIO()
    cw = csv.writer(si)
    cw.writerow(FIELDS)
    for doc in res:
        row = []
        for f in FIELDS:
            row.append(doc[f])
        cw.writerow(row)
    return si.getvalue().strip("\r\n")
#}}}
#{{{ settings/set
@bottle.route('/settings')
@bottle.view('settings')
def settings():
    return get_config()

@bottle.route('/set')
def set():
    config = get_config()
    for k, v in DEFAULTS.items():
        bottle.response.set_cookie(k, str(bottle.request.query.get(k)), max_age=3153600000)
    for d in config['dirs']:
        cookie_name = 'mount_%s' % urllib.quote(d, '')
        bottle.response.set_cookie(cookie_name, str(bottle.request.query.get('mount_%s' % d)), max_age=3153600000)
    bottle.redirect('./')
#}}}
#{{{ osd
@bottle.route('/osd.xml')
@bottle.view('osd')
def main():
    #config = get_config()
    url = bottle.request.urlparts
    url = '%s://%s' % (url.scheme, url.netloc)
    return {'url': url}
#}}}
# vim: fdm=marker:tw=80:ts=4:sw=4:sts=4:et
