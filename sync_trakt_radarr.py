#!/usr/bin/env python3
import json, os, sys, time
from urllib.parse import quote
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get('TRAKT_RADARR_CONFIG', os.path.join(BASE_DIR, 'config.json'))
TOKEN_FILE = os.path.join(BASE_DIR, 'trakt_tokens.json')
REQUEST_TIMEOUT = 60
session = requests.Session()

def load_config():
    try:
        with open(CONFIG_FILE, encoding='utf-8') as f: return json.load(f)
    except Exception as e:
        print(f'ERROR loading {CONFIG_FILE}: {e}'); sys.exit(1)

C = load_config()
T = C['trakt']; R = C['radarr']; S = C.get('sync', {})
CLIENT_ID=T['client_id']; CLIENT_SECRET=T['client_secret']; USERNAME=T['username']; LIST_SLUG=T.get('list_slug','my_watchlist'); LIST_NAME=T.get('list_name','My_Watchlist')
RADARR_URL=R.get('url','http://127.0.0.1:7878').rstrip('/'); API_KEY=R['api_key']; QUALITY=R.get('quality_profile','Drewy Quality'); ROOT=R.get('root_folder','/mnt/movies')
TAG=S.get('managed_tag','trakt-my-watchlist'); SEARCH=S.get('search_on_add',True); MONITORED=S.get('monitored',True); AVAIL=S.get('minimum_availability','announced'); DELETE_REMOVED=S.get('delete_removed_movies',True); DELETE_FILES=S.get('delete_files',True); EXCLUDE=S.get('add_import_exclusion',False); PAGE_SIZE=min(int(S.get('trakt_page_size',250)),250)

def save_tokens(d):
    x={k:d[k] for k in ('access_token','refresh_token')}; x['created_at']=d.get('created_at',int(time.time())); x['expires_in']=d.get('expires_in',604800)
    tmp=TOKEN_FILE+'.tmp'; open(tmp,'w').write(json.dumps(x,indent=2)); os.chmod(tmp,0o600); os.replace(tmp,TOKEN_FILE)
def load_tokens():
    try: return json.load(open(TOKEN_FILE))
    except Exception: return None
def expired(t): return time.time() >= t.get('created_at',0)+t.get('expires_in',604800)-600

