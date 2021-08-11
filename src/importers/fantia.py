import sys
sys.setrecursionlimit(100000)

import requests
import config
import json
import datetime
from urllib.parse import urljoin
from os.path import join
from bs4 import BeautifulSoup

from ..internals.database.database import get_conn, get_raw_conn, return_conn
from ..internals.utils.logger import log
from ..lib.artist import index_artists, is_artist_dnp, update_artist, delete_artist_cache_keys
from ..lib.post import post_flagged, post_exists, delete_post_flags, move_to_backup, delete_backup, restore_from_backup
from ..internals.utils.download import download_file, DownloaderException
from ..internals.utils.scrapper import create_scrapper_session
from ..internals.utils.proxy import get_proxy

# In the future, if the timeline API proves itself to be unreliable, we should probably move to scanning fanclubs individually.
# https://fantia.jp/api/v1/me/fanclubs',

def enable_adult_mode(import_id, jar):
    # log(import_id, f"No active Fantia subscriptions or invalid key. No posts will be imported.", to_client = True)
    scraper = create_scrapper_session(useCloudscraper=False).get(
        'https://fantia.jp/mypage/account/edit',
        cookies=jar,
        proxies=get_proxy()
    )
    scraper_data = scraper.text
    scraper.raise_for_status()
    soup = BeautifulSoup(scraper_data, 'html.parser')

    if (soup.select_one('.edit_user input#user_rating') is None):
        log(import_id, f"Error while enabling adult mode; key is probably invalid")
        
    if (soup.select_one('.edit_user input#user_rating').get('checked') is None):
        authenticity_token = soup.select_one('.edit_user input[name=authenticity_token]')['value']
        create_scrapper_session(useCloudscraper=False).post(
            'https://fantia.jp/mypage/users/update_rating',
            cookies=jar,
            proxies=get_proxy(),
            data={
                "utf8": '✓',
                "authenticity_token": authenticity_token,
                "user[rating]": 'adult',
                "commit": '変更を保存'
            }
        ).raise_for_status()
        return True
    return False
    
def disable_adult_mode(import_id, jar):
    scraper = create_scrapper_session(useCloudscraper=False).get(
        'https://fantia.jp/mypage/account/edit',
        cookies=jar,
        proxies=get_proxy()
    )
    scraper_data = scraper.text
    scraper.raise_for_status()
    soup = BeautifulSoup(scraper_data, 'html.parser')
    authenticity_token = soup.select_one('.edit_user input[name=authenticity_token]')['value']
    create_scrapper_session(useCloudscraper=False).post(
        'https://fantia.jp/mypage/users/update_rating',
        cookies=jar,
        proxies=get_proxy(),
        data={
            "utf8": '✓',
            "authenticity_token": authenticity_token,
            "user[rating]": 'general',
            "commit": '変更を保存'
        }
    ).raise_for_status()