def refresh(rt):
    print('Refreshing Trakt authorization...')
    r=session.post('https://auth.trakt.tv/oauth/token',json={'refresh_token':rt,'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'redirect_uri':'urn:ietf:wg:oauth:2.0:oob','grant_type':'refresh_token'},timeout=REQUEST_TIMEOUT)
    if r.status_code!=200: return None
    d=r.json(); save_tokens(d); return d['access_token']
def authorize():
    r=session.post('https://api.trakt.tv/oauth/device/code',json={'client_id':CLIENT_ID},timeout=REQUEST_TIMEOUT); r.raise_for_status(); d=r.json()
    print(f"Open {d.get('verification_url','https://trakt.tv/activate')} and enter code: {d['user_code']}")
    start=time.time(); interval=d['interval']
    while time.time()-start < d['expires_in']:
        time.sleep(interval)
        r=session.post('https://api.trakt.tv/oauth/device/token',json={'code':d['device_code'],'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET},timeout=REQUEST_TIMEOUT)
        if r.status_code==200:
            x=r.json(); save_tokens(x); print('Trakt authorization successful.'); return x['access_token']
        if r.status_code in (400,404): continue
        if r.status_code==418: interval+=5; continue
        if r.status_code==429: time.sleep(int(r.headers.get('Retry-After',interval))); continue
        r.raise_for_status()
    raise RuntimeError('Trakt authorization timed out')
def access_token():
    t=load_tokens()
    if not t: return authorize()
    if expired(t): return refresh(t.get('refresh_token')) or authorize()
    return t['access_token']
def th(tok): return {'trakt-api-version':'2','trakt-api-key':CLIENT_ID,'Authorization':f'Bearer {tok}','User-Agent':'Trakt-Radarr-Sync/1.0'}
def rh(): return {'X-Api-Key':API_KEY,'Content-Type':'application/json'}
def rr(method,path,**kw): return session.request(method,RADARR_URL+path,headers=rh(),timeout=REQUEST_TIMEOUT,**kw)

def get_trakt(tok):
    out=[]; page=1; url=f"https://api.trakt.tv/users/{quote(USERNAME,safe='')}/lists/{quote(LIST_SLUG,safe='')}/items/movies"
    print(f'Reading Trakt list: {LIST_NAME}')
    while True:
        r=session.get(url,headers=th(tok),params={'page':page,'limit':PAGE_SIZE},timeout=REQUEST_TIMEOUT)
        if r.status_code==401:
            t=load_tokens(); tok=refresh(t.get('refresh_token')) if t else None; tok=tok or authorize(); continue
        r.raise_for_status(); items=r.json(); print(f'  Page {page}: {len(items)} movies')
        if not items: break
        out.extend(items); pc=r.headers.get('X-Pagination-Page-Count')
        if (pc and page>=int(pc)) or (not pc and len(items)<PAGE_SIZE): break
        page+=1
    return out

def profile_id():
    r=rr('GET','/api/v3/qualityprofile'); r.raise_for_status()
    for p in r.json():
        if p.get('name','').lower()==QUALITY.lower(): return p['id']
    raise RuntimeError(f'Quality profile not found: {QUALITY}')
def tag_id():
    r=rr('GET','/api/v3/tag'); r.raise_for_status()
    for t in r.json():
        if t.get('label','').lower()==TAG.lower(): return t['id']
    r=rr('POST','/api/v3/tag',json={'label':TAG}); r.raise_for_status(); return r.json()['id']
def movies():
    r=rr('GET','/api/v3/movie'); r.raise_for_status(); return r.json()
def tag_movie(m,tid):
    if tid in m.get('tags',[]): return False
    x=m.copy(); x['tags']=m.get('tags',[])+[tid]; r=rr('PUT',f"/api/v3/movie/{m['id']}",json=x); r.raise_for_status(); return True
def add_movie(tmdb,pid,tid):
    r=rr('GET','/api/v3/movie/lookup/tmdb',params={'tmdbId':tmdb}); r.raise_for_status(); x=r.json(); x.pop('id',None)
    x.update({'qualityProfileId':pid,'rootFolderPath':ROOT,'monitored':MONITORED,'minimumAvailability':AVAIL,'tags':[tid],'addOptions':{'searchForMovie':SEARCH}})
    r=rr('POST','/api/v3/movie',json=x); r.raise_for_status()
def delete_movie(m):
    r=rr('DELETE',f"/api/v3/movie/{m['id']}",params={'deleteFiles':str(DELETE_FILES).lower(),'addImportExclusion':str(EXCLUDE).lower()}); r.raise_for_status()

def main():
    print('='*60); print('Trakt -> Radarr Managed Sync'); print('='*60)
    tok=access_token(); st=rr('GET','/api/v3/system/status'); st.raise_for_status(); print('Radarr:',st.json().get('version'))
    pid=profile_id(); tid=tag_id(); trakt=get_trakt(tok)
    tmap={i['movie']['ids']['tmdb']:i['movie'] for i in trakt if i.get('movie',{}).get('ids',{}).get('tmdb')}
    rmovies=movies(); rmap={m['tmdbId']:m for m in rmovies if m.get('tmdbId')}
    for tmdb in set(tmap)&set(rmap):
        if tag_movie(rmap[tmdb],tid): print('TAGGED:',rmap[tmdb].get('title'))
    for tmdb in sorted(set(tmap)-set(rmap)):
        m=tmap[tmdb]; print('ADDING:',m.get('title'),m.get('year'))
        try: add_movie(tmdb,pid,tid); print('  ADDED + SEARCH TRIGGERED' if SEARCH else '  ADDED')
        except Exception as e: print('  FAILED:',e)
        time.sleep(.25)
    if DELETE_REMOVED:
        for m in movies():
            if tid in m.get('tags',[]) and m.get('tmdbId') not in tmap:
                print('REMOVING:',m.get('title'),m.get('year'))
                try: delete_movie(m); print('  DELETED FROM RADARR + FILES' if DELETE_FILES else '  REMOVED FROM RADARR')
                except Exception as e: print('  FAILED:',e)
                time.sleep(.25)
    print('Sync complete.')
if __name__=='__main__':
    try: main()
    except Exception as e: print('ERROR:',e); sys.exit(1)