def import_fanclub(fanclub_id, import_id, jar, page = 1):
    try:
        scraper = create_scrapper_session(useCloudscraper=False).get(
            f"https://fantia.jp/fanclubs/{fanclub_id}/posts?page={page}",
            cookies=jar,
            proxies=get_proxy()
        )
        scraper_data = scraper.text
        scraper.raise_for_status()
    except requests.HTTPError as exc:
        log(import_id, f'Status code {exc.response.status_code} when contacting Fantia API.', 'exception')
        return
    
    scraped_posts = BeautifulSoup(scraper_data, 'html.parser').select('div.post')
    user_id = None
    for post in scraped_posts:
        backup_path = None
        try:
            user_id = fanclub_id
            post_id = post.select_one('a.link-block')['href'].lstrip('/posts/')
            file_directory = f"files/fantia/{user_id}/{post_id}"
            attachments_directory = f"attachments/fantia/{user_id}/{post_id}"

            if is_artist_dnp('fantia', user_id):
                log(import_id, f"Skipping user {user_id} because they are in do not post list", to_client = True)
                return     

            if post_exists('fantia', user_id, post_id) and not post_flagged('fantia', user_id, post_id):
                log(import_id, f'Skipping post {post_id} from user {user_id} because already exists', to_client = True)
                continue

            if post_flagged('fantia', user_id, post_id):
                backup_path = move_to_backup('fantia', user_id, post_id)

            try:
                post_scraper = create_scrapper_session(useCloudscraper=False).get(
                    f"https://fantia.jp/api/v1/posts/{post_id}",
                    cookies=jar,
                    proxies=get_proxy()
                )
                post_data = post_scraper.json()
                post_scraper.raise_for_status()
            except requests.HTTPError as exc:
                log(import_id, f'Status code {exc.response.status_code} when contacting Fantia API.', 'exception')
                continue
            
            post_model = {
                'id': post_id,
                '"user"': user_id,
                'service': 'fantia',
                'title': post_data['post']['title'],
                'content': post_data['post']['comment'] or '',
                'embed': {},
                'shared_file': False,
                'added': datetime.datetime.now(),
                'published': post_data['post']['posted_at'],
                'file': {},
                'attachments': []
            }

            paid_contents = []
            for content in post_data['post']['post_contents']:
                if content['plan'] and content['plan']['price'] > 0 and content['visible_status'] == 'visible':
                    paid_contents.append(content)
            if (len(paid_contents) == 0):
                log(import_id, f'Skipping post {post_id} from user {user_id} because no paid contents are unlocked', to_client = True)
                continue
                
            log(import_id, f"Starting import: {post_id} from user {user_id}")

            if post_data['post']['thumb']:
                filename, _ = download_file(
                    join(config.download_path, file_directory),
                    post_data['post']['thumb']['original']
                )
                post_model['file']['name'] = filename
                post_model['file']['path'] = f'/{file_directory}/{filename}'

            for content in post_data['post']['post_contents']:
                if (content['visible_status'] != 'visible'):
                    continue
                if content['category'] == 'photo_gallery':
                    for photo in content['post_content_photos']:
                        filename, _ = download_file(
                            join(config.download_path, attachments_directory),
                            photo['url']['original'],
                            cookies=jar
                        )
                        post_model['attachments'].append({
                            'name': filename,
                            'path': f'/{attachments_directory}/{filename}'
                        })
                elif content['category'] == 'file':
                    filename, _ = download_file(
                        join(config.download_path, attachments_directory),
                        urljoin('https://fantia.jp/posts', content['download_uri']),
                        name = content['filename'],
                        cookies=jar
                    )
                    post_model['attachments'].append({
                        'name': content['filename'],
                        'path': f'/{attachments_directory}/{filename}'
                    })
                elif content['category'] == 'embed':
                    post_model['content'] += f"""
                        <a href="{content['embed_url']}" target="_blank">
                            <div class="embed-view">
                              <h3 class="subtitle">(Embed)</h3>
                            </div>
                        </a>
                        <br>
                    """
                elif content['category'] == 'blog':
                    for op in json.loads(content['comment'])['ops']:
                        if type(op['insert']) is dict and op['insert'].get('fantiaImage'):
                            filename, _ = download_file(
                                join(config.download_path, attachments_directory),
                                urljoin('https://fantia.jp/', op['insert']['fantiaImage']['original_url']),
                                cookies=jar
                            )
                            post_model['attachments'].append({
                                'name': filename,
                                'path': f'/{attachments_directory}/{filename}'
                            })
                else:
                    log(import_id, f'Skipping content {content["id"]} from post {post_id}; unsupported type "{content["category"]}"', to_client = True)
                    log(import_id, json.dumps(content), to_client=False)
            
            post_model['embed'] = json.dumps(post_model['embed'])
            post_model['file'] = json.dumps(post_model['file'])
            for i in range(len(post_model['attachments'])):
                post_model['attachments'][i] = json.dumps(post_model['attachments'][i])

            columns = post_model.keys()
            data = ['%s'] * len(post_model.values())
            data[-1] = '%s::jsonb[]' # attachments
            query = "INSERT INTO posts ({fields}) VALUES ({values}) ON CONFLICT (id, service) DO UPDATE SET {updates}".format(
                fields = ','.join(columns),
                values = ','.join(data),
                updates = ','.join([f'{column}=EXCLUDED.{column}' for column in columns])
            )
            
            conn = get_raw_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(query, list(post_model.values()))
                conn.commit()
            finally:
                return_conn(conn)

            update_artist('fantia', user_id)
            delete_post_flags('fantia', user_id, post_id)
            
            if (config.ban_url):
                requests.request('BAN', f"{config.ban_url}/{post_model['service']}/user/" + post_model['"user"'])
            delete_artist_cache_keys('fantia', user_id)

            if backup_path is not None:
                delete_backup(backup_path)
            log(import_id, f"Finished importing {post_id} from user {user_id}", to_client=False)
        except Exception:
            log(import_id, f'Error importing post {post_id} from user {user_id}', 'exception')

            if backup_path is not None:
                restore_from_backup('fantia', user_id, post_id, backup_path)
            continue
    
    if (scraped_posts):
        log(import_id, f'Finished processing page. Processing next page.')
        import_fanclub(fanclub_id, import_id, jar, page = page + 1)

def get_paid_fanclubs(import_id, jar):
    scraper = create_scrapper_session(useCloudscraper=False).get(
        'https://fantia.jp/mypage/users/plans?type=not_free',
        cookies=jar,
        proxies=get_proxy()
    )
    scraper_data = scraper.text
    scraper.raise_for_status()
    soup = BeautifulSoup(scraper_data, 'html.parser')
    return set(fanclub_link["href"].lstrip("/fanclubs/") for fanclub_link in soup.select("div.mb-5-children > div:nth-of-type(1) a[href^=\"/fanclubs\"]"))

def import_posts(import_id, key):
    jar = requests.cookies.RequestsCookieJar()
    jar.set('_session_id', key)
    
    mode_switched = enable_adult_mode(import_id, jar)
    fanclub_ids = get_paid_fanclubs(import_id, jar)
    if len(fanclub_ids) > 0:
        for fanclub_id in fanclub_ids:
            log(import_id, f'Importing fanclub {fanclub_id}', to_client=True)
            import_fanclub(fanclub_id, import_id, jar)
    else:
        log(import_id, f"No paid subscriptions found. No posts will be imported.", to_client = True)
    
    if (mode_switched):
        disable_adult_mode(import_id, jar)

    log(import_id, f"Finished scanning for posts.")
    index_artists()

if __name__ == '__main__':
    if len(sys.argv) > 1:
        import_posts(str(uuid.uuid4()), sys.argv[1])
    else:
        print('Argument required - Login token')
